"""The two Library Code apps built from this source tree.

`library` is the daily app with Library chat panes. `official` opens chats in the official
Claude Code and Codex panels. Both share the core build and the Library extension; the official
app differs only in its product identity, its dock icon and its default `library.chatEngine`,
so it keeps its own bundle id, profile, extensions, URL scheme and shared storage.
"""
from pathlib import Path
import json, os, plistlib, shlex, shutil, subprocess, sys, tempfile

home = Path.home()
VARIANTS = {
    'library': {
        'name': 'Library Code', 'package': 'VSCode-darwin-arm64', 'bundle_id': 'local.library.code',
        'application': 'library-code', 'data_folder': '.library-code', 'url_scheme': 'library-code', 'chat_engine': 'library',
    },
    'official': {
        'name': 'Library Code Official', 'package': 'output/VSCode-darwin-arm64-official', 'bundle_id': 'local.library.code.official',
        'application': 'library-code-official', 'data_folder': '.library-code-official', 'url_scheme': 'library-code-official', 'chat_engine': 'official',
    },
}
# Codex needs these proposed APIs for its chat-sessions provider; without them its session list is missing.
OFFICIAL_API_PROPOSALS = {'openai.chatgpt': ['chatSessionsProvider', 'languageModelProxy']}
OFFICIAL_EXTENSIONS = ('anthropic.claude-code', 'openai.chatgpt')


def paths(variant):
    v = VARIANTS[variant]
    return {
        'app': home / 'Applications' / f"{v['name']}.app",
        'previous': Path(f"/tmp/{v['application']}-previous.app"),
        'user_data': home / 'Library/Application Support' / v['name'],
        'extensions': home / v['data_folder'] / 'extensions',
        'shared': home / f"{v['data_folder']}-shared",
    }


def product_identity(variant):
    v = VARIANTS[variant]
    return {
        'nameShort': v['name'], 'nameLong': v['name'], 'applicationName': v['application'],
        'dataFolderName': v['data_folder'], 'sharedDataFolderName': v['data_folder'] + '-shared',
        'darwinBundleIdentifier': v['bundle_id'], 'urlProtocol': v['url_scheme'],
        'serverApplicationName': v['application'] + '-server', 'serverDataFolderName': v['data_folder'] + '-server',
        'tunnelApplicationName': v['application'] + '-tunnel', 'win32MutexName': v['application'].replace('-', ''),
        'linuxDesktopName': 'local.' + v['application'].replace('-', '.'), 'linuxIconName': v['application'],
    }


def badge_icon(source, target):
    """The same icon with a small orange badge in the lower right corner."""
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory() as folder:
        iconset = Path(folder) / 'icon.iconset'
        subprocess.run(['iconutil', '-c', 'iconset', '-o', str(iconset), str(source)], check=True)
        for png in iconset.glob('*.png'):
            image = Image.open(png).convert('RGBA')
            size = image.size[0]
            radius, ring = size * 0.12, max(1, size * 0.025)
            center = (size * 0.78, size * 0.78)
            draw = ImageDraw.Draw(image)
            draw.ellipse([center[0] - radius - ring, center[1] - radius - ring, center[0] + radius + ring, center[1] + radius + ring], fill='white')
            draw.ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], fill='#F28C28')
            image.save(png)
        subprocess.run(['iconutil', '-c', 'icns', '-o', str(target), str(iconset)], check=True)


def apply(bundle, variant):
    """Give a packaged app the variant's identity. The default app is left as packaged."""
    if variant == 'library':
        return
    v, contents = VARIANTS[variant], Path(bundle) / 'Contents'
    app = contents / 'Resources/app'
    product = json.loads((app / 'product.json').read_text())
    old_application = product['applicationName']
    product.update(product_identity(variant), extensionEnabledApiProposals=OFFICIAL_API_PROPOSALS)
    (app / 'product.json').write_text(json.dumps(product, indent='\t') + '\n')
    # Electron names its keychain item after package.json "name", so the variant gets its own item.
    package = json.loads((app / 'package.json').read_text())
    package['name'] = v['name']
    (app / 'package.json').write_text(json.dumps(package, indent='\t') + '\n')
    launcher = app / 'bin' / old_application
    if launcher.exists():
        launcher.rename(app / 'bin' / v['application'])
    icon = contents / 'Resources' / f"{v['name']}.icns"
    badge_icon(contents / 'Resources/Library Code.icns', icon)
    info = plistlib.loads((contents / 'Info.plist').read_bytes())
    info.update(CFBundleIdentifier=v['bundle_id'], CFBundleDisplayName=v['name'], CFBundleIconFile=icon.name,
                CFBundleURLTypes=[{'CFBundleTypeRole': 'Viewer', 'CFBundleURLName': v['name'], 'CFBundleURLSchemes': [v['url_scheme']]}])
    info.pop('CFBundleIconName', None)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    # Helper apps keep their names (Electron finds them by CFBundleName) and take the variant's id prefix.
    for helper in (contents / 'Frameworks').glob('*.app'):
        plist = helper / 'Contents/Info.plist'
        data = plistlib.loads(plist.read_bytes())
        data['CFBundleIdentifier'] = data['CFBundleIdentifier'].replace(VARIANTS['library']['bundle_id'], v['bundle_id'], 1)
        plist.write_bytes(plistlib.dumps(data))


def set_defaults(extension, variant):
    """The bundled Library extension's defaults for this app: its chat engine and, for a variant, its name in the window title."""
    path = Path(extension) / 'package.json'
    package = json.loads(path.read_text())
    package['contributes']['configuration']['properties']['library.chatEngine']['default'] = VARIANTS[variant]['chat_engine']
    if variant != 'library':
        shutil.copy2(Path(__file__).with_name('official-sessions.py'), Path(extension) / 'official-sessions.py')
        shutil.copy2(Path(__file__).with_name('auth_failure.py'), Path(extension) / 'auth_failure.py')
        # The macOS default title without the app name, plus the app name.
        package['contributes']['configurationDefaults']['window.title'] = '${activeEditorShort}${separator}${rootName}${separator}${folderLocation}${separator}${profileName}${separator}${appName}'
    path.write_text(json.dumps(package, indent=2) + '\n')


def validate_official_runtime(bundle, backend, locations=None):
    """Read-only prerequisites checked before replacing an installed app."""
    locations = locations or paths('official')
    shared = Path(locations['shared'])
    source = Path(bundle) / 'Contents/Resources/app/extensions/library-code/official-sessions.py'
    if not source.is_file():
        raise ValueError('Official candidate is missing its packaged session helper')
    if not source.with_name('auth_failure.py').is_file():
        raise ValueError('Official candidate is missing its packaged failure classifier')
    if not (Path(backend) / 'data/accounts.json').is_file():
        raise ValueError('Library account registry is missing: ' + str(Path(backend) / 'data/accounts.json'))
    targets = [shared / 'bin/official-sessions.py', shared / 'bin/auth_failure.py', shared / 'bin/claude-wrapper', shared / 'config.json',
               Path(locations['user_data']) / 'User/settings.json']
    for target in targets:
        if target.is_symlink():
            raise ValueError('Official runtime target is a symlink; preserve it: ' + str(target))
        if target.exists() and not target.is_file():
            raise ValueError('Official runtime target is not a file: ' + str(target))
        if target.suffix == '.json' and target.exists():
            if not isinstance(json.loads(target.read_text()), dict):
                raise ValueError('Official settings must be a JSON object: ' + str(target))
    return True


def install_official_runtime(bundle, backend, locations=None):
    """Install the reviewed helper and Official-only settings, preserving originals.

    No clients are launched and no account or credential files are changed here.
    The runtime records source paths once so it never guesses a checkout path.
    """
    locations = locations or paths('official')
    validate_official_runtime(bundle, backend, locations)
    shared = Path(locations['shared'])
    source = Path(bundle) / 'Contents/Resources/app/extensions/library-code/official-sessions.py'
    if not source.is_file():
        raise ValueError('Official candidate is missing its packaged session helper')
    if not source.with_name('auth_failure.py').is_file():
        raise ValueError('Official candidate is missing its packaged failure classifier')
    backend = Path(backend).resolve()
    accounts = backend / 'data/accounts.json'
    if not accounts.is_file():
        raise ValueError('Library account registry is missing: ' + str(accounts))
    settings_path = Path(locations['user_data']) / 'User/settings.json'
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    config_path = shared / 'config.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    wrapper = shared / 'bin/claude-wrapper'
    helper = shared / 'bin/official-sessions.py'
    settings.update({'claudeCode.claudeProcessWrapper': str(wrapper), 'claudeCode.disableLoginPrompt': True,
                     'window.restoreWindows': 'none'})
    settings.setdefault('claudeCode.initialPermissionMode', 'bypassPermissions')
    settings.setdefault('claudeCode.allowDangerouslySkipPermissions', True)
    config.update({'accounts_file': str(accounts), 'library_config_file': str(backend / 'data/config.json'),
                   'app': str(locations['app']), 'user_data_dir': str(locations['user_data']),
                   'extensions_dir': str(locations['extensions'])})
    shared.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='runtime-backup-', dir=shared))
    os.chmod(backup, 0o700)
    files = {helper: source.read_bytes(), shared / 'bin/auth_failure.py': source.with_name('auth_failure.py').read_bytes(), wrapper: ('#!/bin/sh\nexec ' + shlex.quote(sys.executable) +
             ' "$(dirname "$0")/official-sessions.py" exec "$@"\n').encode(),
             config_path: (json.dumps(config, indent=2) + '\n').encode(),
             settings_path: (json.dumps(settings, indent=2) + '\n').encode()}
    for index, target in enumerate(files):
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise ValueError('Official runtime target is a symlink; preserve it: ' + str(target))
            shutil.copy2(target, backup / (str(index) + '-' + target.name))
    for target, content in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=target.name + '.', dir=target.parent)
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
        os.chmod(temporary, 0o700 if target in (helper, wrapper) else 0o600)
        os.replace(temporary, target)
    (shared / 'workspace').mkdir(exist_ok=True)
    links = subprocess.run([sys.executable, str(helper), '--shared', str(shared), 'profile-links-all'],
                           check=True, capture_output=True, text=True)
    return {'helper': str(helper), 'wrapper': str(wrapper), 'settings': str(settings_path),
            'backup': str(backup), 'profile_links': json.loads(links.stdout)}


def newest_extension(source, extension_id):
    """The newest installed copy of an extension, by its package.json version."""
    def version(folder):
        return tuple(int(part) for part in json.loads((folder / 'package.json').read_text())['version'].split('.') if part.isdigit())
    found = [f for f in Path(source).glob(extension_id + '-*') if (f / 'package.json').is_file()]
    return max(found, key=version) if found else None


def install_official_extensions(target, source=home / '.vscode/extensions'):
    """Copy the official extensions into the variant's own extensions folder. The originals are only read."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / 'extensions.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    installed = []
    for extension_id in OFFICIAL_EXTENSIONS:
        folder = newest_extension(source, extension_id)
        if not folder:
            raise SystemExit(f'{extension_id} is not installed in {source}')
        package = json.loads((folder / 'package.json').read_text())
        copy = target / folder.name
        if not copy.exists():
            subprocess.run(['cp', '-cR', str(folder), str(copy)], check=True)  # APFS clone
        manifest = [entry for entry in manifest if entry['identifier']['id'].lower() != extension_id]
        manifest.append({'identifier': {'id': extension_id}, 'version': package['version'],
                         'location': {'$mid': 1, 'fsPath': str(copy), 'path': str(copy), 'scheme': 'file'},
                         'relativeLocation': copy.name,
                         'metadata': {'installedTimestamp': int(copy.stat().st_mtime * 1000), 'targetPlatform': 'darwin-arm64'}})
        for old in target.glob(extension_id + '-*'):
            if old != copy:
                shutil.rmtree(old)
        installed.append(copy.name)
    manifest_path.write_text(json.dumps(manifest))
    return installed
