import json
import os
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import indexer

class RecordedChannels(unittest.TestCase):
    def test_preserves_recorded_channels_without_inference(self):
        rows = []
        for channel, text in [('commentary', 'Checking the build.'), ('final', 'The build passed.')]:
            rows += [{'type':'event_msg','timestamp':'2026-11-15T12:00:00Z','payload':{'type':'agent_message','message':text}}, {'type':'response_item','timestamp':'2026-11-15T12:00:00Z','payload':{'type':'message','role':'assistant','channel':channel,'content':[{'type':'output_text','text':text}]}}]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl') as f:
            for row in rows: f.write(json.dumps(row)+'\n')
            f.flush()
            result=indexer.parse_codex_file(f.name)
        self.assertEqual([t.get('channel') for t in result[1]], ['commentary','final'])

    def test_claude_thinking_is_kept_as_analysis_turn(self):
        rows = [{'type':'user','timestamp':'2026-11-15T12:00:00Z','message':{'role':'user','content':'Fix it'}},
                {'type':'assistant','timestamp':'2026-11-15T12:00:01Z','message':{'id':'m1','content':[{'type':'thinking','thinking':'Check the log first.','signature':'s'},{'type':'thinking','thinking':'','signature':'s'}]}},
                {'type':'assistant','timestamp':'2026-11-15T12:00:02Z','message':{'id':'m1','stop_reason':'end_turn','content':[{'type':'text','text':'Fixed.'}]}}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'proj', 's1.jsonl')
            os.makedirs(os.path.dirname(path))
            with open(path, 'w') as f:
                for row in rows: f.write(json.dumps(row)+'\n')
            chat = indexer.parse_claude(path)
        self.assertEqual([(t['role'], t.get('channel')) for t in chat['turns']], [('user',None),('assistant','analysis'),('assistant',None)])
        self.assertEqual(chat['turns'][1]['text'], 'Check the log first.')
        self.assertEqual(chat['n_answer'], 1)
