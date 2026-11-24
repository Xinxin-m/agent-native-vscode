import datetime as dt
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pipeline
import enrich
import label_model
import project_suggestions as suggestions
from projects import CatalogError


class SuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.projects = {'projects': [{'id':'a','name':'Alpha'}, {'id':'b','name':'Beta'}], 'assignments': {'chat':'a'}}
        for name,value in {'projects':self.projects, 'index':{'chat':{}}, 'project_suggestions':{'chat':{'from':'a','to':'b','title':'Chat'}}}.items():
            suggestions.save(self.data / (name+'.json'), value)

    def test_accept_is_manual_and_snapshots_original(self):
        self.assertEqual(suggestions.decide({'action':'accept','key':'chat','to':'b'},self.data)['suggestions'], [])
        current=suggestions.load(self.data/'projects.json', {})
        self.assertEqual(current['assignments']['chat'], 'b')
        self.assertIn('chat', current['manual_assignments'])
        self.assertEqual(len(list((self.data/'project-snapshots').glob('*.json'))),1)

    def test_rejected_pair_stays_suppressed_after_new_label(self):
        suggestions.decide({'action':'reject','key':'chat','to':'b'},self.data)
        suggestions.merge(self.data/'project_suggestions.json', {'chat':{}}, self.projects, {'chat':{'from':'a','to':'b','title':'New title','at':'later'}})
        self.assertEqual(suggestions.listing(self.data)['suggestions'], [])
        self.assertEqual(suggestions.load(self.data/'projects.json',{}),self.projects)
        self.assertEqual(len(suggestions.load(self.data/'project_suggestion_rejections.json',{})),1)

    def test_stale_assignment_rejected_without_overwrite(self):
        self.projects['assignments']['chat']='c';suggestions.save(self.data/'projects.json',self.projects)
        with self.assertRaises(CatalogError): suggestions.decide({'action':'accept','key':'chat','to':'b'},self.data)
        self.assertEqual(suggestions.load(self.data/'projects.json',{}),self.projects)

    def test_refuses_decision_during_enrichment(self):
        with (self.data/'refresh.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(CatalogError,'Labelling is running'):
                suggestions.decide({'action':'reject','key':'chat','to':'b'},self.data)
        self.assertEqual(len(suggestions.listing(self.data)['suggestions']),1)

    def test_get_uses_current_names_and_manual_title(self):
        suggestions.save(self.data/'chat_flags.json',{'titles':{'chat':'Mine'}})
        row=suggestions.listing(self.data)['suggestions'][0]
        self.assertEqual((row['from_name'],row['to_name'],row['title']),('Alpha','Beta','Mine'))


class DailyRetryTests(unittest.TestCase):
    def run_retry(self, start, until, outcomes, cfg=None, daily=True):
        clock=[start]; waits=[]
        def sleep(seconds): waits.append(seconds);clock[0]+=seconds
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'enrich_backoff.json').write_text(json.dumps({'until':until}))
            with patch.object(pipeline,'DATA',tmp),patch.object(pipeline,'config',return_value=cfg or {}),patch.object(pipeline,'refresh',side_effect=outcomes) as refresh:
                result=pipeline.refresh_daily_with_retry(clock=lambda:clock[0],sleep=sleep,daily=daily)
                return result,refresh.call_count,waits

    def test_unavailable_retries_same_day_after_backoff(self):
        start=dt.datetime(2026,11,16,4,45).timestamp()
        result,calls,waits=self.run_retry(start,start+1800,[False,True])
        self.assertTrue(result);self.assertEqual(calls,2);self.assertEqual(sum(waits),1800);self.assertLessEqual(max(waits),60)

    def test_no_retry_tomorrow_or_when_disabled_or_for_hook(self):
        start=dt.datetime(2026,11,16,23,50).timestamp()
        self.assertEqual(self.run_retry(start,start+1800,[False])[:2],(False,1))
        start=dt.datetime(2026,11,16,4,45).timestamp()
        self.assertEqual(self.run_retry(start,start+1800,[False],{'enable_model_labeling':False})[:2],(False,1))
        self.assertEqual(self.run_retry(start,start+1800,[False],daily=False)[:2],(False,1))

    def test_enrich_paused_status_and_pipeline_failure_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(enrich, 'INDEX', str(Path(tmp,'index.json'))), patch.object(enrich, 'OUT', str(Path(tmp,'enrich.json'))), patch.object(enrich, '_config', return_value={}), patch.object(label_model, 'paused', return_value=True):
                self.assertEqual(enrich.main(['--daily']), 75)
            with patch.object(pipeline, 'LOG_PATH', str(Path(tmp,'log'))), patch.object(pipeline, 'run_stage', side_effect=[True, False, True, True]):
                self.assertFalse(pipeline._run_pass({}, 'daily', False, False, False, False, True))

    def test_expired_backoff_does_not_busy_retry(self):
        start=dt.datetime(2026,11,16,4,45).timestamp()
        self.assertEqual(self.run_retry(start,start-1,[False])[:2],(False,1))
