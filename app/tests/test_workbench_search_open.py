import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

import search_api
import open_destinations
import file_service


class SearchOpenTests(unittest.TestCase):
    def test_full_turn_search_filters_and_snippets(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            (data/'chats').mkdir()
            (data/'index.json').write_text(json.dumps({'claude-one':{'cwd':'/work','node':'mac','account_id':'a'}, 'codex-two':{'cwd':'/hidden','node':'sc'}}))
            (data/'projects.json').write_text(json.dumps({'assignments':{'claude-one':'p'}}))
            (data/'config.json').write_text(json.dumps({'excluded_cwds':['/hidden']}))
            for key in ('claude-one','codex-two'):
                (data/'chats'/(key+'.json')).write_text(json.dumps({'turns':[{'text':'unrelated'},{'text':'deep NEEDLE result'}]}))
            value=search_api.search(data,{'q':'needle','project_id':'p','account_id':'a'})
            self.assertEqual(len(value['results']),1)
            self.assertEqual(value['results'][0]['turn_index'],1)
            self.assertIn('NEEDLE',value['results'][0]['snippet'])
            self.assertEqual(search_api.search(data,{'q':'needle','account_id':'unknown'})['results'],[])

    def test_search_carries_workspace_provenance_when_navigation_metadata_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory); (data/'chats').mkdir()
            (data/'index.json').write_text(json.dumps({'remote':{'cwd':'/sc/Research','node':'mac+sc','execution_host':'sc'}}))
            (data/'chats/remote.json').write_text(json.dumps({'turns':[{'text':'needle'}]}))
            result=search_api.search(data,{'q':'needle'})['results'][0]
            self.assertEqual(result['cwd'],'/sc/Research')
            self.assertEqual(result['execution_host'],'sc')
            self.assertEqual(result['node'],'mac+sc')

    def test_search_returns_unique_root_chats_across_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            (data/'chats').mkdir()
            index={'root':{'ts_last':'3'}, 'child':{'ts_last':'2','root_key':'root'}, 'other':{'ts_last':'1'}}
            (data/'index.json').write_text(json.dumps(index))
            for key in index:
                (data/'chats'/(key+'.json')).write_text(json.dumps({'turns':[{'text':'needle first'},{'text':'needle again'}]}))
            first=search_api.search(data,{'q':'needle','limit':1})
            second=search_api.search(data,{'q':'needle','limit':1,'cursor':first['next_cursor']})
            self.assertEqual([r['key'] for r in first['results']+second['results']],['root','other'])
            self.assertIsNone(second['next_cursor'])

    def test_search_never_promotes_child_only_match_into_root(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory); (data/'chats').mkdir()
            index={'root':{},'child':{'root_key':'root','is_child':True},'orphan':{'is_child':True}}
            (data/'index.json').write_text(json.dumps(index))
            for key in index:
                (data/'chats'/(key+'.json')).write_text(json.dumps({'turns':[{'text':'parent text' if key=='root' else 'needle'}]}))
            self.assertEqual(search_api.search(data,{'q':'needle'})['results'],[])

    def test_explicit_open_argv_and_remote_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'a $(danger).pdf'
            path.write_bytes(b'%PDF')
            service=file_service.FileService(root/'data',default_root=None)
            rid=service.register('mac',str(root),'read')['id']
            with patch.object(open_destinations.fs,'instance',return_value=service), patch.object(open_destinations.subprocess,'run') as run:
                open_destinations.open_resource({'root_id':rid,'path':path.name},'default_browser')
                argv=run.call_args.args[0]
                self.assertEqual(argv[0],'open')
                self.assertIn(str(path.resolve()),argv)
                self.assertNotIn('code',argv)
                with self.assertRaises(file_service.FileError):
                    open_destinations.open_resource({'host_id':'sc','path':str(path)},'system')
