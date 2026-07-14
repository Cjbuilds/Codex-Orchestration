# Contributing

## Development setup

Use Python 3.11 or newer and a current Node.js release when running the real plugin lifecycle test. The production scripts use only the Python standard library.

```bash
python3 -m pip install -r requirements-dev.txt
python3 scripts/preflight.py quick
```

Run the complete local gate before opening a pull request:

```bash
python3 scripts/preflight.py full
```

The lifecycle smoke test installs and upgrades the plugin through a disposable Git marketplace and requires a local `codex` CLI. It does not use or change your normal Codex home.

These preflight commands are the source of truth for local validation. Local results are PARTIAL; required hosted checks run strict targets and remain authoritative. To enable the versioned quick/full Git hooks, preview `python3 scripts/install_hooks.py`, then opt in with `python3 scripts/install_hooks.py --apply`.

## Pull requests

- Keep filesystem and config mutations reversible and fail closed on ambiguity.
- Add an exact regression test for every behavior fix.
- Give every plugin payload change a strictly greater semantic version.
- For security or state changes, include a threat model, malformed and negative-path tests, and a fresh final-tree review.
- Bind the review attestation to the exact head SHA. Any later head change invalidates the review.
- Do not weaken root authority, approvals, permissions, or the `fork_turns = "none"` contract.
- Update the changelog and public compatibility statements when behavior changes.
- Never include credentials or private configuration in fixtures, logs, or routing hints.

All required checks must pass. Resolve review conversations before merge. Releases follow [RELEASE.md](RELEASE.md).
