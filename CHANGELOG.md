# Changelog

## 0.5.0 — 2026-07-10

- Add Claude Fable 5 as an opt-in, root-only advisor inside Codex through a bundled local MCP bridge to the authenticated Claude Code CLI.
- Keep all MCP launcher variants disabled by default; setup enables only the compatible Python 3.11+ variant and disable restores the prior plugin override values.
- Pin `claude-fable-5`, sanitize API/provider override variables, disable tools, sessions, and prompt suggestions, and fail closed unless both the plan signal and runtime model metadata are valid.
- Extend setup and status with no-spend login/capability checks and add unit, lifecycle, restoration, packaging, and protocol coverage.
- Keep user-facing status on the exact `Claude Fable 5` name and omit account-plan metadata from MCP results.

## 0.4.0 — 2026-07-10

- Make one-time, config-first routing the primary workflow: setup once, then use Codex normally.
- Add native setup, status, update, and disable through Codex App Server's atomic config API.
- Route same-provider executors with exact model, effort, and `fork_turns = "none"` inputs.
- Keep the selected task model as root orchestrator and let Codex decide whether delegation helps.
- Make the advisor truly optional: omission now means `none`.
- Preserve custom agents as the durable and cross-provider route.
- Give personal provider-pinned roles stable home-specific names and reject missing or project-shadowed agent routes.
- Capability-test the active, PATH, known Desktop, and explicitly supplied Codex clients before writing newer fields.
- Configure and restore `tool_namespace = "agents"` for the validated v2 route; live Desktop testing showed the default `collaboration` namespace rejected expanded model metadata while `agents` spawned Luna at `xhigh`.
- Clarify that metadata visibility plus the `agents` namespace exposes the needed controls but still does not choose Luna; `usage_hint_text` supplies the executor route.
- Keep the unnecessary Sol/Terra v2 force flag omitted.
- Preserve unrelated TOML, comments, concurrency settings, and pre-setup routing values on disable.
- Add native-policy setup/restore lifecycle validation plus generated routing-contract tests.
- Rewrite the README, ASCII flow, role explanations, config-only comparison, compatibility guidance, and savings claim in plain language.

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
