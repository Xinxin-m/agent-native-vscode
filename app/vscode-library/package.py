"""Build an offline VSIX from a strict source allowlist. Never package user data."""
from pathlib import Path
import json,zipfile,datetime
from bundle_project_map import bundle,ASSETS
root=Path(__file__).resolve().parent
bundle(root)
package=json.loads((root/'package.json').read_text())
files=['project-map.cjs','package.json','extension.js','core.js','README.md','media/panel.js','media/auth-recovery.js','media/notification-center.js','media/notification-center.css','media/session-reference-menu.js','media/state.js','media/run-phase.js','media/native-commands.json','media/panel.css','media/markdown-it.min.js','media/purify.min.js','media/markdown-it.LICENSE','media/dompurify.LICENSE']
files += ['media/project-map/'+name for name in (*ASSETS,'bundle.json')]
manifest=f'''<?xml version="1.0" encoding="utf-8"?><PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata><Identity Language="en-US" Id="{package['name']}" Version="{package['version']}" Publisher="{package['publisher']}"/><DisplayName>Library Chat</DisplayName><Description xml:space="preserve">Shared Library broker chat client</Description><Categories>Other</Categories><GalleryFlags>Preview</GalleryFlags><Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.85.0"/><Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui"/></Properties></Metadata><Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/><Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/></Assets></PackageManifest>'''
content='''<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="cjs" ContentType="application/javascript"/><Default Extension="html" ContentType="text/html"/><Default Extension="json" ContentType="application/json"/><Default Extension="js" ContentType="application/javascript"/><Default Extension="css" ContentType="text/css"/><Default Extension="md" ContentType="text/markdown"/><Default Extension="LICENSE" ContentType="text/plain"/><Default Extension="vsixmanifest" ContentType="text/xml"/></Types>'''
(root/'builds').mkdir(exist_ok=True)
output=root/'builds'/('library-workspace-'+package['version']+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.vsix')
with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,strict_timestamps=False) as archive:
    archive.writestr('extension.vsixmanifest',manifest)
    archive.writestr('[Content_Types].xml',content)
    for name in files:
        source=root/name
        assert source.is_file() and not source.is_symlink(),name
        archive.write(source,'extension/'+name)
with zipfile.ZipFile(output) as archive:
    assert sorted(archive.namelist())==sorted(['extension.vsixmanifest','[Content_Types].xml']+['extension/'+f for f in files])
    assert archive.testzip() is None
print(output)
