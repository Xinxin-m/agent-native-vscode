import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import indexer
from live_library import LiveLibrary


class LiveLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir()
        self.cfg = dict(indexer.DEFAULT_CONFIG, claude_projects_root=str(self.root/'projects'),
                        codex_sessions_root=str(self.sessions), codex_index=str(self.root/'names'))
        self.path = self.sessions/'rollout-2026-11-14T00-00-00-11111111-1111-1111-1111-111111111111.jsonl'
        self.key = 'codex-11111111-1111-1111-1111-111111111111'
        self.live = LiveLibrary(interval=0)
        self.addCleanup(patch.stopall)
        patch.object(indexer, 'configured_profiles', return_value=[]).start()

    def append(self, kind, payload):
        with self.path.open('a') as f:
            f.write(json.dumps({'type':kind,'timestamp':'2026-11-14T12:00:00Z','payload':payload})+'\n')

    def start(self):
        self.append('session_meta', {'id':self.key[6:],'cwd':'/work'})
        self.append('event_msg', {'type':'user_message','message':'Brand new chat'})

    def test_discovers_chat_before_first_answer_and_refreshes_persisted_reply(self):
        self.start()
        meta = self.live.snapshot({}, self.cfg)[self.key]
        self.assertEqual(meta['n_user'], 1)
        self.assertEqual(meta['n_asst'], 0)
        revision = self.live.revision
        self.append('event_msg', {'type':'agent_message','message':'Still working, newest response'})
        self.live.snapshot({}, self.cfg)
        self.assertNotEqual(revision, self.live.revision)
        self.assertEqual(self.live.chat(self.key)['turns'][-1]['text'], 'Still working, newest response')

    def test_merges_resume_and_preserves_dashboard_execution_binding(self):
        self.start()
        self.append('event_msg', {'type':'agent_message','message':'First answer'})
        previous = self.live.snapshot({}, self.cfg)[self.key]
        previous['account_id'] = 'verified-profile'
        self.append('event_msg', {'type':'agent_message','message':'Continued answer'})
        updated = self.live.snapshot({self.key:previous}, self.cfg)[self.key]
        self.assertEqual(updated['account_id'], 'verified-profile')
        self.assertIn('Continued answer', self.live.chat(self.key)['turns'][-1]['text'])

    def test_unchanged_rollout_is_not_reparsed(self):
        self.start()
        self.live.snapshot({}, self.cfg)
        with patch.object(indexer, 'parse_unit', wraps=indexer.parse_unit) as parse:
            self.live.snapshot({}, self.cfg)
        parse.assert_not_called()

    def test_partial_json_line_does_not_hide_last_saved_message(self):
        self.start()
        self.append('event_msg', {'type':'agent_message','message':'Saved reply'})
        with self.path.open('a') as f:
            f.write('{"type":')
        self.live.snapshot({}, self.cfg)
        self.assertEqual(self.live.chat(self.key)['turns'][-1]['text'], 'Saved reply')

    def test_merges_a_new_resume_rollout(self):
        self.start()
        self.append('event_msg', {'type':'agent_message','message':'First answer'})
        previous = self.live.snapshot({}, self.cfg)[self.key]
        resume = self.sessions/('rollout-2026-11-14T13-00-00-' + self.key[6:] + '.jsonl')
        resume.write_text('\n'.join(json.dumps({'type':'event_msg','timestamp':'2026-11-14T13:00:00Z','payload':p}) for p in [
            {'type':'user_message','message':'Continue this session'},
            {'type':'agent_message','message':'Resumed answer'}])+'\n')
        merged = self.live.snapshot({self.key:previous}, self.cfg)[self.key]
        self.assertEqual(merged['n_user'], 2)
        self.assertEqual(len(merged['files_all']), 2)
        self.assertEqual(self.live.chat(self.key)['turns'][-1]['text'], 'Resumed answer')

    def test_publish_updates_cleaned_chat_and_index_without_label_calls(self):
        import live_refresh
        self.start()
        chats = self.root/'cleaned'
        chats.mkdir()
        archive = self.root/'index.json'
        archive.write_text('{}')
        config = self.root/'config.json'
        config.write_text(json.dumps(self.cfg))
        with patch.object(indexer,'DATA',str(self.root)), patch.object(indexer,'INDEX',str(archive)), patch.object(indexer,'CHATS',str(chats)), patch.object(indexer,'CONFIG',str(config)):
            self.assertEqual(live_refresh.publish(self.live), 1)
            self.assertIn(self.key, json.loads(archive.read_text()))
            self.assertEqual(json.loads((chats/(self.key+'.json')).read_text())['n_user'], 1)
            self.assertEqual(live_refresh.publish(self.live), 0)

    def test_publish_without_change_does_not_reload_or_rewrite_index(self):
        import live_refresh
        self.start()
        chats = self.root/'cleaned'
        chats.mkdir()
        archive = self.root/'index.json'
        archive.write_text('{}')
        config = self.root/'config.json'
        config.write_text(json.dumps(self.cfg))
        with patch.object(indexer,'DATA',str(self.root)), patch.object(indexer,'INDEX',str(archive)), patch.object(indexer,'CHATS',str(chats)), patch.object(indexer,'CONFIG',str(config)):
            self.assertEqual(live_refresh.publish(self.live), 1)
            before = archive.stat().st_mtime_ns
            with patch.object(live_refresh, 'load', wraps=live_refresh.load) as load, \
                 patch.object(indexer, 'atomic_json_dump') as dump:
                self.assertEqual(live_refresh.publish(self.live), 0)
                self.assertEqual(live_refresh.publish(self.live), 0)
                loaded = [c.args[0] for c in load.call_args_list]
                self.assertNotIn(indexer.INDEX, loaded)
                dump.assert_not_called()
            self.assertEqual(archive.stat().st_mtime_ns, before)
            self.append('event_msg', {'type':'agent_message','message':'new reply'})
            self.assertEqual(live_refresh.publish(self.live), 1)
            self.assertEqual(json.loads((chats/(self.key+'.json')).read_text())['turns'][-1]['text'], 'new reply')

    def test_publish_retries_when_codex_finishes_after_the_rollout_was_read(self):
        import live_refresh
        self.start()
        chats = self.root / 'cleaned'
        chats.mkdir()
        archive = self.root / 'index.json'
        archive.write_text('{}')
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.cfg))
        parse_unit = indexer.parse_unit

        def finish_after_read(*args):
            chat = parse_unit(*args)
            self.append('response_item', {'type': 'message', 'role': 'assistant',
                                          'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': 'Final answer'}]})
            self.append('event_msg', {'type': 'task_complete', 'last_agent_message': 'Final answer'})
            return chat

        with patch.object(indexer, 'DATA', str(self.root)), patch.object(indexer, 'INDEX', str(archive)), \
             patch.object(indexer, 'CHATS', str(chats)), patch.object(indexer, 'CONFIG', str(config)):
            with patch.object(indexer, 'parse_unit', side_effect=finish_after_read):
                live_refresh.publish(self.live)
            self.assertEqual(live_refresh.publish(self.live), 1)
            transcript = json.loads((chats / (self.key + '.json')).read_text())
            self.assertEqual([(turn['role'], turn['text']) for turn in transcript['turns']],
                             [('user', 'Brand new chat'), ('assistant', 'Final answer')])
            self.assertEqual(live_refresh.publish(self.live), 0)

    def test_older_live_snapshot_does_not_replace_a_newer_archive_snapshot(self):
        self.start()
        self.live.refresh({}, self.cfg)
        self.append('event_msg', {'type': 'agent_message', 'message': 'Final answer'})
        saved = LiveLibrary(interval=0).snapshot({}, self.cfg)
        self.assertEqual(self.live.merge(saved), saved)
        self.assertEqual(saved[self.key]['n_asst'], 1)
