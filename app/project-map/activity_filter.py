"""Select recorded recent work and ongoing families before graph pagination."""
from datetime import datetime

PERIODS={'live','3','7','14','30','all'}

def stamp(value):
 if isinstance(value,(int,float)):return float(value)
 try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return 0

def select_activity(index,keys,working,consultations,period,now):
 period=str(period)
 if period not in PERIODS:raise ValueError('Unsupported activity window')
 if period=='all':return set(keys)
 cutoff=now-int(period)*86400 if period!='live' else now
 keep=set(working)&set(keys)
 if period!='live':keep.update(k for k in keys if cutoff<=stamp(index[k].get('ts_last'))<=now)
 for record in consultations.values():
  recent=period!='live' and any(cutoff<=stamp(e.get('time'))<=now for e in record.get('events',[]))
  if recent or record.get('status',{}).get('state') in ('working','waiting'):
   keep.update(p for p in record.get('parents',[]) if p in keys)
 # Promote evidence to recorded ancestors without inventing lineage.
 pending=list(keep)
 while pending:
  m=index.get(pending.pop(),{})
  for p in (m.get('root_key'),m.get('parent_key')):
   if p in keys and p not in keep:keep.add(p);pending.append(p)
 return keep
