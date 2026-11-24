"""Read-only native settings and account namespace regressions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import conversation_settings as settings

class SettingsTests(unittest.TestCase):
    def test_codex_session_context_overrides_config_and_creator_is_not_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config.toml').write_text('model="configured"\nmodel_reasoning_effort="high"\n')
            transcript=root/'rollout.jsonl'; transcript.write_text(json.dumps({'type':'turn_context','payload':{'model':'actual','effort':'xhigh'}})+'\n')
            meta={'key':'codex-a','id':'a','source':'codex','cwd':tmp,'file':str(transcript),'provider_account_id':'native-uuid'}
            with patch.object(settings.host_connector,'resolve_local_account',return_value={'account_id':'codex-default'}) as resolve, patch.object(settings.host_connector,'local_profile',return_value=('codex-default',tmp,{})):
                value=settings.local_defaults('codex',meta=meta)
            self.assertIsNone(resolve.call_args.kwargs.get('account_id'))
            self.assertEqual(value['account'],'codex-default');self.assertEqual(value['model'],'actual');self.assertEqual(value['effort'],'xhigh')

    def test_picker_passes_untrusted_path_as_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(settings.sys,'platform','darwin'),patch.object(settings.subprocess,'run') as run:
                run.return_value.returncode=0;run.return_value.stdout=tmp+'\n';run.return_value.stderr=''
                value=settings.choose_folder(tmp)
                self.assertEqual(value['path'],os.path.realpath(tmp))
                self.assertEqual(run.call_args.args[0][-1],tmp)
                self.assertNotIn(tmp,run.call_args.kwargs['input'])


class ForkTests(unittest.TestCase):
    def test_explicit_handoff_uses_destination_folder_and_excludes_future_context(self):
        import broker
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'destination';folder.mkdir()
            meta={'key':'codex-root','source':'codex','id':'root','cwd':tmp,'node':'mac','account_id':'codex-owner'}
            chat={'turns':[{'role':'user','text':'before'},{'role':'assistant','text':'selected'},{'role':'assistant','text':'future-secret'}]}
            spec=broker.prepare_run({'key':meta['key'],'provider':'codex','message':'Continue','handoff':True,'through_turn':1,'new':{'cwd':str(folder),'host_id':'mac'}},{meta['key']:meta},lambda key:chat,lambda key:'future-tool-secret')
            self.assertEqual(spec['cwd'],str(folder));self.assertEqual(spec['session_id'],'');self.assertEqual(spec['source_turn'],1)
            self.assertIn('selected',spec['prompt']);self.assertNotIn('future-secret',spec['prompt']);self.assertNotIn('future-tool-secret',spec['prompt'])
            normal=broker.prepare_run({'key':meta['key'],'message':'Continue','new':{'cwd':str(folder)}},{meta['key']:meta},lambda key:chat)
            self.assertEqual(normal['cwd'],tmp);self.assertEqual(normal['account_id'],'codex-owner')

    def test_explicit_host_change_is_a_new_session(self):
        import broker
        meta={'key':'codex-root','source':'codex','id':'root','cwd':'/old','node':'remote-a'}
        spec=broker.prepare_run({'key':meta['key'],'message':'Continue','handoff':True,'new':{'host_id':'remote-b','cwd':'/new'}},{meta['key']:meta},lambda key:{'turns':[]})
        self.assertEqual(spec['host_id'],'remote-b');self.assertEqual(spec['cwd'],'/new');self.assertEqual(spec['session_id'],'')

    def test_tree_message_availability_requires_verified_native_ownership(self):
        import session_lineage
        import host_connector
        meta={'source':'codex','id':'child','cwd':'/work','node':'mac'}
        with patch.object(host_connector,'resolve_local_account',return_value={'account_id':'codex-default'}):
            self.assertTrue(session_lineage.resume_availability(meta)['native_resumable'])
        with patch.object(host_connector,'resolve_local_account',side_effect=ValueError('missing')):
            self.assertFalse(session_lineage.resume_availability(meta)['native_resumable'])


class AmbiguousAccountTests(unittest.TestCase):
    def test_remote_defaults_carry_no_informational_notice(self):
        meta={'key':'claude-r','id':'r','source':'claude','cwd':'/work','execution_host':'cluster','account_id':'claude-default'}
        with patch.object(settings,'_json',return_value={'claude-r':meta}):
            value=settings.defaults(None,{'key':'claude-r'},{})
        self.assertNotIn('notice',value)
        self.assertEqual(value['defaults']['host_id'],'cluster');self.assertEqual(value['defaults']['account'],'claude-default')

    def test_ambiguous_session_account_is_a_notice_not_an_error(self):
        import host_connector
        meta={'key':'claude-a','id':'a','source':'claude','cwd':'/work'}
        calls=[]
        def resolve(provider,**kw):
            calls.append(kw)
            if kw.get('session_id'):raise host_connector.AmbiguousSessionAccount('Session account is missing or ambiguous; choose its original account')
            return {'account_id':'claude-default'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings,'_json',return_value={'claude-a':meta}), patch.object(settings,'DATA',Path(tmp)/'data'), \
             patch.object(host_connector,'resolve_local_account',side_effect=resolve), patch.object(host_connector,'local_profile',return_value=('claude-default',tmp,{})):
            value=settings.defaults(None,{'key':'claude-a'},{})
        self.assertIsInstance(value,dict)
        self.assertNotIn('notice',value)
        self.assertEqual(value['defaults']['account'],'');self.assertEqual(value['defaults']['selectedAccount'],'')
        self.assertTrue(value['defaults']['account_required']);self.assertEqual(value['defaults']['cwd'],'/work')
        self.assertIsNone(calls[-1].get('session_id'))

    def run_defaults(self,meta,resolve,tmp):
        import host_connector
        with patch.object(settings,'_json',return_value={meta['key']:meta}), patch.object(settings,'DATA',Path(tmp)/'data'), \
             patch.object(host_connector,'resolve_local_account',side_effect=resolve), patch.object(host_connector,'local_profile',side_effect=lambda p,a=None:(a or 'codex-default',tmp,{})):
            return settings.defaults(None,{'key':meta['key']},{})

    def test_stale_binding_resumes_on_a_profile_that_can_read_the_transcript(self):
        import host_connector, notices
        meta={'key':'codex-s','id':'s','source':'codex','cwd':'/work','account_id':'codex-default'}
        def resolve(provider,**kw):
            if kw.get('account_id'):
                error=host_connector.SessionAccountMismatch('Session does not exist in the selected host, account and workspace');error.candidates=['codex-personal'];raise error
            return {'account_id':'codex-personal'}
        with tempfile.TemporaryDirectory() as tmp:
            value=self.run_defaults(meta,resolve,tmp)
            self.assertFalse((Path(tmp)/'data/notices.json').exists())
        self.assertIsInstance(value,dict);self.assertNotIn('notice',value)
        self.assertEqual(value['defaults']['account'],'codex-personal')
        self.assertEqual(value['defaults']['account_selection']['requested_account_id'],'codex-default')

    def test_unreadable_transcript_is_told_in_the_chat_not_the_notification_center(self):
        import host_connector
        meta={'key':'codex-u','id':'u','source':'codex','cwd':'/work','account_id':'codex-default','title_seed':'Lost run'}
        def resolve(provider,**kw):
            if kw.get('session_id'):
                error=host_connector.SessionAccountMismatch('missing');error.candidates=[];raise error
            return {'account_id':'codex-default'}
        with tempfile.TemporaryDirectory() as tmp:
            first=self.run_defaults(meta,resolve,tmp);self.run_defaults(meta,resolve,tmp)
            child=self.run_defaults(dict(meta,key='codex-u-child',is_child=True),resolve,tmp)
            self.assertFalse((Path(tmp)/'data/notices.json').exists())
        self.assertIn('cannot be resumed',first['notice']);self.assertNotIn('notice',child)
        self.assertTrue(first['defaults']['account_required'])
        self.assertEqual(first['defaults']['account_candidates'],[])

    def test_different_logins_without_an_active_candidate_stay_quiet(self):
        import host_connector, notices
        meta={'key':'claude-a','id':'a','source':'claude','cwd':'/work','title_seed':'Fix the build'}
        def resolve(provider,**kw):
            if kw.get('session_id'):
                error=host_connector.AmbiguousSessionAccount('ambiguous');error.candidates=['claude-default','claude-other'];raise error
            return {'account_id':'claude-default'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings,'_json',return_value={'claude-a':meta}), patch.object(settings,'DATA',Path(tmp)/'data'), \
             patch.object(host_connector,'resolve_local_account',side_effect=resolve), patch.object(host_connector,'local_profile',return_value=('claude-default',tmp,{})):
            first=settings.defaults(None,{'key':'claude-a'},{})
            self.assertFalse((Path(tmp)/'data/notices.json').exists())
        self.assertNotIn('notice',first);self.assertTrue(first['defaults']['account_required'])
        self.assertEqual(first['defaults']['account_candidates'],['claude-default','claude-other'])
