# Contributing

## Development setup

Use Python 3.11 or newer and a current Node.js release when running the real plugin lifecycle test. The production scripts use only the Python standard library.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m ruff check plugins tests scripts
```

Run the local release gate before opening a pull request:

```bash
python3 -m compileall -q plugins tests scripts
python3 -m ruff check plugins tests scripts
python3 -m unittest discover -s tests -v
python3 tests/plugin_lifecycle_smoke.py
python3 scripts/release_check.py
```

The lifecycle smoke test installs and upgrades the plugin through a disposable Git marketplace and requires a local `codex` CLI. It does not use or change your normal Codex home.

## Pull requests

- Keep filesystem and config mutations reversible and fail closed on ambiguity.
- Add negative-path tests for crash recovery, concurrency, provider, and ownership boundaries.
- Do not weaken root authority, approvals, permissions, or the `fork_turns = "none"` contract.
- Update the changelog and public compatibility statements when behavior changes.
- Never include credentials or private configuration in fixtures, logs, or routing hints.

All required checks must pass. Resolve review conversations before merge. Releases follow [RELEASE.md](RELEASE.md).
