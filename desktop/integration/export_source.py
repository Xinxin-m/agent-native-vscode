#!/usr/bin/env python3
"""Export the portable source allowlist; exclude private evidence and user data."""
from pathlib import Path
import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile

PORTABLE_FILES = (
    'README.md', 'docs/upstream.json', 'docs/build-and-update.md',
    'integration/build.py', 'integration/launch.sh', 'integration/export_source.py',
    'integration/install.py', 'integration/variants.py',
    'integration/official-sessions.py', 'integration/auth_failure.py',
    'integration/backend/web/settings-insights.js', 'integration/backend/web/settings-insights.css',
    'integration/qa/webviews.cjs', 'integration/qa/delivery-state.cjs',
    'integration/qa/draft-preservation.py', 'integration/qa/test_install_guard.py',
    'integration/qa/test_export_source.py', 'integration/qa/agent-map-timeout.cjs',
)
UPSTREAM_PATHS = ('src', 'product.json', 'build/gulpfile.vscode.ts')
EXCLUDED = {'node_modules', 'dist', '.git', 'test-results', 'playwright-report', '__pycache__'}

def collect(root):
    root = Path(root)
    entries = {}
    input_hashes = {}
    def add(path, name=None):
        if path.is_symlink():
            raise ValueError('Unexpected symlink: ' + str(path))
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError('Source changed while exporting: ' + str(path))
        entries[name or path.relative_to(root).as_posix()] = content
        input_hashes[path] = hashlib.sha256(content).digest()
    for name in PORTABLE_FILES:
        add(root / name)
    media = root / 'integration/extension/media'
    chunks = {p.name: p for p in media.glob('*.js') if re.search(r'-[A-Za-z0-9_-]{8}\.js$', p.name)}
    reachable = set()
    queue = [p for p in media.glob('*.js') if p.name not in chunks]
    while queue:
        entry = queue.pop()
        for name in re.findall(r'[A-Za-z0-9_.-]+\.js', entry.read_text()):
            if name in chunks and name not in reachable:
                reachable.add(name)
                queue.append(chunks[name])
    for base in ('integration/ui', 'integration/extension'):
        for path in sorted((root / base).rglob('*')):
            rel = path.relative_to(root / base)
            if any(part in EXCLUDED or part.startswith('.') for part in rel.parts):
                continue
            if path.is_symlink():
                raise ValueError('Unexpected symlink: ' + str(path))
            if path.is_file():
                if path.parent == media and path.name in chunks and path.name not in reachable:
                    continue
                add(path)
    repo = root / 'vscode'
    base = json.loads(entries['docs/upstream.json'])['commit']
    git = lambda *args: subprocess.check_output(['git', *args], cwd=repo)
    changed = git('diff', '--name-only', base, '--').decode().splitlines()
    unsupported = [name for name in changed if not any(name == scope or name.startswith(scope + '/') for scope in UPSTREAM_PATHS)]
    if unsupported:
        raise ValueError('Review upstream changes outside the export allowlist: ' + ', '.join(unsupported))
    patch = git('diff', '--binary', base, '--', *UPSTREAM_PATHS)
    entries['upstream.patch'] = patch
    for raw in git('ls-files', '--others', '--exclude-standard', '-z', '--', 'src').split(b'\0'):
        if raw:
            name = raw.decode()
            add(repo / name, 'native-additions/' + name)
    if git('diff', '--binary', base, '--', *UPSTREAM_PATHS) != patch:
        raise ValueError('Upstream source changed while exporting; retry a stable snapshot')
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        add(repo / name, 'upstream-' + name)
    # This machine's private home path must never leak through fixtures or bundles.
    private_home = str(Path.home()).encode()
    leaked = [name for name, content in entries.items() if private_home in content]
    if leaked:
        raise ValueError('Private home path in exported files: ' + ', '.join(leaked))
    if any(hashlib.sha256(path.read_bytes()).digest() != digest for path, digest in input_hashes.items()):
        raise ValueError('Source changed while exporting; retry a stable snapshot')
    entries['manifest.json'] = (json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())}, indent=2) + '\n').encode()
    return entries

def export(root, output):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('Refusing to overwrite an existing artifact: ' + str(output))
    entries = collect(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as tar:
            for name, content in sorted(entries.items()):
                item = tarfile.TarInfo(name)
                item.size = len(content)
                item.mode = 0o755 if name.endswith('.sh') else 0o644
                tar.addfile(item, io.BytesIO(content))
    return {'artifact': str(output), 'files': len(entries), 'bytes': output.stat().st_size}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(Path(__file__).resolve().parents[1], args.output)))

if __name__ == '__main__':
    main()
