import unittest
from unittest.mock import Mock
from attention_watch import AttentionWatch

class AttentionWatchTests(unittest.TestCase):
    def item(self,kind='approval',request='5',**payload):
        return {'run_id':'r','request_id':request,'kind':kind,'actionable':True,'payload':{'method':'item/commandExecution/requestApproval','availableDecisions':['accept','cancel'],**payload}}
    def test_explicit_auto_mode_approves_supported_tools_once_and_does_not_answer_questions(self):
        request=Mock(return_value={'ok':True});sound=Mock();watch=AttentionWatch(request,sound)
        items=[self.item(),self.item('question','6')]
        watch.process(items,{'auto_approve_tools':True,'attention_sound':True})
        watch.process(items,{'auto_approve_tools':True,'attention_sound':True})
        request.assert_called_once_with('/api/run/control',{'id':'r','action':'answer','request_id':'5','decision':'accept'})
        sound.assert_called_once()
    def test_manual_default_does_not_approve_and_chimes_once_for_new_request(self):
        request=Mock();sound=Mock();watch=AttentionWatch(request,sound)
        watch.process([self.item()],{'attention_sound':True});watch.process([self.item()],{'attention_sound':True})
        request.assert_not_called();sound.assert_called_once()
    def test_unknown_or_disallowed_decision_stays_for_user(self):
        request=Mock();sound=Mock();watch=AttentionWatch(request,sound)
        watch.process([self.item(method='unknown'),self.item(request='6',availableDecisions=['cancel'])],{'auto_approve_tools':True,'attention_sound':True})
        request.assert_not_called();sound.assert_called_once()
    def test_claude_approval_is_supported_but_question_tool_is_never_auto_answered(self):
        request=Mock(return_value={'ok':True});watch=AttentionWatch(request,Mock())
        items=[self.item(subtype='can_use_tool',method='',name='Bash'),self.item(request='7',subtype='can_use_tool',method='',name='AskUserQuestion')]
        watch.process(items,{'auto_approve_tools':True})
        self.assertEqual(request.call_count,1)
    def test_sound_can_be_disabled_and_failed_approval_remains_visible(self):
        request=Mock(side_effect=OSError('offline'));sound=Mock();watch=AttentionWatch(request,sound)
        watch.process([self.item()],{'auto_approve_tools':True,'attention_sound':False})
        sound.assert_not_called();self.assertEqual(len(watch.handled),0)
    def test_nonactionable_requests_are_ignored(self):
        item=self.item();item['actionable']=False
        request=Mock();sound=Mock();AttentionWatch(request,sound).process([item],{'auto_approve_tools':True,'attention_sound':True})
        request.assert_not_called();sound.assert_not_called()

class WatcherActiveTests(unittest.TestCase):
    def test_active_only_while_a_watcher_holds_the_lock(self):
        import fcntl, tempfile
        from pathlib import Path
        from attention_watch import active
        with tempfile.TemporaryDirectory() as data:
            self.assertFalse(active(data))
            with (Path(data)/'attention-watch.lock').open('a') as lease:
                self.assertFalse(active(data))
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(active(data))
                fcntl.flock(lease, fcntl.LOCK_UN)
            self.assertFalse(active(data))


class BannerTests(unittest.TestCase):
    def feed(self, *ids, state='open', needs=True):
        return [{'id': i, 'title': 't' + i, 'state': state, 'needs_answer': needs} for i in ids]
    def test_first_poll_is_silent_then_one_banner_per_new_item(self):
        from attention_watch import Banners
        post = Mock(); banners = Banners(post)
        banners.process(self.feed('a'), {})
        banners.process(self.feed('a', 'b'), {})
        banners.process(self.feed('a', 'b'), {})
        post.assert_called_once_with('tb', '', '')
    def test_only_items_that_need_an_answer_and_the_switch(self):
        from attention_watch import Banners
        post = Mock(); banners = Banners(post)
        banners.process([], {})
        banners.process(self.feed('x', needs=False) + self.feed('y', state='answered'), {})
        post.assert_not_called()
        banners.process(self.feed('z'), {'notification_banners': False})
        banners.process(self.feed('z'), {})
        post.assert_not_called()
    def test_environment_switch_disables_banners(self):
        import os
        from unittest.mock import patch
        from attention_watch import Banners
        post = Mock(); banners = Banners(post)
        banners.process([], {})
        with patch.dict(os.environ, {'CHAT_DASHBOARD_NO_BANNER': '1'}):
            banners.process(self.feed('n'), {})
        post.assert_not_called()
