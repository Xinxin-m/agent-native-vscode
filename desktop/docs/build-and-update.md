# Build and update Library Code

The export contains the Library client and changes to Code-OSS. It does not contain the shared Library server, credentials, conversations or a prebuilt application. Connect to your existing Library service with the application-level `library.endpoint` setting; the default is `http://127.0.0.1:8744`.

## Reconstruct the source

Extract the source archive into a new directory. Check out `microsoft/vscode` into `vscode/` at the exact commit in `docs/upstream.json`. Apply `upstream.patch` from inside that checkout, then copy the contents of `native-additions/` into it, if present. The patch includes the custom package destination used by variant builds.

Install the Node version in `vscode/.nvmrc`, the package-manager version in `vscode/package.json`, and the upstream macOS build prerequisites. Keep Node and the package manager on PATH.

1. In `vscode/`, run `pnpm install --frozen-lockfile`.
2. In `integration/ui/`, run `npm ci`.
3. At the export root, run `python3 integration/build.py`.
4. In `vscode/`, run `pnpm run gulp vscode-darwin-arm64`.

The default macOS Apple Silicon candidate is `VSCode-darwin-arm64/Library Code.app`. Development runs use `integration/launch.sh` with a separate profile. The bundled map assets have a hash manifest; the portable build verifies them without requiring another source repository.

## Verify and install

Verify the candidate's behavior in an isolated profile against synthetic data before replacing your daily client. Sign the complete candidate after packaging:

```sh
codesign --force --deep --sign - "VSCode-darwin-arm64/Library Code.app"
codesign --verify --deep --strict "VSCode-darwin-arm64/Library Code.app"
python3 integration/install.py --candidate-hash
```

Record the printed whole-bundle hash and the `sourceHash` from the candidate's `Contents/Resources/app/extensions/library-code/build-info.json`. Then install that exact candidate:

```sh
python3 integration/install.py --install \
  --expected-sha256 REVIEWED_BUNDLE_SHA256 \
  --expected-extension-hash REVIEWED_EXTENSION_SOURCE_HASH
```

For a running daily app, add `--restart`. The app must have been launched with `--remote-debugging-port=19342`; the installer verifies that connection belongs to its process. It refuses dirty native editors, external child processes, custom profiles and ambiguous client instances. It preserves a private rollback bundle and checks restored tabs, saved conversation drafts and provider identities. An incomplete restoration is reported as pending verification.

`python3 integration/install.py --check` only checks integrity. The installer never repairs hashes to bless mixed files. Never copy a newer extension or core into a running application, and never restart the shared service while provider runs are active.

## Checks

Run `pnpm run compile-client` in `vscode/` before the extension tests; their native IPC checks load development modules from `vscode/out`. Packaging alone writes different output directories.

- `node --test integration/extension/test/*.test.cjs`
- In `integration/ui/`: `npm test`
- `python3 integration/qa/test_install_guard.py`

These source tests complement native interaction checks. They do not prove provider authentication, remote write support or the state of a particular user's open app. Official proprietary extensions and Microsoft services have separate compatibility requirements.
