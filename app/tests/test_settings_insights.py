import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import settings_insights as s
import unittest, tempfile
from unittest.mock import patch

def test_custom_calls_deduplicated_and_system_hidden(tmp_path):
    path=tmp_path/'session.jsonl'
    msg={'type':'assistant','timestamp':'2026-11-14T15:00:00Z','message':{'content':[
        {'type':'tool_use','id':'a','name':'mcp__research__search','input':{}},
        {'type':'tool_use','id':'b','name':'Bash','input':{'command':'pwd'}},
        {'type':'tool_use','id':'c','name':'Skill','input':{'skill':'paper-reading'}}]}}
    result={'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'a','content':'x'*40,'is_error':True}]}}
    path.write_text('\n'.join(json.dumps(x) for x in [msg,msg,result]))
    calls=s.scan_calls([str(path)])['calls']
    assert len(calls)==2
    row=next(x for x in calls if x['name']=='mcp__research__search')
    assert row['result_chars']==40 and row['failed'] is True
    assert row['cost_usd'] is None
    assert any(x['kind']=='skill' and x['name']=='paper-reading' for x in calls)

def test_codex_nested_tools(tmp_path):
    p=tmp_path/'rollout.jsonl'
    p.write_text(json.dumps({'type':'response_item','timestamp':'2026-11-14T15:00:00Z','payload':{'type':'function_call','call_id':'a','name':'functions.exec','arguments':'await tools.mcp__custom__lookup({}); await tools.exec_command({cmd:"pwd"});'}}))
    calls=s.scan_calls([str(p)])['calls']
    assert [x['name'] for x in calls]==['mcp__custom__lookup']
    assert calls[0]['evidence']=='nested reference'

def test_resources_scoped_and_preview_allowlist(tmp_path):
    s.HOME=tmp_path
    workspace=tmp_path/'project';workspace.mkdir()
    p=workspace/'.claude/skills/review/SKILL.md';p.parent.mkdir(parents=True);p.write_text('---\nname: Review\ndescription: Checks work\n---\nInstructions')
    (workspace/'AGENTS.md').write_text('Local rules')
    index={'a':{'cwd':str(workspace),'node':'mac'},'b':{'cwd':str(workspace),'node':'mac','is_child':True}}
    s.load_index=lambda:index
    data=s.catalog(str(workspace))
    assert data['workspaces'][0]['chats']==1 and data['workspaces'][0]['agents']==1
    row=next(x for x in data['items'] if x['kind']=='skill')
    assert row['scope']=='Workspace' and row['name']=='Review'
    assert s.preview(row['id'],str(workspace))['text'].endswith('Instructions')
    assert s.preview('/etc/passwd',str(workspace))[1]==404

def test_pricing_does_not_price_unknown_model_or_hide_cache_ttl(tmp_path):
    x=s.estimate_cost([{'model':'claude-opus-5-5','in':1000000,'out':1000000,'cache_read':1000000,'cache_create':1000000}])
    assert x['low']==29.2 and x['high']==32.2 and x['coverage']==1
    unknown=s.estimate_cost([{'model':'future-unknown','in':100}])
    assert unknown['low'] is None and unknown['coverage']==0


def test_linked_agent_usage_and_automation_filter(tmp_path):
    today=s.dt.datetime.now().astimezone().date().isoformat()
    root={'cwd':str(tmp_path),'node':'mac','source':'claude','ts_last':today,'spawned_children':[{'agent_id':'kid','agent_type':'reviewer'}]}
    child={'cwd':str(tmp_path),'node':'mac','source':'claude','ts_last':today,'is_child':True,'agent_id':'kid','parent_key':'root','root_key':'root','usage_days':{today:{'in':100,'out':20}},'usage_buckets':[{'day':today,'model':'claude-opus-5-5','in':100,'out':20}]}
    with patch.object(s,'load_index',lambda:{'root':root,'child':child}), patch.object(s,'load_json',lambda *args:{}):
        data=s.analytics(7)
    assert len(data['agents'])==1 and data['agents'][0]['name']=='reviewer'
    assert data['agents'][0]['usage']['in']==100
    assert data['agents'][0]['sessions'][0]['key']=='root'
    assert data['agents'][0]['estimate']['low']==.0008

def test_remote_catalog_does_not_read_local_matching_path(tmp_path):
    (tmp_path/'AGENTS.md').write_text('local only')
    s.load_index=lambda:{'r':{'cwd':str(tmp_path),'node':'sc'}}
    data=s.catalog(str(tmp_path),'sc')
    assert data['items']==[] and 'not been inventoried' in data['notice']


def test_nested_reference_inside_string_is_not_a_tool_call(tmp_path):
    p=tmp_path/'r.jsonl'
    code='await tools.exec_command({cmd: "example tools.mcp__fake__test({})"}); // tools.mcp__fake__comment()'
    p.write_text(json.dumps({'type':'response_item','timestamp':'2026-11-14T15:00:00Z','payload':{'type':'function_call','call_id':'a','name':'functions.exec','arguments':code}}))
    assert s.scan_calls([str(p)])['calls']==[]


def test_codex_namespace_and_result_call_id(tmp_path):
    p=tmp_path/'r.jsonl'
    records=[{'type':'response_item','timestamp':'2026-11-14T15:00:00Z','payload':{'type':'function_call','id':'item','call_id':'call','namespace':'mcp__cua_repl','name':'js','arguments':'{}'}},
      {'type':'response_item','payload':{'type':'function_call_output','call_id':'call','output':'hello'}}]
    p.write_text('\n'.join(json.dumps(r) for r in records))
    calls=s.scan_calls([str(p)])['calls']
    assert len(calls)==1 and calls[0]['name']=='mcp__cua_repl.js'
    assert calls[0]['result_chars']==5


def test_mcp_wrapped_native_tools_are_not_custom(tmp_path):
    assert s.custom_kind('mcp__claude__Bash',{}) is None
    assert s.custom_kind('mcp__claude.Read',{}) is None
    assert s.custom_kind('mcp__claude__Skill',{'skill':'research'})==('skill','research')
    assert s.custom_kind('mcp__myapp__lookup',{})==('tool','mcp__myapp__lookup')


def test_plugin_agent_definitions_are_cataloged(tmp_path):
    p=tmp_path/'.claude/plugins/cache/example/reviewer/1.0/agents/checker.md'
    p.parent.mkdir(parents=True);p.write_text('---\nname: checker\ndescription: Reviews changes\n---\nCheck the work.')
    s.HOME=tmp_path;s.load_index=lambda:{}
    rows=s.catalog()['items']
    assert any(r['name']=='checker' and r['kind']=='agent' and r['scope']=='Plugin' for r in rows)


class InsightsTests(unittest.TestCase):
    pass

def wrap(fn):
    def run(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, "HOME", s.HOME), patch.object(s, "load_index", s.load_index):
            fn(Path(d))
    return run
for name,fn in list(globals().items()):
    if name.startswith("test_"):setattr(InsightsTests,name,wrap(fn))
