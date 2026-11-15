#!/usr/bin/env python3
"""
accounts.py - Claude Code and Codex account profiles, live usage, and switching.

Contract: docs/V2-CONTRACTS.md, section `GET /api/accounts`.

Profiles live in data/accounts.json. The default profile of each tool is the
Mac-wide login itself (Keychain `Claude Code-credentials` + ~/.claude.json,
~/.codex/auth.json). Extra profiles are ~/.claude-profiles/<label>
(CLAUDE_CONFIG_DIR) and ~/.codex-profiles/<label> (CODEX_HOME).

Tokens never leave this module: they are read from their credential store, sent
only as an Authorization header to api.anthropic.com or chatgpt.com, and written only to a
credential location (Keychain, a profile's .credentials.json / auth.json, or a
mode-600 backup under data/account-backups/). Nothing here prints or returns one.

CLI (testing):
    python3 accounts.py                     refresh and print a summary table
    python3 accounts.py add claude personal create a profile, print the login command
    python3 accounts.py remove claude-personal
    python3 accounts.py switch claude claude-personal
"""
import base64
import calendar
import datetime
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ACCOUNTS_FILE = os.path.join(DATA, "accounts.json")
CONFIG_FILE = os.path.join(DATA, "config.json")
BACKUPS = os.path.join(DATA, "account-backups")
HOME = os.path.expanduser("~")
CLAUDE_JSON = os.path.join(HOME, ".claude.json")
CODEX_HOME = os.path.join(HOME, ".codex")
KC_SERVICE = "Claude Code-credentials"
API = "https://api.anthropic.com/api/oauth/"
UA = "claude-code/2.1.282"
SWITCH_COOLDOWN_S = 600

LOCK = threading.RLock()
_last_bg = 0.0

try:
    import clis
except Exception:  # pragma: no cover
    clis = None


# ------------------------------------------------------------------ io
def _jread(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _jwrite(path, obj, mode=None):
    """Atomic JSON write; mode 0o600 for anything holding a credential."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode or 0o644)
    with os.fdopen(fd, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    if mode:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _config():
    return _jread(CONFIG_FILE, {})


def _auto_cfg():
    a = dict({"enabled": True, "threshold": 90, "mode": "global"}, **(_config().get("auto_switch") or {}))
    return a


def _base_env(**extra):
    if clis:
        return clis.env(**extra)
    return dict(os.environ, **extra)


def _cli(name):
    return clis.cli(name) if clis else name


# ------------------------------------------------------------------ registry
def _defaults():
    return [
        {"id": "claude-default", "tool": "claude", "label": "Default", "config_dir": os.path.join(HOME, ".claude")},
        {"id": "codex-default", "tool": "codex", "label": "Default", "config_dir": CODEX_HOME},
    ]


def load():
    d = _jread(ACCOUNTS_FILE, {})
    d.setdefault("accounts", [])
    d.setdefault("pending_accounts", [])
    have = {a["id"] for a in d["accounts"]}
    missing = [a for a in _defaults() if a["id"] not in have]
    d["accounts"] = missing + d["accounts"]
    st = d.setdefault("state", {})
    st.setdefault("active", {"claude": "claude-default", "codex": "codex-default"})
    st.setdefault("global_active", dict(st["active"]))
    st.setdefault("last_switch", None)
    d.setdefault("cache", {})
    return d


def save(d):
    _jwrite(ACCOUNTS_FILE, d)


def _acct(d, aid):
    for a in d["accounts"] + d.get("pending_accounts", []):
        if a["id"] == aid:
            return a
    return None


def _is_default(a):
    return a["id"].endswith("-default")


# ------------------------------------------------------------------ keychain
def _kc_read(service):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return None


def _kc_account(service):
    """The `acct` attribute of an existing item (metadata only, no secret)."""
    r = subprocess.run(["security", "find-generic-password", "-s", service],
                       capture_output=True, text=True, timeout=15)
    m = re.search(r'"acct"<blob>="([^"]*)"', r.stdout)
    return m.group(1) if m else None


def _kc_write(service, blob):
    """Update the item through `security -i` stdin with hex data, so the secret
    never appears in a process argument list."""
    acct = _kc_account(service) or os.environ.get("USER") or "user"
    hexdata = json.dumps(blob, separators=(",", ":")).encode().hex()
    cmd = f'add-generic-password -U -s "{service}" -a "{acct}" -X {hexdata}\n'
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0 or "error" in (r.stderr or "").lower():
        raise RuntimeError("keychain write failed")


def _kc_services():
    try:
        r = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True, timeout=30)
    except Exception:
        return []
    names = set(re.findall(r'"svce"<blob>="([^"]*)"', r.stdout))
    return sorted(n for n in names if n.startswith(KC_SERVICE) and n != KC_SERVICE)


# ------------------------------------------------------------------ claude creds
def _claude_json(path=CLAUDE_JSON):
    return _jread(path, {})


def _profile_oauth_account(a):
    """oauthAccount block for a profile: snapshot account.json, else its .claude.json."""
    d = a["config_dir"]
    acc = _jread(os.path.join(d, "account.json"), None)
    if acc:
        return acc
    return (_jread(os.path.join(d, ".claude.json"), {}) or {}).get("oauthAccount")


def _api_get(endpoint, token):
    req = urllib.request.Request(API + endpoint, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _detect_profile_service(d, a):
    """Find the Keychain item holding a non-default Claude profile's login.
    Order: cached mapping, the predicted name (sha256 of the config dir, first 8
    hex), then every `Claude Code-credentials*` service. A candidate counts only
    when /api/oauth/profile returns the email recorded in the profile's
    .claude.json. Unverified naming: see the module report."""
    expect = ((_profile_oauth_account(a) or {}).get("emailAddress") or "").lower()
    cands = []
    if a.get("keychain_service"):
        cands.append(a["keychain_service"])
    cands.append(f"{KC_SERVICE}-{hashlib.sha256(a['config_dir'].encode()).hexdigest()[:8]}")
    taken = {x.get("keychain_service") for x in d["accounts"] if x["id"] != a["id"]}
    for s in _kc_services():
        if s not in cands and s not in taken:
            cands.append(s)
    for s in cands:
        blob = _kc_read(s)
        tok = ((blob or {}).get("claudeAiOauth") or {}).get("accessToken")
        if not tok:
            continue
        if not expect:
            continue
        try:
            email = (_api_get("profile", tok).get("account") or {}).get("email", "").lower()
        except Exception:
            continue
        if email == expect:
            a["keychain_service"] = s
            return s, blob
    return None, None


def _claude_creds(d, a):
    """(blob, source) for a Claude account, or (None, reason)."""
    st = d["state"]
    if st["global_active"].get("claude") == a["id"]:
        return _kc_read(KC_SERVICE), "keychain:" + KC_SERVICE
    if _is_default(a):
        stash = a.get("stash")
        blob = _jread(os.path.join(stash, "claude-credentials.json"), None) if stash else None
        return blob, ("stash" if blob else "default login is stashed nowhere")
    p = os.path.join(a["config_dir"], ".credentials.json")
    blob = _jread(p, None)
    if blob and (blob.get("claudeAiOauth") or {}).get("accessToken"):
        return blob, "file"
    svc, blob = _detect_profile_service(d, a)
    if blob:
        return blob, "keychain:" + svc
    return None, "not logged in"


def _claude_refresh_via_cli(d, a):
    """Run `claude auth status --json` as that profile once; Claude Code refreshes
    an expired token while answering. Output is discarded (may name the account)."""
    try:
        subprocess.run([_cli("claude"), "auth", "status", "--json"], env=env_for("claude", a["id"], d),
                       capture_output=True, text=True, timeout=60)
    except Exception:
        pass


PLAN_TYPES = {"claude_team": "Team", "claude_enterprise": "Enterprise", "claude_max": "Max",
              "claude_pro": "Pro", "claude_free": "Free"}


def claude_plan(org_type, tier):
    base = PLAN_TYPES.get(org_type or "", (org_type or "").replace("claude_", "").replace("_", " ").title())
    m = re.search(r"max_(\d+)x", tier or "")
    t = f"Max {m.group(1)}x" if m else ""
    if base and t and base != "Max":
        return f"{base} ({t})"
    return t or base or ""


def _claude_windows(u):
    out = []
    for lim in u.get("limits") or []:
        kind, pct, reset = lim.get("kind"), lim.get("percent"), lim.get("resets_at")
        if pct is None:
            continue
        if kind == "session":
            out.append({"kind": "5h", "label": "Session (5h)", "percent": pct, "resets_at": reset})
        elif kind == "weekly_all":
            out.append({"kind": "7d", "label": "Weekly", "percent": pct, "resets_at": reset})
        elif kind == "weekly_scoped":
            model = (((lim.get("scope") or {}).get("model") or {}).get("display_name")) or "scoped"
            out.append({"kind": "7d_" + model.lower(), "label": "Weekly " + model,
                        "percent": pct, "resets_at": reset})
        else:
            out.append({"kind": kind, "label": str(kind).replace("_", " ").title(),
                        "percent": pct, "resets_at": reset})
    if out:
        return out
    # older payloads and the cachedUsageUtilization fallback
    for key, kind, label in (("five_hour", "5h", "Session (5h)"), ("seven_day", "7d", "Weekly"),
                             ("seven_day_opus", "7d_opus", "Weekly Opus"),
                             ("seven_day_sonnet", "7d_sonnet", "Weekly Sonnet")):
        w = u.get(key)
        if isinstance(w, dict) and w.get("utilization") is not None:
            out.append({"kind": kind, "label": label, "percent": round(w["utilization"]),
                        "resets_at": w.get("resets_at")})
    return out


def _claude_extra(u):
    e = u.get("extra_usage")
    if not isinstance(e, dict):
        return None
    scale = 10 ** int(e.get("decimal_places") or 0)
    used, limit = e.get("used_credits"), e.get("monthly_limit")
    return {"enabled": bool(e.get("is_enabled")),
            "used": round(used / scale, 2) if used is not None else None,
            "limit": round(limit / scale, 2) if limit is not None else None,
            "currency": e.get("currency"),
            "percent": round(e["utilization"], 1) if e.get("utilization") is not None else None}


def _fetch_claude(d, a):
    rec = {"logged_in": False, "error": "", "windows": [], "extra": None, "credits": None,
           "email": "", "org": "", "plan": ""}
    acc = _claude_json().get("oauthAccount") if d["state"]["global_active"].get("claude") == a["id"] \
        else _profile_oauth_account(a)
    if _is_default(a) and d["state"]["global_active"].get("claude") != a["id"] and a.get("stash"):
        acc = _jread(os.path.join(a["stash"], "claude-oauthAccount.json"), None)
    if acc:
        rec["email"] = acc.get("emailAddress", "")
        rec["org"] = acc.get("organizationName", "")
        rec["plan"] = claude_plan(acc.get("organizationType"), acc.get("organizationRateLimitTier")
                                  or acc.get("userRateLimitTier"))
    blob, src = _claude_creds(d, a)
    tok = ((blob or {}).get("claudeAiOauth") or {}).get("accessToken")
    if not tok:
        rec["error"] = src if isinstance(src, str) else "no credentials"
        return _claude_cached_fallback(a, rec)
    rec["logged_in"] = True
    for attempt in (0, 1):
        try:
            u = _api_get("usage", tok)
            p = _api_get("profile", tok)
            org = p.get("organization") or {}
            rec["email"] = (p.get("account") or {}).get("email") or rec["email"]
            rec["org"] = org.get("name") or rec["org"]
            rec["plan"] = claude_plan(org.get("organization_type"), org.get("rate_limit_tier")) or rec["plan"]
            rec["windows"] = _claude_windows(u)
            rec["extra"] = _claude_extra(u)
            return rec
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                _claude_refresh_via_cli(d, a)
                blob, src = _claude_creds(d, a)
                tok = ((blob or {}).get("claudeAiOauth") or {}).get("accessToken")
                if tok:
                    continue
            rec["error"] = f"usage endpoint HTTP {e.code}"
            if e.code == 401: rec["logged_in"] = False
            break
        except Exception as e:
            rec["error"] = f"usage endpoint: {type(e).__name__}"
            break
    return _claude_cached_fallback(a, rec)


def _claude_cached_fallback(a, rec):
    """Use cachedUsageUtilization from the profile's .claude.json (what the CLI last saw)."""
    path = CLAUDE_JSON if _is_default(a) else os.path.join(a["config_dir"], ".claude.json")
    c = (_claude_json(path) or {}).get("cachedUsageUtilization") or {}
    if c.get("utilization"):
        rec["windows"] = _claude_windows(c["utilization"])
        rec["cached_at"] = int((c.get("fetchedAtMs") or 0) / 1000) or None
        rec["error"] = (rec["error"] + "; " if rec["error"] else "") + "showing cached usage"
    return rec


# ------------------------------------------------------------------ codex
def _codex_home(d, a):
    if d["state"]["global_active"].get("codex") == a["id"]:
        return CODEX_HOME
    return a["config_dir"]


def _codex_auth(d, a):
    if d["state"]["global_active"].get("codex") == a["id"]:
        return _jread(os.path.join(CODEX_HOME, "auth.json"), None)
    if _is_default(a):
        return _jread(os.path.join(a["stash"], "codex-auth.json"), None) if a.get("stash") else None
    return _jread(os.path.join(a["config_dir"], "auth.json"), None)


def _jwt_claims(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def _newest_rate_limits(home):
    files = sorted(glob.glob(os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")),
                   key=lambda f: os.path.getmtime(f), reverse=True)
    for f in files[:20]:
        last = None
        observed = None
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                        rl = (event.get("payload") or {}).get("rate_limits")
                    except Exception:
                        continue
                    if isinstance(rl, dict):
                        last = rl
                        try:
                            parsed = datetime.datetime.fromisoformat(event.get('timestamp', '').replace('Z', '+00:00'))
                            observed = parsed.timestamp() if parsed.tzinfo else None
                        except (TypeError, ValueError, AttributeError):
                            observed = None
        except Exception:
            continue
        if last is not None:
            return last, observed
    return None, None


def _codex_window(w, fallback_kind, ts):
    if not isinstance(w, dict) or w.get("used_percent") is None:
        return None
    mins = w.get("window_minutes")
    kind = {300: "5h", 10080: "7d"}.get(mins, fallback_kind)
    label = {"5h": "Session (5h)", "7d": "Weekly"}[kind]
    reset = w.get("resets_at")
    if reset is None and w.get("resets_in_seconds") is not None and ts:
        reset = ts + w["resets_in_seconds"]
    if isinstance(reset, (int, float)):
        reset = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset))
    return {"kind": kind, "label": label, "percent": round(w["used_percent"]), "resets_at": reset}


def _codex_live_usage(token, account_id):
    """Read subscription usage with the same account binding as the native client."""
    req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers={
        "Authorization": "Bearer " + token, "ChatGPT-Account-Id": account_id,
        "User-Agent": "codex_cli_rs"})
    with urllib.request.urlopen(req, timeout=15) as response:
        usage = json.load(response)
    if usage.get("account_id") != account_id:
        raise ValueError("usage account identity mismatch")
    return usage


def _apply_codex_live(rec, usage):
    rec["quota_source"] = "account API"
    rec["quota_attributed"] = True
    if usage.get("plan_type"):
        rec["plan"] = str(usage["plan_type"]).replace("_", " ").title()
    # A provider may introduce separately metered models. Until those scopes can
    # be mapped exactly, never treat a partial general window as spare quota.
    if usage.get("additional_rate_limits"):
        rec["error"] = "Additional model-specific limits reported; automatic quota selection unavailable for this account"
    limits = usage.get("rate_limit") or {}
    for key, fallback in (("primary_window", "5h"), ("secondary_window", "7d")):
        raw = limits.get(key)
        if not isinstance(raw, dict):
            continue
        window = _codex_window({"used_percent": raw.get("used_percent"),
                                "window_minutes": (raw.get("limit_window_seconds") or 0) / 60,
                                "resets_at": raw.get("reset_at")}, fallback, time.time())
        if window:
            rec["windows"].append(window)
    credits = usage.get("credits")
    if isinstance(credits, dict):
        rec["credits"] = {k: credits.get(k) for k in ("has_credits", "balance", "unlimited")}
    spend = (usage.get("spend_control") or {}).get("individual_limit")
    if isinstance(spend, dict) and spend.get("unit") == "credit":
        rec["spend_control"] = {k: spend.get(k) for k in
                                ("source", "unit", "limit", "used", "remaining", "used_percent", "reset_at")}
        if isinstance(spend.get("used_percent"), (float, int)):
            reset = spend.get("reset_at")
            rec["windows"].append({"kind": "credits", "label": "Workspace credits",
                                   "percent": spend["used_percent"],
                                   "resets_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset)) if reset else None})
    return rec


def _fetch_codex(d, a):
    rec = {"logged_in": False, "error": "", "windows": [], "extra": None, "credits": None,
           "email": "", "org": "", "plan": ""}
    auth = _codex_auth(d, a)
    toks = (auth or {}).get("tokens") or {}
    if not (toks.get("access_token") or (auth or {}).get("OPENAI_API_KEY")):
        rec["error"] = "not logged in"
        return rec
    rec["logged_in"] = True
    c = _jwt_claims(toks.get("id_token", ""))
    oa = c.get("https://api.openai.com/auth") or {}
    rec["email"] = c.get("email", "")
    rec["plan"] = str(oa.get("chatgpt_plan_type") or ("api key" if auth.get("OPENAI_API_KEY") else "")).title()
    orgs = oa.get("organizations") or []
    rec["org"] = next((o.get("title", "") for o in orgs if o.get("is_default")), "")
    expected_account = toks.get('account_id') or oa.get('chatgpt_account_id')
    live_error = ""
    if toks.get("access_token") and expected_account:
        try:
            return _apply_codex_live(rec, _codex_live_usage(toks["access_token"], expected_account))
        except urllib.error.HTTPError as exc:
            live_error = f"usage endpoint HTTP {exc.code}"
            if exc.code == 401: rec["logged_in"] = False
        except Exception as exc:
            live_error = f"usage endpoint: {type(exc).__name__}"
    rl, mtime = _newest_rate_limits(_codex_home(d, a))
    if rl is None:
        rec["error"] = live_error or "no account-attributed quota observation yet"
        return rec
    rec["rollout_at"] = int(mtime) if mtime is not None else 0
    # Global rollout directories survive credential changes. A low quota from
    # an unattributed transcript is display evidence, never spare account quota.
    observed_account = rl.get('account_id') or rl.get('chatgpt_account_id')
    if not expected_account or observed_account != expected_account:
        rec['error'] = (live_error + "; " if live_error else "") + 'quota account attribution unknown'
        rec['quota_attributed'] = False
        # Rollouts in a shared home can belong to any historical login. Do not
        # copy their windows, plan or credits onto this profile.
        return rec
    rec['quota_attributed'] = True
    rec['quota_source'] = 'account-attributed rollout'
    for w, fb in ((rl.get("primary"), "5h"), (rl.get("secondary"), "7d")):
        win = _codex_window(w, fb, mtime)
        if win:
            rec["windows"].append(win)
    cr = rl.get("credits")
    if isinstance(cr, dict):
        rec["credits"] = {"has_credits": cr.get("has_credits"), "balance": cr.get("balance"),
                          "unlimited": cr.get("unlimited")}
    if rl.get("plan_type"):
        rec["plan"] = str(rl["plan_type"]).title()
    return rec


# ------------------------------------------------------------------ refresh + payload
def refresh(d=None):
    with LOCK:
        d = d or load()
        for a in list(d["accounts"] + d.get("pending_accounts", [])):
            try:
                rec = _fetch_claude(d, a) if a["tool"] == "claude" else _fetch_codex(d, a)
            except Exception as e:
                rec = {"logged_in": False, "error": f"{type(e).__name__}", "windows": []}
            rec["fetched_at"] = int(time.time())
            d["cache"][a["id"]] = rec
            _promote_verified(d, a, rec)
        save(d)
        return d


def payload(d=None):
    d = d or load()
    st = d["state"]
    auto = _auto_cfg()
    auto["last_switch"] = st.get("last_switch")
    out = []
    for a in sorted(d["accounts"] + d.get("pending_accounts", []), key=lambda x: (x["tool"], not _is_default(x))):
        c = d["cache"].get(a["id"], {})
        out.append({
            "id": a["id"], "tool": a["tool"], "label": a.get("label", ""),
            "email": c.get("email", ""), "org": c.get("org", ""), "plan": c.get("plan", ""),
            "config_dir": a["config_dir"], "is_default": _is_default(a),
            "logged_in": bool(c.get("logged_in")), "fetched_at": c.get("fetched_at"),
            "error": c.get("error", ""), "windows": c.get("windows", []),
            "cached_at": c.get("cached_at"), "rollout_at": c.get("rollout_at"),
            "extra": c.get("extra"), "credits": c.get("credits"),
            "spend_control": c.get("spend_control"), "quota_source": c.get("quota_source"),
            "quota_attributed": c.get("quota_attributed"),
            "global": st["global_active"].get(a["tool"]) == a["id"],
        })
    return {"active": st["active"], "auto_switch": auto,
            "accounts": [a for a in out if a['logged_in'] and a['email']],
            "pending_accounts": [a for a in out if not (a['logged_in'] and a['email'])]}


def _promote_verified(d, a, record):
    if record.get('logged_in') and record.get('email') and a in d.get('pending_accounts', []):
        d['pending_accounts'].remove(a)
        d['accounts'].append(a)


def verify(aid):
    """Check only this profile; a typed name alone never establishes a login."""
    with LOCK:
        d = load()
        a = _acct(d, aid)
        if not a:
            raise ValueError('Unknown account setup')
        rec = _fetch_claude(d, a) if a['tool'] == 'claude' else _fetch_codex(d, a)
        rec['fetched_at'] = int(time.time())
        d['cache'][aid] = rec
        _promote_verified(d, a, rec)
        save(d)
        return payload(d)


def _worst(c):
    ws = [w.get("percent") or 0 for w in (c or {}).get("windows", [])]
    return max(ws) if ws else None


# ------------------------------------------------------------------ env + pick
def env_for(tool, account_id=None, d=None):
    """Env for a CLI call as that profile. The globally logged-in account (and the
    default profile, whose home is the global location) run with the plain env."""
    d = d or load()
    a = _acct(d, account_id or d["state"]["active"].get(tool)) if (account_id or tool) else None
    if not a or a["tool"] != tool or _is_default(a) or d["state"]["global_active"].get(tool) == a["id"]:
        return _base_env()
    var = "CLAUDE_CONFIG_DIR" if tool == "claude" else "CODEX_HOME"
    return _base_env(**{var: a["config_dir"]})


def resolve_launch(tool, model=None, account_id=None, session_account_id=None, host_id='mac', d=None):
    """Read-only model-aware decision shared with the standalone connector."""
    from host_connector import resolve_account
    return resolve_account(d or load(), tool, model, account_id, session_account_id,
                           host_id=host_id, auto=_auto_cfg())


def pick(tool, model=None, account_id=None, session_account_id=None):
    """Compatibility accessor. Selection never mutates host-wide credentials."""
    return resolve_launch(tool, model, account_id, session_account_id)['account_id']


# ------------------------------------------------------------------ switch (mode global)
def _backup_dir():
    p = os.path.join(BACKUPS, time.strftime("%Y%m%dT%H%M%S"))
    os.makedirs(p, mode=0o700, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    return p


def _write_claude_json_oauth(acc):
    """Set oauthAccount in ~/.claude.json, preserving every other key (atomic)."""
    cur = _claude_json()
    if not cur:
        raise RuntimeError("~/.claude.json unreadable")
    cur["oauthAccount"] = acc
    st = os.stat(CLAUDE_JSON)
    _jwrite(CLAUDE_JSON, cur, mode=st.st_mode & 0o777)


def _switch_claude(d, src, dst):
    gblob = _kc_read(KC_SERVICE)
    gacc = _claude_json().get("oauthAccount")
    if not gblob:
        raise RuntimeError("cannot read global Claude credentials")
    bk = _backup_dir()
    _jwrite(os.path.join(bk, "claude-credentials.json"), gblob, mode=0o600)
    _jwrite(os.path.join(bk, "claude-oauthAccount.json"), gacc or {}, mode=0o600)
    # snapshot the outgoing login so its refreshed tokens are kept
    if _is_default(src):
        src["stash"] = bk
    else:
        _jwrite(os.path.join(src["config_dir"], ".credentials.json"), gblob, mode=0o600)
        _jwrite(os.path.join(src["config_dir"], "account.json"), gacc or {}, mode=0o600)
    # target credentials
    if _is_default(dst):
        if not dst.get("stash"):
            raise RuntimeError("no stashed default login to restore")
        tblob = _jread(os.path.join(dst["stash"], "claude-credentials.json"), None)
        tacc = _jread(os.path.join(dst["stash"], "claude-oauthAccount.json"), None)
    else:
        tblob, _ = _claude_creds(d, dst)
        tacc = _profile_oauth_account(dst)
    if not ((tblob or {}).get("claudeAiOauth") or {}).get("refreshToken") or not tacc:
        raise RuntimeError(f"{dst['id']} has no usable login; run its login command first")
    new = dict(gblob)  # keep the global mcpOAuth tokens; swap only the account login
    new["claudeAiOauth"] = tblob["claudeAiOauth"]
    if tblob.get("organizationUuid"):
        new["organizationUuid"] = tblob["organizationUuid"]
    _kc_write(KC_SERVICE, new)
    _write_claude_json_oauth(tacc)


def _switch_codex(d, src, dst):
    gpath = os.path.join(CODEX_HOME, "auth.json")
    gauth = _jread(gpath, None)
    if not gauth:
        raise RuntimeError("cannot read ~/.codex/auth.json")
    bk = _backup_dir()
    _jwrite(os.path.join(bk, "codex-auth.json"), gauth, mode=0o600)
    if _is_default(src):
        src["stash"] = bk
    else:
        _jwrite(os.path.join(src["config_dir"], "auth.json"), gauth, mode=0o600)
    if _is_default(dst):
        tauth = _jread(os.path.join(dst["stash"], "codex-auth.json"), None) if dst.get("stash") else None
    else:
        tauth = _jread(os.path.join(dst["config_dir"], "auth.json"), None)
    if not tauth:
        raise RuntimeError(f"{dst['id']} has no usable login; run its login command first")
    _jwrite(gpath, tauth, mode=0o600)


def switch(tool, target_id, reason="manual"):
    with LOCK:
        d = load()
        dst = _acct(d, target_id)
        if not dst or dst["tool"] != tool:
            raise ValueError("unknown account for that tool")
        cur_id = d["state"]["global_active"].get(tool) or f"{tool}-default"
        if cur_id != target_id:
            src = _acct(d, cur_id) or _acct(d, f"{tool}-default")
            (_switch_claude if tool == "claude" else _switch_codex)(d, src, dst)
        d["state"]["global_active"][tool] = target_id
        d["state"]["active"][tool] = target_id
        d["state"]["last_switch"] = {"tool": tool, "from": cur_id, "to": target_id,
                                     "at": int(time.time()), "mode": "global", "reason": reason}
        save(d)
        return refresh(d)


# ------------------------------------------------------------------ add / remove / config
def _slug(s):
    return re.sub(r"[^a-z0-9_-]+", "-", (s or "").strip().lower()).strip("-")


def add(tool, label):
    if tool not in ("claude", "codex"):
        raise ValueError("tool must be claude or codex")
    slug = _slug(label)
    if not slug or slug == "default":
        raise ValueError("label required (not 'default')")
    root = "~/.claude-profiles" if tool == "claude" else "~/.codex-profiles"
    short = f"{root}/{slug}"
    cdir = os.path.expanduser(short)
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    with LOCK:
        d = load()
        aid = f"{tool}-{slug}"
        if _acct(d, aid):
            raise ValueError("An account with this label already exists; choose another label")
        d.setdefault("pending_accounts", []).append({"id": aid, "tool": tool, "label": label, "config_dir": cdir})
        save(d)
    cmd = (f"CLAUDE_CONFIG_DIR={short} claude auth login" if tool == "claude"
           else f"CODEX_HOME={short} codex login")
    return {"ok": True, "id": aid, "config_dir": cdir, "login_cmd": cmd}


def start_login(aid):
    """Open an isolated native login in Terminal, only on an explicit UI action."""
    d = load()
    a = _acct(d, aid)
    if not a:
        raise ValueError("unknown account")
    if _is_default(a) or d["state"]["global_active"].get(a["tool"]) == aid:
        raise ValueError("Create a separate profile to add a login")
    key = "CLAUDE_CONFIG_DIR" if a["tool"] == "claude" else "CODEX_HOME"
    args = ["env", key + "=" + a["config_dir"], _cli(a["tool"])]
    args += ["auth", "login"] if a["tool"] == "claude" else ["login"]
    command = shlex.join(args)
    if sys.platform != 'darwin':
        return {"ok": True, "id": aid, "login_started": False, "login_cmd": command,
                "message": "Run this command in your terminal, then Check sign-in."}
    # Opening a .command file avoids Terminal's Apple Events permission and its
    # synchronous 15-second osascript timeout. No credential is placed in it.
    directory = os.path.join(DATA, 'login-scripts')
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, 'login-' + uuid.uuid4().hex + '.command')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, 'w') as handle:
        handle.write('#!/bin/sh\nexport PATH=' + shlex.quote(_base_env()['PATH']) + '\n' + command + '\nprintf "\\nSign-in finished. Return to Workspace and click Check sign-in.\\n"\n')
    try:
        result = subprocess.run(['/usr/bin/open', '-a', 'Terminal', path], capture_output=True, timeout=10)
        started = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        started = False
    return {"ok": True, "id": aid, "login_started": started, "login_cmd": command,
            "message": "Complete sign-in in Terminal, then Check sign-in." if started else "Terminal could not open. Run this command, then Check sign-in."}


def remove(aid):
    with LOCK:
        d = load()
        a = _acct(d, aid)
        if not a:
            return {"ok": False, "error": "unknown account"}
        if _is_default(a):
            return {"ok": False, "error": "the default profile cannot be removed"}
        if d["state"]["global_active"].get(a["tool"]) == aid:
            return {"ok": False, "error": "switch away from this account first"}
        d["accounts"] = [x for x in d["accounts"] if x["id"] != aid]
        d["pending_accounts"] = [x for x in d.get("pending_accounts", []) if x["id"] != aid]
        d["cache"].pop(aid, None)
        if d["state"]["active"].get(a["tool"]) == aid:
            d["state"]["active"][a["tool"]] = d["state"]["global_active"].get(a["tool"], f"{a['tool']}-default")
        save(d)
        return {"ok": True}


def set_auto(body):
    with LOCK:
        return _set_auto_locked(body)


def _set_auto_locked(body):
    cfg = _config()
    auto = dict(_auto_cfg())
    if "enabled" in body:
        auto["enabled"] = bool(body["enabled"])
    if "threshold" in body:
        auto["threshold"] = max(1, min(100, int(body["threshold"])))
    if body.get("mode") in ("global", "dashboard", "off"):
        auto["mode"] = body["mode"]
    cfg["auto_switch"] = auto
    _jwrite(CONFIG_FILE, cfg)
    return auto


# ------------------------------------------------------------------ routes + background
def _h_get(req, query, body):
    return payload()


def _h_refresh(req, query, body):
    return payload(refresh())


def _h_add(req, query, body):
    body = body or {}
    try:
        return add(body.get("tool"), body.get("label"))
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400


def _h_login(req, query, body):
    try:
        return start_login((body or {}).get("id"))
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}, 400


def _h_verify(req, query, body):
    try:
        return verify((body or {}).get("id"))
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}, 400


def _h_remove(req, query, body):
    r = remove((body or {}).get("id"))
    return r if r["ok"] else (r, 400)


def _h_switch(req, query, body):
    body = body or {}
    try:
        return payload(switch(body.get("tool"), body.get("id")))
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}, 400


def _h_auto(req, query, body):
    set_auto(body or {})
    return payload()


def background():
    """Refresh quota observations only; routing occurs once at launch time."""
    global _last_bg
    every = int(_config().get("usage_refresh_s") or 300)
    if time.time() - _last_bg < every:
        return
    _last_bg = time.time()
    refresh()


ROUTES = {
    ("GET", "/api/accounts"): _h_get,
    ("POST", "/api/accounts/refresh"): _h_refresh,
    ("POST", "/api/accounts/add"): _h_add,
    ("POST", "/api/accounts/login"): _h_login,
    ("POST", "/api/accounts/verify"): _h_verify,
    ("POST", "/api/accounts/remove"): _h_remove,
    ("POST", "/api/accounts/switch"): _h_switch,
    ("POST", "/api/accounts/auto_switch"): _h_auto,
}
BACKGROUND = [(background, 30)]


# ------------------------------------------------------------------ CLI
def _rel(iso):
    if not iso:
        return "-"
    try:
        t = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return iso
    s = int(t - time.time())
    if s <= 0:
        return "now"
    dd, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return f"{dd}d{h:02d}h" if dd else (f"{h}h{m:02d}m" if h else f"{m}m")


def _print_table(p):
    print(f"active: {p['active']}  auto_switch: {json.dumps({k: v for k, v in p['auto_switch'].items()})}")
    for a in p["accounts"]:
        mark = "*" if p["active"].get(a["tool"]) == a["id"] else " "
        print(f"{mark} {a['id']:<18} {a['email'] or '-':<26} {a['plan'] or '-':<16} "
              f"{'logged in' if a['logged_in'] else 'logged out'}  {a['error']}")
        for w in a["windows"]:
            print(f"      {w['label']:<16} {w['percent']:>4}%  resets in {_rel(w['resets_at'])} ({w['resets_at']})")
        if a.get("extra"):
            e = a["extra"]
            print(f"      Extra usage      {e['percent']}%  {e['used']} / {e['limit']} {e['currency']}")
        if a.get("credits"):
            print(f"      Credits          {json.dumps(a['credits'])}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        _print_table(payload(refresh()))
    elif args[0] == "add" and len(args) == 3:
        r = add(args[1], args[2])
        print(f"profile {r['id']} at {r['config_dir']}\nrun once:\n  {r['login_cmd']}")
    elif args[0] == "remove" and len(args) == 2:
        print(json.dumps(remove(args[1])))
    elif args[0] == "switch" and len(args) == 3:
        _print_table(payload(switch(args[1], args[2])))
    else:
        print(__doc__)
        sys.exit(2)
