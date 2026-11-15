# Repository layout and release checks

## Where files belong

| Directory or file | Contents |
| --- | --- |
| `app/*.py` | Cohesive local backend, broker, indexing pipeline, and provider adapters |
| `scripts/` | Sanitized release packager |
| Root `install.sh`, `run.sh` | Small launchers for the application |
| `app/workbench/src/` | Browser client source |
| `app/web/workbench/` | Current bundled browser client; ship referenced assets only |
| `app/vscode-library/` | VS Code extension source and vendored dependencies |
| `docs/` | Portable setup, operation, and release guides |
| `app/data/` | Private per-user state; only the example configuration is shareable |
| `app/output/` | Local reports, screenshots, and release archives; ignored |

Installed dependencies, caches, historical asset hashes, screenshots, and timestamped VSIX files do not belong in the shared source repository. Keep build products under ignored output/build directories. Keep collected data and completed results until their owner explicitly chooses to remove them.

The development machine may contain private requirement notes and QA reports. The release packager uses an explicit allowlist rather than copying that entire checkout.

## Produce a shareable release

1. Build the browser client from the locked dependencies.
2. Run `python3 scripts/package_workspace.py` from the root. It creates a new dated output directory and never overwrites a previous release.
3. Extract the archive into a new writable directory. Inspect `PACKAGE-INVENTORY.json` and `PRIVACY-SCAN.json` there.
4. Confirm that `app/data/` contains only `config.example.json`. No source histories, account profiles, journals, logs, screenshots, or personal configuration should ship.
5. With a temporary home directory, create configuration and run `python3 app/pipeline.py --parse-only`. Confirm empty history is accepted without provider model calls.
6. Start the extracted server on an unused loopback port. Verify Workspace loads, accounts return a truthful empty/discovered state, and missing histories have useful empty states. Stop only this test process.
7. Build the VSIX in the extracted checkout. Exercise it with a mocked backend; do not send a paid message as release QA.
8. Review the final file list and secret scan before committing. Push only the extracted sanitized tree.

The scanner rejects credential-shaped values and unexpected home paths. Automated scanning cannot recognize every kind of private text; manual review remains part of release verification. Rebuild and recheck the package whenever implementation changes after review.

## Source changes

Preserve in-flight user work. Keep the previous browser build outside the shipped tree before replacing it. Use fixture data for tests and never restart a production broker with active runs. The browser and VS Code extension must keep accepted prompts and broker state consistent while preserving their own unsent drafts.
