# Changelog

## 0.8.4 — 2026-07-22

- Add an opt-in, versioned context-envelope contract for stateless official GLM
  calls. Structured packets validate role, phase, source version, current artifact,
  constraints, findings ledger, open findings, and output protocol before dispatch,
  then bind accepted output to the exact canonical packet digest.
- Keep legacy plain task files backward-compatible while requiring new orchestrated
  GLM plan/review calls and direct routed children to carry complete authoritative
  context on every initial request and retry.
- Fail closed before credential lookup or network access when the exact request's
  conservative prompt-token upper bound plus configured output exceeds the bundled
  model context window. Inputs are never silently truncated or summarized.

## 0.8.3 — 2026-07-21

- Default official GLM `glm-5.2` calls with omitted or `auto` effort to `high`
  while retaining Thinking-enabled requests and explicit `max` support.

## 0.8.2 — 2026-07-21

- Return a strictly allowlisted token-usage summary from successful official GLM
  role calls. `usage_state` distinguishes provider-reported counters from an omitted
  `usage` object without weakening the separate `USED_CONFIRMED` model-identity
  evidence.
- Validate prompt, completion, total, and optional cached prompt-token counters as
  non-negative JSON integers, reject malformed present usage before releasing model
  content, and never forward raw provider metadata.

- Distinguish a genuinely absent OS-stored provider credential from a credential
  store that the current sandbox cannot reach. Official GLM status now exposes
  `READY`, `AUTH_REQUIRED`, or `CREDENTIAL_STORE_UNREACHABLE`, preserves the legacy
  readiness boolean, and keeps host-visible retries on the complete sealed status
  or call command without exposing the bearer.

- Allow `GLM-5.2 High` or `GLM-5.2 Max` to appear directly after any built-in
  `planner:`, `advisor:`, `designer:`, or `executor:` label in task-local mixed-model
  orchestration. Labels remain authoritative and GLM never gets silently recast as
  a fixed Advisor or researcher.
- Add a preview-first `seat` command with deterministic built-in role purposes,
  clean-add-only collision handling, exact qualification/auth checks, and mandatory
  Planner/Advisor response protocol signals.
- Add custom `researcher`, `reviewer`, `designer`, and other bounded roles backed
  directly by Z.AI/BigModel's official GLM-5.2 API, without OpenRouter.
- Keep the GLM route honest: Codex currently rejects Chat Completions providers,
  so the adapter is a sealed no-tools role call rather than a native
  provider-pinned `spawn_agent` route.
- Add strict official-endpoint/model/effort manifests, OS credential-store
  authentication, exact tuple qualification, separately authorized billable
  Gate 0, bounded task files, response-model identity checks, and non-secret
  compare-and-swap role state.
- Refuse the Coding Plan endpoint because Z.AI restricts subscription quota to
  its named supported tools and does not currently list Codex.

## 0.7.2 — 2026-07-20

- Enable implicit skill discovery for natural-language Kimi K3, External Model,
  and model-role availability questions. The previous metadata required an explicit
  plugin mention, so an untagged question could bypass the lifecycle and incorrectly
  infer that Kimi was unavailable from the visible Fable tools alone.
- Treat implicit availability questions as read-only status checks: report bundled
  support, local configuration, and current-task callability separately without
  authorizing setup, credentials, provider writes, role creation, or spend.
- Replace the verbose normalized routing summary with one concise activation line
  per explicitly supplied model-bearing seat, preserving the user's seat order and
  using the `Role — Model effort: Activated` wording.
- Reserve `Activated` for routes that are genuinely ready and callable in the
  current task. External authentication, qualification, connection, restart, and
  resolution boundaries continue to report their exact state and next action.

## 0.7.1 — 2026-07-18

- Recognize a bare `Designer: Kimi K3` seat assignment as the audited task-local
  External Model role `designer` instead of incorrectly reporting the route as
  unexposed. The root now dispatches the existing status, preparation,
  authentication, qualification, connection, restart, readiness, and resolution
  states explicitly.
- Preserve the no-secret and no-surprise-spend contract: the shorthand can authorize
  clean provider preparation and role creation, but never credential entry, Gate 0
  billing, probe retries, replacement, or deletion. Preparation may add the exact
  audited OpenRouter provider entry when absent; it never modifies, replaces, or
  removes existing provider entries or changes the root model, native GPT routes,
  model picker, chats, or sessions.

## 0.7.0 — 2026-07-18

- Add `/codex-orchestration --update`, a canonical-source-checked orchestration of
  Codex's native plugin upgrade/install commands. It refuses disabled, local,
  missing, duplicate, or unexpected sources and verifies final source, version, and
  enabled state without removing the plugin or touching routing, credentials, chats,
  or sessions.
- Add an optional first-class Designer seat with exact direct-model effort,
  bounded root-directed design authority, status and task-local reporting, and
  native routing schema/policy version 4. Cross-provider/custom Designers remain
  task-local until Codex exposes a scope-qualified agent identity.
- Preserve schemas 1–3 as valid legacy states with no Designer and migrate them on
  the next explicit setup while retaining their original disable snapshot.
- Add preview-first native policy repair for the narrow case where only marked
  mode/usage hints drift from otherwise valid saved state. Repair uses App Server
  compare-and-swap plus user/effective readback, preserves concurrent edits and
  restore history, and distinguishes a stale loaded Fable bridge from healthy
  first-party authentication after an update.

## 0.6.0 — 2026-07-18

- Add security-first External Model roles that remain outside the Codex Desktop
  model picker and never replace the root provider or model.
- Add strict bundled provider manifests, an explicit readiness state machine, exact
  effort validation, provider-pinned personal agent variants, and honest
  route-accepted versus runtime-confirmed states.
- Add command-backed provider authentication through a stable helper under
  `CODEX_HOME`, with macOS Keychain, Linux Secret Service, Windows Credential
  Manager, and explicitly pinned user-helper paths. No provider key is accepted in
  chat, command arguments, TOML, registry state, journals, logs, tests, or Git.
- Add preview-first provider preparation, isolated paid Gate 0 qualification,
  additive App Server writes, content-free crash recovery, exact-match disconnect,
  and provider removal that preserve root settings, OpenAI auth, and chat sessions.
- Preserve Claude Fable 5 as the only sealed first-party subscription adapter, with
  its existing no-tools/no-persistence bridge, first-party login checks, and runtime
  model metadata.
- Include OpenRouter's officially listed `moonshotai/kimi-k3` route, based on its
  model page and endpoint metadata reviewed 2026-07-18, with a 1,048,576-token
  context window and `max` as its only supported reasoning effort. `auto` resolves
  to `max`; every other effort is rejected without clamping. Each installation
  remains unqualified until the exact OpenRouter/Kimi/max tuple passes one
  explicitly billing-authorized isolated Gate 0.
- Verify Gate 0 CLI controls before any billable command and read only Codex's
  bounded `--output-last-message` artifact, never decorated process output. The
  Windows portability job performs a real temporary Credential Manager round trip
  and verifies transactional owner/group/DACL/integrity-label preservation across file replacement;
  those hosted gates must pass before 0.6.0 is released and are not reproducible on
  a non-Windows local preflight.

## 0.5.1 — 2026-07-16

- Preserve explicit role labels exactly: a model supplied as `planner:` can never be reinterpreted as an Advisor, and Fable Planner uses only the Planner operations.
- Give Planner support a new plugin version so marketplace upgrade and reinstall replace the affected Advisor-only `0.5.0` cache instead of reusing it.
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
