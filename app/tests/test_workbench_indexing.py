import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

import indexer


class IndexingTests(unittest.TestCase):
    def test_shared_profile_session_links_record_all_hints_without_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            shared=base/'default'/'sessions'
            shared.mkdir(parents=True)
            alias=base/'personal'
            alias.mkdir()
            (alias/'sessions').symlink_to(shared,target_is_directory=True)
            data=base/'data'
            data.mkdir()
            (data/'accounts.json').write_text(json.dumps({'accounts':[{'id':'default','tool':'codex','config_dir':str(base/'default')},{'id':'personal','tool':'codex','config_dir':str(alias)}]}))
            with patch.object(indexer,'DATA',str(data)):
                locations=indexer.source_locations({'mac':[str(shared/'rollout.jsonl')]},{})
            self.assertEqual(locations[0]['profile_ids'],['default','personal'])
            self.assertNotIn('account_id',locations[0])

    def test_codex_retains_only_published_progress_from_reasoning_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout.jsonl'
            record = {'type': 'response_item', 'timestamp': '2026-11-25', 'payload': {
                'type': 'reasoning', 'encrypted_content': 'opaque', 'content': [{'text': 'internal'}],
                'summary': [{'type': 'summary_text', 'text': 'Checking the layout.'}]}}
            path.write_text(json.dumps(record) + '\n')
            self.assertEqual(indexer.parse_codex_file(str(path))[1], [{
                'role': 'assistant', 'channel': 'analysis', 'text': 'Checking the layout.', 'ts': '2026-11-25'}])

    def test_exact_codex_profile_mirror_does_not_duplicate_turns(self):
        parsed=('/work',[{'role':'user','text':'hello'},{'role':'assistant','text':'answer'}],{},'2026-01-01','2026-01-02','complete',False,[],{},[])
        with patch.object(indexer,'parse_codex_file',return_value=parsed):
            result=indexer.parse_codex_group('id',['first','mirror'],{})
        self.assertEqual(result['n_user'],1)
        self.assertEqual(result['files_all'],['first','mirror'])

    def test_profile_roots_deduplicate_and_provenance_stays_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            profile=base/'profile'
            profile.mkdir()
            data=base/'data'
            data.mkdir()
            (data/'accounts.json').write_text(json.dumps({'accounts':[{'id':'extra','tool':'claude','config_dir':str(profile)}, {'id':'alias','tool':'claude','config_dir':str(profile)}]}))
            cfg={'claude_projects_root':str(base/'default/projects'),'codex_sessions_root':str(base/'codex/sessions'),'codex_index':str(base/'codex/session_index.jsonl')}
            with patch.object(indexer,'DATA',str(data)):
                roots=indexer.source_roots(cfg)
                self.assertEqual(sum(r[1]==str(profile/'projects') for r in roots),1)
                provenance=indexer.source_locations({'mac':[str(profile/'projects/p/s.jsonl')]},cfg)
            self.assertEqual(provenance[0]['profile_ids'],['alias','extra'])
            self.assertEqual(provenance[0]['provenance'],'configured_profile')

    def test_latest_claude_mirror_wins_within_host(self):
        chats={'old':{'ts_last':'2026-01-01','files_all':['old']},'new':{'ts_last':'2026-02-01','files_all':['new']}}
        with patch.object(indexer,'parse_claude',side_effect=lambda p:dict(chats[p])):
            result=indexer.parse_unit('claude-id','claude',{'mac':['old','new']},{})
        self.assertEqual(result['ts_last'],'2026-02-01')
        self.assertEqual(result['files_all'],['new','old'])
