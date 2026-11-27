"""Deterministic provenance for known unattended jobs, independent of titles."""
import copy
import json
import os
import re
import time
from session_lineage import effective_assignments

AUTOMATION_PROJECT = {'id': 'automation-runs', 'name': 'Automation runs', 'archived': True, 'automation': True}
PREFIXES = ()  # every prefix is user-specific, so all of them live in data/config.json

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'config.json')
_private = [None, (), None, 0.0]  # config stamp, extra prefixes, config path, last check


def prefixes():
    """Built-in prefixes plus private ones from data/config.json:
    "automation_prompt_prefixes": [["You triage one conversation for <name>", "attention review"], ...]

    The config is checked at most once a second: /api/state calls this for every
    chat, and 46k stat calls per build stalled the whole server behind the GIL."""
    if _private[2] == CONFIG and time.monotonic() - _private[3] < 1:
        return PREFIXES + _private[1]
    try:
        stamp = os.stat(CONFIG).st_mtime_ns
    except OSError:
        _private[:] = [None, (), CONFIG, time.monotonic()]
        return PREFIXES
    _private[2:] = [CONFIG, time.monotonic()]
    if _private[0] != stamp:
        try:
            with open(CONFIG) as fh:
                extra = json.load(fh).get('automation_prompt_prefixes') or []
            _private[:2] = [stamp, tuple((e[0], e[1]) for e in extra if isinstance(e, list) and len(e) == 2
                                         and isinstance(e[0], str) and e[0] and isinstance(e[1], str))]
        except (OSError, ValueError, TypeError, AttributeError):
            _private[:2] = [stamp, ()]
    return PREFIXES + _private[1]


def human_project_name(name, misc):
    return misc if re.sub(r'[^a-z0-9]', '', name.lower()) == 'automationruns' else name


def automation_kind(chat):
    prompt = (chat.get('first_prompt') or '').lstrip()
    if not prompt and chat.get('turns'):
        prompt = next((t.get('text', '').lstrip() for t in chat['turns'] if t.get('role') == 'user'), '')
    return next((kind for prefix, kind in prefixes() if prompt.startswith(prefix)), None)


def effective_projects(index, projects):
    result = copy.deepcopy(projects)
    runs = [key for key, chat in index.items() if not chat.get('is_child') and automation_kind(chat)]
    if runs:
        project = next((p for p in result['projects'] if p['id'] == AUTOMATION_PROJECT['id']), None)
        if project is None:
            result['projects'].append(dict(AUTOMATION_PROJECT))
        else:
            project.update(AUTOMATION_PROJECT)
        result.setdefault('assignments', {}).update({key: AUTOMATION_PROJECT['id'] for key in runs})
    result['assignments'] = effective_assignments(index, result.get('assignments') or {})
    return result
