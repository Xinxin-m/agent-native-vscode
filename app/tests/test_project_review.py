import copy
import datetime as dt
import unittest
from project_review import audit, descendants

NOW=dt.datetime(2026,11,15,tzinfo=dt.timezone.utc)

class ReviewTests(unittest.TestCase):
    def test_rechecks_assigned_purpose_without_mutating_manual(self):
        projects={'projects':[{'id':'old','name':'Old'}],'assignments':{'a':'old'},'manual_assignments':['a']}
        original=copy.deepcopy(projects)
        result=audit({'a':{}},{'a':{'title':'Application essay'}},projects,{'classification_rules':[{'project_id':'new','any':['essay']}]},NOW)
        self.assertEqual(result['items'][0]['suggested'],'new')
        self.assertTrue(result['items'][0]['manual'])
        self.assertEqual(projects,original)

    def test_recent_assigned_chats_need_periodic_purpose_review(self):
        index={'a':{'ts_last':'2026-11-14T00:00:00Z'}}
        projects={'projects':[],'assignments':{'a':'known'}}
        report=audit(index,{},projects,{},NOW)
        self.assertEqual(report['items'][0]['kind'],'purpose_review')
        again=audit(index,{},projects,{},NOW,previous=report)
        self.assertEqual(len(again['items']),1)
        projects['assignments']['a']='new'
        self.assertEqual(audit(index,{},projects,{},NOW,previous=again)['items'],[])

    def test_rules_exclusions_automation_children_and_conflicts(self):
        cfg={'classification_rules':[{'project_id':'a','any':['essay'],'exclude':['interview']},{'project_id':'b','all':['essay']}]}
        report=audit({'child':{'is_child':True},'human':{}},{'human':{'title':'essay'}},{'projects':[],'assignments':{}},cfg,NOW)
        self.assertEqual(len(report['items']),1)
        self.assertEqual(report['items'][0]['kind'],'rule_conflict')

    def test_hierarchy_bounded_cycle(self):
        self.assertEqual(descendants([{'id':'child','parent_id':'parent'},{'id':'parent','parent_id':'child'}],'parent'),{'parent','child'})

    def test_structure_review_suggests_flattening_and_completion_without_mutation(self):
        projects={'projects':[{'id':'parent','name':'Parent'},{'id':'child','name':'Child','parent_id':'parent'},{'id':'empty','name':'Empty'}],'assignments':{'chat':'child'}}
        before=copy.deepcopy(projects)
        result=audit({'chat':{}},{},projects,{},NOW,flags={'archived':['chat']})
        found={(row['project_id'],row['kind']) for row in result['structure_suggestions']}
        self.assertEqual(found,{('parent','single_child_parent'),('empty','empty_project'),('child','completed_project')})
        self.assertEqual(projects,before)
