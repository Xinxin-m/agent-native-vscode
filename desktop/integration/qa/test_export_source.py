"""Source-export contract checks. No providers, network or installed-app changes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('export_source', ROOT / 'integration/export_source.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class ExportTests(unittest.TestCase):
    def fixture(self, root):
        for name in exporter.PORTABLE_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        (root / 'docs/upstream.json').write_text(json.dumps({'commit': 'abc123'}))
        for name, value in {'integration/ui/src/main.ts':'export {}', 'integration/extension/main.cjs':'module.exports={}', 'integration/ui/node_modules/private.txt':'EXCLUDED', 'integration/ui/.env':'EXCLUDED', 'vscode/LICENSE.txt':'license', 'vscode/ThirdPartyNotices.txt':'notices', 'vscode/src/new.ts':'new native code'}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
    def git(self, args, **kwargs):
        self.assertEqual(args[0], 'git')
        if args[1:3] == ['diff', '--name-only']:
            return b'build/gulpfile.vscode.ts\nsrc/main.ts\nproduct.json\n'
        if args[1:3] == ['diff', '--binary']:
            self.assertEqual(args[3], 'abc123')
            self.assertIn('build/gulpfile.vscode.ts', args)
            return b'portable upstream patch'
        if args[1] == 'ls-files':
            return b'src/new.ts\0'
        raise AssertionError(args)
    def test_complete_dependency_closure_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);self.fixture(root)
            with patch.object(exporter.subprocess, 'check_output', side_effect=self.git):
                values = exporter.collect(root)
            for name in exporter.PORTABLE_FILES:
                self.assertIn(name, values)
            self.assertEqual(values['native-additions/src/new.ts'], b'new native code')
            self.assertFalse(any('node_modules' in name or name.endswith('.env') for name in values))
            manifest = json.loads(values['manifest.json'])
            self.assertEqual(manifest, {name:hashlib.sha256(value).hexdigest() for name,value in values.items() if name!='manifest.json'})
    def test_archive_extract_imports_installer_without_local_toolchain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();self.fixture(root)
            archive=Path(tmp)/'source.tar.gz'
            with patch.object(exporter.subprocess, 'check_output', side_effect=self.git):
                exporter.export(root, archive)
            extracted=Path(tmp)/'extracted';extracted.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(extracted, filter='data')
            result=subprocess.run([sys.executable,str(extracted/'integration/install.py'),'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--expected-sha256',result.stdout)
    def test_rejects_unreviewed_upstream_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with patch.object(exporter.subprocess,'check_output',return_value=b'package.json\n'):
                with self.assertRaisesRegex(ValueError,'outside the export allowlist'):
                    exporter.collect(root)
    def test_rejects_symlink_and_private_home_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            link=root/'integration/ui/src/link.ts';link.symlink_to('main.ts')
            with self.assertRaisesRegex(ValueError,'symlink'):exporter.collect(root)
            link.unlink();(root/'integration/ui/src/main.ts').write_text(str(Path.home())+'/private')
            with patch.object(exporter.subprocess,'check_output',side_effect=self.git):
                with self.assertRaisesRegex(ValueError,'Private home path'):exporter.collect(root)
    def test_rejects_existing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'finished.tar.gz';target.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError,'overwrite'):exporter.export(Path(tmp),target)
            self.assertEqual(target.read_bytes(),b'keep')
    def test_archive_bytes_are_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();self.fixture(root)
            a,b=Path(tmp)/'a.tar.gz',Path(tmp)/'b.tar.gz'
            with patch.object(exporter.subprocess,'check_output',side_effect=self.git):
                exporter.export(root,a);exporter.export(root,b)
            self.assertEqual(a.read_bytes(),b.read_bytes())

if __name__ == '__main__':unittest.main()
