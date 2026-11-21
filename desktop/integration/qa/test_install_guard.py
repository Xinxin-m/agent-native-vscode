import importlib.util, json, tempfile, unittest, shutil
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('guarded_installer',Path(__file__).parents[1]/'install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerGuardTests(unittest.TestCase):
 def test_candidate_hash_covers_content_and_symlink_targets(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); (root/'a').write_text('one'); (root/'link').symlink_to('a')
   first=installer.bundle_hash(root); (root/'a').write_text('two'); self.assertNotEqual(first,installer.bundle_hash(root))
   (root/'a').write_text('one'); (root/'link').unlink(); (root/'link').symlink_to('elsewhere');self.assertNotEqual(first,installer.bundle_hash(root))
 def test_dirty_or_external_child_blocks_close(self):
  snapshot={'workbench':{'groups':[{'tabs':[{'dirty':True}]}]}}
  with self.assertRaises(installer.GuardError): installer.check_snapshot(snapshot)
  with self.assertRaises(installer.GuardError): installer.check_children(1,{1:{'parent':0,'command':'/app/main'},2:{'parent':1,'command':'/bin/zsh'}},Path('/app'))
 def test_candidate_identity_or_extension_mismatch_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   candidate=Path(d);(candidate/'Contents/Resources/app/extensions/library-code').mkdir(parents=True)
   (candidate/'Contents/Resources/app/extensions/library-code/build-info.json').write_text(json.dumps({'sourceHash':'other'}))
   with patch.object(installer,'bad_checksums',return_value=[]),patch.object(installer,'verify_signature'),patch.object(installer,'bundle_hash',return_value='wanted'):
    with self.assertRaises(installer.GuardError): installer.validate_candidate(candidate,'wanted','expected')
    with self.assertRaises(installer.GuardError): installer.validate_candidate(candidate,'wrong','other')
 def test_default_check_is_read_only_and_no_copy_or_close(self):
  with patch.object(installer,'check_report',return_value={'ok':True}) as report,patch.object(installer,'install') as install:
   self.assertEqual(installer.main([]),0);report.assert_called_once();install.assert_not_called()
 def test_install_requires_explicit_hashes(self):
  with self.assertRaises(SystemExit): installer.main(['--install'])

 def test_official_first_install_never_captures_daily_state(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);candidate=base/'candidate.app';candidate.mkdir();(candidate/'asset').write_text('candidate');app=base/'Official.app'
   def fake_run(args):
    if args[0]=='ditto': shutil.copytree(args[1],args[2]);return ''
    raise AssertionError('Unexpected external action: '+str(args))
   with patch.object(installer,'validate_candidate'),patch.object(installer,'processes',return_value={}),patch.object(installer,'snapshot') as snap,patch.object(installer,'draft_capture') as drafts,patch.object(installer,'run',side_effect=fake_run):
    value=installer.install(candidate,installer.bundle_hash(candidate),'ext',app=app,bundle_id='local.library.code.official')
    self.assertTrue(value['ok']);snap.assert_not_called();drafts.assert_not_called();self.assertEqual((app/'asset').read_text(),'candidate')
 def test_running_official_refuses_without_reading_daily_state(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);candidate=base/'candidate.app';candidate.mkdir();(candidate/'asset').write_text('candidate');app=base/'Official.app';app.mkdir();(app/'asset').write_text('old')
   rows={42:{'parent':1,'started':'identity','command':str(app/'Contents/MacOS/Library Code')}}
   def fake_run(args):
    if args[0]=='ditto': shutil.copytree(args[1],args[2]);return ''
    raise AssertionError('Unexpected action: '+str(args))
   with patch.object(installer,'validate_candidate'),patch.object(installer,'check_report',return_value={'ok':True}),patch.object(installer,'processes',return_value=rows),patch.object(installer,'snapshot') as snap,patch.object(installer,'draft_capture') as drafts,patch.object(installer,'run',side_effect=fake_run):
    with self.assertRaises(installer.GuardError): installer.install(candidate,installer.bundle_hash(candidate),'ext',True,app,'local.library.code.official')
    snap.assert_not_called();drafts.assert_not_called();self.assertEqual((app/'asset').read_text(),'old')
 def test_pre_swap_failure_after_close_reopens_original(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);candidate=base/'candidate.app';candidate.mkdir();(candidate/'asset').write_text('new');app=base/'Library Code.app';app.mkdir();(app/'asset').write_text('old')
   pid=42;alive={'value':True};calls=[];executable=str(app/'Contents/MacOS/Library Code');rows={pid:{'parent':1,'started':'identity','command':executable}}
   def proc():return rows if alive['value'] else {}
   def fake_run(args):
    calls.append([str(x) for x in args])
    if args[0]=='ditto':shutil.copytree(args[1],args[2]);return ''
    if args[0]=='ps':return executable+' --remote-debugging-port=19342'
    if args[0]=='lsof':return '42\n'
    if args[0]==installer.NODE and args[1]=='-e':alive['value']=False;return ''
    if args[0]=='open':return ''
    raise AssertionError('Unexpected external action')
   def drafts(receipt,mode):
    if mode=='check':raise installer.GuardError('fixture persistence failed')
   state={'workbench':{'groups':[{'tabs':[{'dirty':False}]}]},'activeRuns':[],'views':[]}
   with patch.object(installer,'validate_candidate'),patch.object(installer,'check_report',return_value={'ok':True}),patch.object(installer,'processes',side_effect=proc),patch.object(installer,'snapshot',return_value=state),patch.object(installer,'draft_capture',side_effect=drafts),patch.object(installer,'run',side_effect=fake_run):
    with self.assertRaises(installer.GuardError):installer.install(candidate,installer.bundle_hash(candidate),'ext',True,app,'local.library.code')
    self.assertEqual((app/'asset').read_text(),'old');self.assertTrue(any(x[0]=='open' for x in calls));self.assertTrue(any(x[0]==str(installer.NODE) and x[1]=='-e' for x in calls));self.assertFalse(any(x[0]=='osascript' for x in calls))


 def test_running_custom_profile_refuses_before_daily_capture(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);candidate=base/'candidate.app';candidate.mkdir();(candidate/'asset').write_text('new');app=base/'Library Code.app';app.mkdir();(app/'asset').write_text('old')
   executable=str(app/'Contents/MacOS/Library Code');rows={42:{'parent':1,'started':'id','command':executable}}
   def fake_run(args):
    if args[0]=='ditto':shutil.copytree(args[1],args[2]);return ''
    if args[0]=='ps':return executable+' --remote-debugging-port=19342 --user-data-dir=/other/profile'
    raise AssertionError('Unexpected action')
   with patch.object(installer,'validate_candidate'),patch.object(installer,'check_report',return_value={'ok':True}),patch.object(installer,'processes',return_value=rows),patch.object(installer,'snapshot') as snap,patch.object(installer,'draft_capture') as drafts,patch.object(installer,'run',side_effect=fake_run):
    with self.assertRaises(installer.GuardError):installer.install(candidate,installer.bundle_hash(candidate),'ext',True,app)
    snap.assert_not_called();drafts.assert_not_called();self.assertEqual((app/'asset').read_text(),'old')
 def test_replacement_validation_failure_restores_and_reopens_original(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);candidate=base/'candidate.app';candidate.mkdir();(candidate/'asset').write_text('new');app=base/'Library Code.app';app.mkdir();(app/'asset').write_text('old')
   alive={'value':True};calls=[];executable=str(app/'Contents/MacOS/Library Code');rows={42:{'parent':1,'started':'id','command':executable}}
   def fake_run(args):
    calls.append([str(x) for x in args])
    if args[0]=='ditto':shutil.copytree(args[1],args[2]);return ''
    if args[0]=='ps':return executable+' --remote-debugging-port=19342'
    if args[0]=='lsof':return '42\n'
    if args[0]==installer.NODE and args[1]=='-e':alive['value']=False;return ''
    if args[0]=='open':return ''
    raise AssertionError('Unexpected action')
   def validate(path,*args):
    if Path(path)==app:raise installer.GuardError('fixture signature failure')
   state={'workbench':{'groups':[{'tabs':[{'dirty':False}]}]},'activeRuns':[],'views':[]}
   with patch.object(installer,'validate_candidate',side_effect=validate),patch.object(installer,'check_report',return_value={'ok':True}),patch.object(installer,'processes',side_effect=lambda:rows if alive['value'] else {}),patch.object(installer,'snapshot',return_value=state),patch.object(installer,'draft_capture'),patch.object(installer,'run',side_effect=fake_run):
    with self.assertRaises(installer.GuardError):installer.install(candidate,installer.bundle_hash(candidate),'ext',True,app)
    self.assertEqual((app/'asset').read_text(),'old');self.assertTrue(any(x[0]=='open' for x in calls));self.assertTrue(any(x[0]==str(installer.NODE) and x[1]=='-e' for x in calls));self.assertFalse(any(x[0]=='osascript' for x in calls))


 def test_debug_endpoint_must_belong_to_client_process_tree(self):
  with patch.object(installer,'run',return_value='999\n'):
   with self.assertRaises(installer.GuardError):installer.check_debug_endpoint({42:('id','client')})
  with patch.object(installer,'run',return_value='42\n'):
   installer.check_debug_endpoint({42:('id','client')})


 def test_close_uses_verified_cdp_endpoint_and_no_bundle_quit(self):
  with patch.object(installer,'run') as run:
   installer.close_daily_client()
   argv=run.call_args.args[0]
   self.assertEqual(argv[:2],[installer.NODE,'-e'])
   self.assertIn('connect(19342)',argv[2]);self.assertIn("send('Browser.close')",argv[2])
   self.assertIn('closed|disconnected',argv[2]);self.assertNotIn('osascript',argv[2])


 def test_restore_waits_for_groups_tabs_and_views_using_unique_snapshots(self):
  before={'workbench':{'groups':[{'tabs':[{'name':'one'}]}]},'views':[{'kind':'chat'}],'activeRuns':[]}
  incomplete={'workbench':{'groups':[]},'views':[],'activeRuns':[]}
  with patch.object(installer,'snapshot',side_effect=[incomplete,before]) as snap,patch.object(installer.time,'sleep'):
   self.assertEqual(installer.wait_restored(Path('/fixture'),before,attempts=3),before)
   self.assertEqual([c.args[1] for c in snap.call_args_list],['after-000','after-001'])
 def test_restore_timeout_reports_pending_without_claiming_success(self):
  before={'workbench':{'groups':[{'tabs':[{'name':'one'}]}]},'views':[{'kind':'chat'}],'activeRuns':[]}
  with patch.object(installer,'snapshot',return_value={'workbench':{'groups':[]},'views':[],'activeRuns':[]}),patch.object(installer.time,'sleep'):
   with self.assertRaisesRegex(installer.GuardError,'pending'):installer.wait_restored(Path('/fixture'),before,attempts=2)

if __name__=='__main__': unittest.main()
