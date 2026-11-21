"""Provider auth/quota failures shared by the broker, indexer and Official helper.

Patterns come from observed structured errors and installed CLI strings recorded
in docs/AUTH-RECOVERY.md. Ordinary assistant prose is never an error signal.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KINDS = ('auth_expired', 'not_logged_in', 'limit_reached', 'rate_limited', 'other')

def instant(value=None):
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(value / 1000 if value > 1e12 else value, dt.timezone.utc)
    if value:
        try:
            result=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return result if result.tzinfo else result.replace(tzinfo=dt.timezone.utc)
        except ValueError:pass
    return dt.datetime.now(dt.timezone.utc)

def iso(value):return value.astimezone(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def reset_time(raw, structured=None, at=None):
    base=instant(at)
    def walk(item):
        if isinstance(item,dict):
            for key,value in item.items():
                if key in ('resets_at','resetsAt','reset_at','resetAt') and value:
                    try:
                        if isinstance(value,(int,float)) or re.fullmatch(r'\d{10,13}(?:\.\d+)?',str(value)):
                            return iso(instant(float(value)))
                        if re.match(r'\d{4}-\d\d-\d\dT',str(value)):return iso(instant(value))
                    except (ValueError,OverflowError,OSError):pass
                if key in ('resets_in_seconds','retry_after_seconds') and isinstance(value,(int,float)):
                    return iso(base+dt.timedelta(seconds=max(0,value)))
                found=walk(value)
                if found:return found
        elif isinstance(item,list):
            for value in item:
                found=walk(value)
                if found:return found
    explicit=walk(structured)
    if explicit:return explicit
    # Codex renders its reset deadline in the CLI host's local timezone.
    codex = re.search(r'try again at ([A-Za-z]{3})[a-z]* (\d{1,2})(?:st|nd|rd|th)?,? (\d{4}) (\d{1,2}):(\d{2}) ?(AM|PM)', raw, re.I)
    if codex:
        month, day, year, hour, minute, meridiem = codex.groups()
        try:
            value = dt.datetime(int(year), dt.datetime.strptime(month.title(), '%b').month, int(day), int(hour) % 12 + (12 if meridiem.lower() == 'pm' else 0), int(minute))
            return iso(value.astimezone())
        except ValueError:
            return None
    # Claude formats quota resets in the named IANA timezone, including midnight.
    found=re.search(r'resets\s+(?:(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:,?\s+(\d{4}))?\s+at\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)\s*\(([^)]+)\)',raw,re.I)
    if not found:return None
    month,day,year,hour,minute,meridiem,zone=found.groups()
    try:
        tz=ZoneInfo(zone);local=base.astimezone(tz);h=int(hour)%12+(12 if meridiem.lower()=='pm' else 0)
        monthnum=dt.datetime.strptime(month.title(),'%b').month if month else local.month
        result=dt.datetime(int(year) if year else local.year,monthnum,int(day) if day else local.day,h,int(minute or 0),tzinfo=tz)
        if not day and result < local:result+=dt.timedelta(days=1)
        elif month and not year and result < local-dt.timedelta(days=180):result=result.replace(year=result.year+1)
        return iso(result)
    except (ValueError,ZoneInfoNotFoundError):return None

def classify(provider,raw,account_id=None,at=None,structured=None,account_label=None):
    if not isinstance(raw,str):raw=json.dumps(raw,ensure_ascii=False)
    text=raw.casefold().replace('’',"'")
    metadata=json.dumps(structured or {},ensure_ascii=False).casefold()
    both=text+' '+metadata
    if ('login reached its time limit' in both or
        'access token could not be refreshed' in both or
        'token_expired' in both or 'refresh_token_reused' in both):kind='auth_expired'
    elif ("you've hit your weekly limit" in both or "you've hit your usage limit" in both or
          'usagelimitexceeded' in both or 'usage_limit_exceeded' in both or 'usage_limit_reached' in both or 'insufficient_quota' in both):kind='limit_reached'
    elif ('not logged in' in both or 'unauthorized' in both or 'authentication_failed' in both or
          'authentication_error' in both or 'invalid_api_key' in both):kind='not_logged_in'
    elif ('ratelimitexceeded' in both or 'rate_limit' in both or 'rate limit' in both or 'too many requests' in both):kind='rate_limited'
    else:kind='other'
    result={'kind':kind,'provider':provider,'account_id':account_id or '',
            'resets_at':reset_time(raw,structured,at),'raw':raw}
    if at:result['observed_at']=iso(instant(at))
    if account_label:result['account_label']=account_label
    return result

def _text(value):
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,dict):return _text(parsed)
        except ValueError:pass
        return value
    if isinstance(value,list):return '\n'.join(filter(None,(_text(v) for v in value)))
    if isinstance(value,dict):
        for key in ('message','text','result','errors','error','content'):
            if value.get(key):return _text(value[key])
    return ''

def from_event(provider,event,account_id=None,at=None):
    if not isinstance(event,dict):return None
    typ=event.get('type');method=event.get('method');payload=event.get('payload') or {};params=event.get('params') or {}
    if provider=='claude':
        if event.get('parent_tool_use_id') or event.get('isSidechain') or (event.get('origin') or {}).get('kind') == 'task-notification':
            return None
        actual=(event.get('isApiErrorMessage') is True or typ=='error' or typ=='result' and event.get('is_error') is True)
        detail=event
    else:
        actual=(typ in ('error','turn.failed') or typ=='event_msg' and (payload.get('type')=='error' or payload.get('type')=='task_complete' and bool(payload.get('error'))) or method=='error' or method=='turn/completed' and (params.get('turn') or {}).get('status')=='failed')
        detail=(params.get('turn') or {}).get('error') if method=='turn/completed' else params if method else payload if typ=='event_msg' else event
    if not actual:return None
    raw=_text(detail) or str(event.get('subtype') or 'Provider error')
    return classify(provider,raw,account_id,at or event.get('timestamp'),detail)

def transcript_failure(provider,path,account_id=None):
    """Read one native file without writing or resuming it; return newest error."""
    result={'failure':None,'marker':None,'message':'','through_turn':-1,'session_id':None,'active':False}
    last_role=None;turn=-1;last_user=''
    with Path(path).open(errors='replace') as lines:
        for number,line in enumerate(lines):
            try:event=json.loads(line)
            except ValueError:continue
            payload=event.get('payload') or {};typ=event.get('type')
            if typ=='session_meta':result['session_id']=payload.get('id') or payload.get('session_id')
            result['session_id']=event.get('sessionId') or event.get('session_id') or result['session_id']
            role=text=None
            if provider=='claude' and typ in ('user','assistant'):
                if event.get('isMeta') or event.get('isCompactSummary') or event.get('isSidechain'):
                    continue
                content=(event.get('message') or {}).get('content')
                role=typ
                text=content if isinstance(content,str) else '\n'.join(b.get('text','') for b in content or [] if isinstance(b,dict) and b.get('type')=='text')
                if role=='user' and text:result['active']=True
                elif role=='assistant' and (event.get('message') or {}).get('stop_reason') in ('end_turn','stop_sequence'):result['active']=False

            elif provider=='codex' and typ=='event_msg' and payload.get('type') in ('user_message','agent_message'):
                role='user' if payload['type']=='user_message' else 'assistant';text=_text(payload)
            if provider=='codex' and typ=='event_msg':
                if payload.get('type')=='task_started':result['active']=True
                elif payload.get('type') in ('task_complete','turn_aborted'):result['active']=False
            failure=from_event(provider,event,account_id)
            if failure:
                result.update(failure=failure,marker=hashlib.sha256(line.encode()).hexdigest(),message=last_user,through_turn=turn,active=False)
            elif role=='assistant' and text:
                result.update(failure=None,marker=None)
            if role and text:
                if role!=last_role:turn+=1
                last_role=role
                if role=='user':last_user=text
    return result
