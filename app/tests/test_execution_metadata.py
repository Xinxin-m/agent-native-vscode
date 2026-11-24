import unittest
from indexer import preserve_execution_metadata, meta_of

class MetadataTests(unittest.TestCase):
    def test_reparse_keeps_host_account_and_branch_identity(self):
        parsed={'key':'claude-id','id':'id','turns':[{'text':'new'}]}
        previous={'id':'wrong','account_id':'claude-personal','execution_host':'sc','conversation_key':'claude-parent'}
        preserve_execution_metadata(parsed, previous)
        self.assertEqual(parsed['id'],'id')
        self.assertEqual(parsed['turns'],[{'text':'new'}])
        self.assertEqual(parsed['execution_host'],'sc')
        self.assertEqual(parsed['account_id'],'claude-personal')
        self.assertEqual(parsed['conversation_key'],'claude-parent')
    def test_no_previous_metadata_is_valid(self):
        parsed={'id':'id'}
        preserve_execution_metadata(parsed,{})
        self.assertEqual(parsed,{'id':'id'})

class QuotaSeedTests(unittest.TestCase):
    def test_marks_only_exact_hash_bound_to_session(self):
        import hashlib
        from indexer import mark_quota_handoff_seed
        seed='generated exact seed'; digest=hashlib.sha256(seed.encode()).hexdigest()
        chat={'id':'s1','source':'claude','last_run_id':'dest','quota_failover_from':{'native_session_id':'s1','run_id':'r','prompt_sha256':digest},'turns':[{'role':'user','text':seed},{'role':'user','text':seed}]}
        mark_quota_handoff_seed(chat, {'id':'dest','session_id':'s1','provider':'claude','quota_failover_from':chat['quota_failover_from']})
        self.assertTrue(chat['turns'][0]['internal_handoff_seed'])
        self.assertNotIn('internal_handoff_seed',chat['turns'][1])
        untrusted={'id':'s1','source':'claude','last_run_id':'dest','quota_failover_from':chat['quota_failover_from'],'turns':[{'role':'user','text':seed}]}
        mark_quota_handoff_seed(untrusted, {'id':'wrong','session_id':'s1','provider':'claude','quota_failover_from':chat['quota_failover_from']})
        self.assertNotIn('internal_handoff_seed',untrusted['turns'][0])
        for sid, text in [('s2',seed),('s1',seed+' user text')]:
            other={'id':sid,'source':'claude','last_run_id':'dest','quota_failover_from':chat['quota_failover_from'],'turns':[{'role':'user','text':text}]}
            mark_quota_handoff_seed(other, {'id':'dest','session_id':'s1','provider':'claude','quota_failover_from':chat['quota_failover_from']})
            self.assertNotIn('internal_handoff_seed',other['turns'][0])

    def test_native_resume_does_not_mark_original_first_user_turn(self):
        import hashlib
        from indexer import mark_quota_handoff_seed
        seed='original user message'; provenance={'native_session_id':'s1','run_id':'r','mode':'native_resume','prompt_sha256':hashlib.sha256(seed.encode()).hexdigest()}
        chat={'id':'s1','source':'claude','last_run_id':'dest','quota_failover_from':provenance,'turns':[{'role':'user','text':seed}]}
        mark_quota_handoff_seed(chat, {'id':'dest','session_id':'s1','provider':'claude','quota_failover_from':provenance})
        self.assertNotIn('internal_handoff_seed',chat['turns'][0])
