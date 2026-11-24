import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import indexer

class OfficialIndexingTests(unittest.TestCase):
    def test_claude_panel_metadata_does_not_move_activity_or_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'session.jsonl'
            rows=[{'type':'user','timestamp':'2026-11-15T10:00:00Z','cwd':tmp,'message':{'role':'user','content':'Original question'}}, {'type':'assistant','timestamp':'2026-11-15T10:01:00Z','message':{'role':'assistant','content':[{'type':'text','text':'Original answer'}]}}]
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            before=indexer.parse_claude(str(p))
            with p.open('a') as f:
                for typ in ('session-owner','tools-used','thread_settings_applied'):
                    f.write(json.dumps({'type':typ,'timestamp':'2026-11-16T12:00:00Z','cwd':'/metadata','aiTitle':'Metadata'})+'\n')
            after=indexer.parse_claude(str(p))
            for field in ('ts_first','ts_last','title_seed','turns','cwd'):
                self.assertEqual(before[field],after[field],field)

    def test_codex_resume_metadata_does_not_move_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'rollout.jsonl'
            p.write_text(json.dumps({'type':'session_meta','timestamp':'2026-11-15T10:00:00Z','payload':{'id':'s','cwd':tmp}})+'\n'+json.dumps({'type':'event_msg','timestamp':'2026-11-15T10:01:00Z','payload':{'type':'user_message','message':'Original question'}})+'\n')
            before=indexer.parse_codex_file(str(p))
            with p.open('a') as f:
                for row in ({'type':'event_msg','payload':{'type':'thread_settings_applied'}},{'type':'thread_settings_applied','payload':{}}):
                    f.write(json.dumps(dict(row,timestamp='2026-11-16T12:00:00Z'))+'\n')
            self.assertEqual(before,indexer.parse_codex_file(str(p)))

    def test_discovery_skips_transcript_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'projects'/'workspace';project.mkdir(parents=True)
            real=project/'real.jsonl';real.write_text('{}\n');(project/'opened.jsonl').symlink_to(real)
            sessions=root/'sessions';sessions.mkdir();codex=sessions/'rollout-real.jsonl';codex.write_text('{}\n');(sessions/'rollout-link.jsonl').symlink_to(codex)
            calls=[]
            with patch.object(indexer,'CHATS',str(root/'chats')),patch.object(indexer,'INDEX',str(root/'index.json')),patch.object(indexer,'load_config',return_value={'min_user_msgs':0}),patch.object(indexer,'source_roots',return_value=[('mac',str(project.parent),str(sessions),str(root/'missing'))]),patch.object(indexer,'parse_unit',side_effect=lambda *a: calls.append(a) or None),patch.object(indexer.sys,'argv',['indexer.py']),contextlib.redirect_stdout(io.StringIO()):
                indexer.main()
            discovered=[p for call in calls for paths in call[2].values() for p in paths]
            self.assertEqual(set(discovered),{str(real),str(codex)})

    def test_owned_hardlinks_are_not_duplicate_sources_and_replacements_survive(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=root/'projects'/'A';b=root/'projects'/'B';a.mkdir(parents=True);b.mkdir()
            source=b/'session.jsonl';source.write_text('{}\n');alias=a/'session.jsonl';os.link(source,alias)
            inode=source.stat();shared=root/'shared';shared.mkdir()
            manifest={'version':1,'aliases':{str(alias):{'kind':'hardlink','source':str(source),'dev':inode.st_dev,'ino':inode.st_ino,'session_id':'session'}}}
            (shared/'session-aliases.json').write_text(json.dumps(manifest))
            def discover():
                calls=[]
                with patch.dict(os.environ,{'LIBRARY_OFFICIAL_SHARED':str(shared)}),patch.object(indexer,'CHATS',str(root/'chats')),patch.object(indexer,'INDEX',str(root/'index.json')),patch.object(indexer,'load_config',return_value={'min_user_msgs':0}),patch.object(indexer,'source_roots',return_value=[('mac',str(a.parent),'',str(root/'missing'))]),patch.object(indexer,'parse_unit',side_effect=lambda *args:calls.append(args) or None),patch.object(indexer.sys,'argv',['indexer.py']),contextlib.redirect_stdout(io.StringIO()):
                    indexer.main()
                return {p for call in calls for paths in call[2].values() for p in paths}
            self.assertEqual(discover(),{str(source)})
            unrelated=a/'unrelated.jsonl';os.link(source,unrelated)
            alias.unlink();alias.symlink_to(unrelated)
            self.assertEqual(discover(),{str(source),str(unrelated)})
            alias.unlink();unrelated.unlink();os.link(source,alias)
            # The provider may atomically replace its canonical transcript. The
            # recorded old alias is still stale and must not reappear as history.
            replacement=b/'replacement';replacement.write_text('{"new":true}\n');replacement.replace(source)
            self.assertEqual(discover(),{str(source)})
            # If a user replaces the alias, the old ownership record has no say.
            replacement=a/'replacement';replacement.write_text('{"other":true}\n');replacement.replace(alias)
            self.assertEqual(discover(),{str(source),str(alias)})

    def test_orphaned_owned_alias_preserves_last_transcript_copy(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.jsonl';source.write_text('{}\n');alias=root/'alias.jsonl';os.link(source,alias)
            info=source.stat();shared=root/'shared';shared.mkdir()
            (shared/'session-aliases.json').write_text(json.dumps({'version':1,'aliases':{str(alias):{'kind':'hardlink','source':str(source),'dev':info.st_dev,'ino':info.st_ino}}}))
            source.unlink()  # Synthetic fixture only: the alias is the last copy.
            with patch.dict(os.environ,{'LIBRARY_OFFICIAL_SHARED':str(shared)}):
                self.assertNotIn(str(alias),indexer.official_transcript_aliases())

if __name__=='__main__':unittest.main()
