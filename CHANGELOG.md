# Changelog

## 0.5.0 — Unreleased

- Add an optional Planner route: a configured model drafts and revises the plan, while omission keeps planning with the root Codex model.
- Let Claude Fable 5 act as Planner through bounded `create_plan` and `revise_plan` tools while preserving its existing Advisor workflow.
- Run Planner and Advisor through a root-mediated approval loop that stops on `PLAN_APPROVED`, caps review at five rounds, and fails closed before execution when approval or a required route is unavailable.
- Migrate native routing state to schema 3 while accepting schemas 1 and 2 as root-Planner configurations, and reject identical configured Planner and Advisor routes.
- Use one shared full-state validator for native setup/status/disable and Fable authorization, enforcing genuine schema/policy pairs, exact nested restore/scalar/MCP contracts, schema-specific fields, and plugin-owned policy markers.
- Harden Fable seat authorization against malformed, cross-home, legacy-Planner, multi-seat, and launcher-mismatch state, and document that MCP caller isolation is policy-enforced while no-tools execution is mechanical.
- Make Claude Fable 5 advisor effort configurable, default it to `high`, support `low` through `max`, treat user-facing `ultra` as an explicit alias for Claude Code's `max`, and fail `--require-effective` when the saved Fable route is unavailable.
- Add Claude Fable 5 as an opt-in, root-directed Advisor through a bundled no-tools local MCP bridge to the authenticated Claude Code CLI.
- Keep every Fable launcher disabled by default, enable only one compatible Python 3.11+ route, and restore prior plugin overrides on disable.
- Pin `claude-fable-5`, allow only its explicitly documented Claude Code helper in runtime usage metadata, remove provider override variables, disable tools and session persistence, and fail closed unless the plan signal and runtime model set are valid.
- Add automation-safe native status gating with `--require-effective`.
- Detect orphaned managed personal roles and distinguish installed policy from live route validation.
- Fail truthfully when restore-state persistence and config rollback do not both succeed.
- Exercise direct-model lifecycle setup and add macOS/Windows portability checks.
- Pin GitHub Actions, add CodeQL and Dependabot, and document secure contribution and release workflows.
- Clarify policy-guided routing, concurrency, Windows custom-role limitations, and two-phase recovery.

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
