"""Explicit OS destinations. No shell evaluation or remote-path substitution."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

import file_service as fs


def default_browser():
    path = Path.home()/'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist'
    try:
        with path.open('rb') as handle:
            handlers = plistlib.load(handle).get('LSHandlers',[])
        for scheme in ('https','http'):
            for record in handlers:
                if record.get('LSHandlerURLScheme') == scheme and record.get('LSHandlerRoleAll'):
                    return record['LSHandlerRoleAll']
    except (OSError,ValueError,plistlib.InvalidFileException):
        pass
    result = subprocess.run(['/usr/bin/osascript','-e','id of application (path to default application for URL "https://example.com")'],capture_output=True,text=True,timeout=10,check=True)
    value = result.stdout.strip()
    if not isinstance(value,str) or not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in value):
        raise fs.FileError('opener_unavailable','Cannot identify the default browser',503)
    return value


def open_resource(resource,destination):
    if not isinstance(resource,dict):
        raise fs.FileError('invalid_resource','A resource is required')
    fs.local_host(resource.get('host_id'))
    if destination not in ('default_browser','system','editor'):
        raise fs.FileError('invalid_destination','Choose default_browser, system or editor')
    if resource.get('url'):
        url = str(resource['url'])
        parsed = urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
            raise fs.FileError('invalid_url','Only HTTP/HTTPS URLs may open in a browser')
        if destination == 'editor':
            raise fs.FileError('invalid_destination','The editor destination requires a local file')
        target = url
    else:
        item = fs.instance().stat(resource.get('root_id'),resource.get('path',''),resource.get('file_id'))
        fs.local_host(item.get('host_id'))
        target = item['path']
    if destination == 'editor':
        executable = shutil.which('code')
        if not executable:
            raise fs.FileError('opener_unavailable','VS Code command is not installed',503)
        argv = [executable,'-g',target]
    elif sys.platform == 'darwin':
        argv = ['open','-b',default_browser(),target] if destination == 'default_browser' else ['open',target]
    else:
        if destination == 'default_browser' and not resource.get('url'):
            raise fs.FileError('opener_unavailable','Explicit file-to-default-browser opening is currently supported on macOS',501)
        executable = shutil.which('xdg-open')
        if not executable:
            raise fs.FileError('opener_unavailable','No system opener is available',503)
        argv = [executable,target]
    subprocess.run(argv,capture_output=True,text=True,timeout=15,check=True)
    return {'ok':True,'destination':destination}


@fs.guarded
def route(req,query,body):
    try:
        return open_resource(body.get('resource'),body.get('destination'))
    except (subprocess.SubprocessError,OSError) as exc:
        raise fs.FileError('opener_failed','Opening failed: '+str(exc),503)


ROUTES = {('POST','/api/open_destination'):route}
BACKGROUND = []
