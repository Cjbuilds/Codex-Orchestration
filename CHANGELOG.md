# Changelog

## 0.3.0 — 2026-07-10

- Treat the current Codex task model as the only orchestrator.
- Add an optional root-facing plan advisor with bounded approval signals.
- Replace generic role layers with namespaced standalone Codex custom agents.
- Keep normal persistence out of `.codex/config.toml`.
- Add opt-in, backup-first migration for every previous published format.
- Distinguish prompt preferences, loaded pins, unavailable routes, and confirmed child models.
- Add project/personal provider boundaries, symlink/hard-link and collision protection, catalog provenance, timeouts, secret-redacted previews, atomic metadata-preserving swaps, directory fsyncs, and content-free crash-recovery journals.
- Add preview-first removal for fully managed saved roles without touching root configuration.
- Rewrite installation, invocation, role explanations, savings math, and the ASCII workflow for normal users.
- Add CI, packaging checks, contract tests, model-inspection tests, and a real Git-backed install/upgrade/runtime lifecycle smoke.

## 0.2.0 — 2026-07-09

- Added the optional advisor workflow.
- Kept Plan, Goal, delegation, integration, and verification under Codex control.

## 0.1.0 — 2026-07-09

- Initial Codex-Orchestration release.
