---
name: codex-orchestration
description: Build multi-model Codex workflows by assigning compatible models to roles such as planner, advisor, executor, researcher, reviewer, writer, or supervisor. Use when the user invokes Codex Orchestration to create custom roles, define a workflow, or set up, inspect, change, disable, or temporarily override model routing. Keep the selected task model as root and preserve Codex's Goal, permissions, integration, and verification behavior.
---

# Codex Orchestration

The model selected when this Codex task started is already the orchestrator. Never ask the user to configure another one and never change the root model on this skill's behalf.

This skill adds a model route to Codex's existing multi-agent flow. It does not create another scheduler.

## Understand the command

Support these simple forms:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High
/codex-orchestration setup executor: GPT-5.6 Luna Extra High, advisor: Claude Fable 5 High
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High
/codex-orchestration create project role: researcher
/codex-orchestration create personal roles: researcher, writer, reviewer
/codex-orchestration status
/codex-orchestration disable
/codex-orchestration remove custom roles personally
/codex-orchestration executor: GPT-5.6 Terra high — <one task only>
```

`setup` installs or updates the personal one-time routing policy. `create project role` or `create personal role` creates native Codex custom-agent files. `status` inspects built-in routing. `disable` restores its pre-setup values.

`remove custom roles` cleans only verified plugin-managed advisor/executor files. Arbitrary native roles are user-owned. An invocation with seats and work but no control verb is a current-task override and must not rewrite config.

Explicit seat labels are authoritative. `planner:` configures only Planner, `advisor:` configures only Advisor, and `executor:` configures only Executor. Never infer or change a seat from a model's historical use, default role, cached description, or provider; in particular, never reinterpret a supplied `planner:` model as an Advisor. Saved defaults may fill only omitted seats and never override a seat supplied in the current invocation.

The executor is required for setup or a task-local override. It is not required for a custom-role creation request. Planner and advisor are optional: omitted planner means the current root model plans, while omitted advisor means `advisor: none`. Do not ask separate planner or advisor questions unless the user asks for help choosing them.

For both persistent setup and task-local overrides, reject an identical Planner and Advisor route: the same direct model ID, the same custom-agent name, or Claude Fable 5 in both seats. Independent critique is required.

If the executor is missing, ask only:

```text
Which executor model and effort should Codex use? You can optionally include a planner and advisor; omission uses the root as planner and no advisor.
```

Because explicit skills may not reload from a bare reply, include a ready-to-copy line using the exact label shown by the client and preserve the original work:

```text
<exact-skill-label> setup executor=<model>@<effort-or-auto>, planner=<model>@<effort-or-auto>|root, advisor=<model>@<effort-or-auto>|none
```

For a task-local request, append `— <original task>`. Keep every supplied modifier. Do not lose the user's task while collecting a model choice.

Before applying setup or starting task-local work, report the normalized mapping as `Planner`, `Advisor`, and `Executor` in that order. Compare it with the user's explicit labels. If an exact seat cannot run, report that seat as unavailable and stop under the required-route rules; never move its model to another seat. When the user supplies Planner and Executor but omits Advisor, report `Advisor: none`.

If an old prompt contains `orchestrator:`, explain that the current task model already owns that role. Ignore that seat instead of switching or persisting it.

Normalize `Extra High` to `xhigh`. For Claude Fable 5, accept `Low`, `Medium`, `High`, `XHigh`, `Max`, or `Ultra`. Omission or `Auto` means `High`; `Ultra` is a user-facing alias for Claude Code's actual `max` setting and must be reported as that mapping. Route Fable with `--planner-fable --planner-effort <normalized-effort>` or `--advisor-fable --advisor-effort <normalized-effort>`, not through the Codex model catalog. Resolve every other display name to an exact ID only through the executing host's model catalog, picker, a loaded custom agent, or official provider documentation. Never invent an ID. For persistent direct routing, resolve `auto` to the catalog's concrete default.

Read [providers-and-models.md](references/providers-and-models.md) before setup, when clients disagree, when a model is absent, when providers differ, or when custom agents or legacy migration are involved.

## Create arbitrary custom roles

Use native Codex custom-agent files for roles beyond the built-in planner, advisor, and executor seats. Examples include researcher, reviewer, writer, supervisor, security auditor, browser debugger, or domain expert.

Use project scope when the user says `project`, `repo`, `workspace`, or `current project`. Write to `<trusted-project>/.codex/agents/<role-name>.toml`. Use personal scope only when explicitly requested and write to `~/.codex/agents/<role-name>.toml`.

Before writing:

1. Normalize the role name to lowercase snake case and validate `^[a-z][a-z0-9_]{0,62}$`.
2. Require a clear purpose and `developer_instructions` that keep the role bounded.
3. Resolve the model and effort from the active catalog or a user-confirmed exact ID.
4. If `model_provider` is supplied, require an existing configured and authenticated compatible provider. Never create provider access or collect credentials.
5. Use the current task permission mode by default. Add `sandbox_mode` only when the user requests it. A role may request a narrower sandbox; it never bypasses the parent task's authority.
6. Keep `agents.max_depth = 1` behavior unless the user explicitly asks for nested agents. A custom role should not create descendants by default.
7. Refuse symlinked paths, duplicate agent names, malformed TOML, and overwriting an existing file without explicit replacement approval.

A custom agent file must define `name`, `description`, and `developer_instructions`. It may also define `model`, `model_reasoning_effort`, `model_provider`, `sandbox_mode`, `mcp_servers`, and `skills.config` when supported.

Preview the path and complete TOML before writing. A literal create request authorizes a clean new file after preview. Replacing or deleting an existing user-owned role requires a separate explicit decision.

Do not add the plugin ownership marker to arbitrary roles. Do not claim `disable` or `remove custom roles` will remove them. Tell the user to start a new task after creation so Codex loads the new roles.

When the user supplies a sequence such as `researcher -> reviewer -> writer`, preserve it as task-level workflow instructions. The root orchestrator owns every handoff, resolves conflicting feedback, verifies the result, and may skip only optional steps.

If the user combines a workflow with a Codex Goal, leave Goal lifecycle and limits under Codex's normal Goal controls. The orchestration policy operates inside the Goal; this skill does not silently create, pause, resume, or clear it.

## One-time native setup

Use this path for a current same-provider setup such as Sol root to Luna or Terra children. Claude Fable 5 is the one built-in cross-provider Planner or Advisor exception because it runs through the bundled read-only MCP bridge and the user's authenticated Claude Code CLI.

1. Identify the Codex binary used by the active host. Do not assume the shell `codex` is the Desktop binary.
2. Resolve the exact executor and optional Planner and Advisor IDs and efforts from that host.
3. Run the bundled native configurator from this skill's real directory with Python 3.11 or newer. Use `python3` on typical macOS/Linux hosts; on Windows select an available `py -3.11` or `python` launcher after checking its version. Never use a repository-relative copy from the user's workspace.
4. Inspect the dry-run output. A literal `setup` request authorizes applying a clean, non-replacement personal policy after that preview.
5. Start a new task after apply. The user chooses the orchestrator in the normal model picker and no longer needs to invoke this skill for ordinary work.

Typical dry run and apply:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --executor-model gpt-5.6-luna \
  --executor-effort xhigh

python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --executor-model gpt-5.6-luna \
  --executor-effort xhigh \
  --apply
```

Add `--advisor-model` and `--advisor-effort` for a same-provider Codex advisor. For Claude Fable 5, use `--advisor-fable`; add `--advisor-effort low|medium|high|xhigh|max` when the user chooses one. Omitting Fable effort defaults to `high`, while user-facing `ultra` is normalized to Claude Code's `max`. The configurator verifies that the installed Claude Code CLI advertises the selected effective effort. It also requires Claude Code to be logged in through a first-party Pro or Max account, chooses an available Python 3.11+ MCP launcher, and performs only an auth/capability check during setup. It never extracts a token, writes a credential, or makes a model call during setup or status. Omission persists `advisor: none`.

Add `--planner-model` and `--planner-effort` for a same-provider Planner. For Claude Fable 5, use `--planner-fable`; add `--planner-effort low|medium|high|xhigh|max` when the user chooses one. Planner omission persists no Planner route and means the root plans. A configured Planner and Advisor must not resolve to the same model or agent route; independent review is required.

The configurator capability-tests the complete four-field preset on the active target, `codex` on PATH when different, the known macOS Desktop binary when present, and every explicit `--compat-bin`. A successful isolated config probe means that client can parse the preset; it is not a live child-model confirmation. Report `route accepted` or `used and confirmed` only from the exact live spawn evidence defined below. Ask about other Codex/IDE installations that share this config only when the environment suggests they exist, and pass their binaries explicitly. If the request or active host indicates a named `--profile`, explain that normal setup manages the default user layer and is not verified for that profile; do not add a routine question for users with no profile signal. If a checked client rejects any managed field, stop before apply. Recommend updating it or using the task-local fallback. `--allow-incompatible-client` requires a separate explicit user decision because it can make the shared config unreadable to that client.

For the current validated v2 direct route, set `tool_namespace = "agents"`. Live testing on Desktop `0.144.0-alpha.4` showed that the default reserved `collaboration.spawn_agent` schema rejected expanded model/effort metadata, while `agents` accepted the same request and spawned Luna at `xhigh`. Treat this as a required control-surface setting for that tested path, not as the executor selection. `usage_hint_text` carries the actual Planner, Advisor, and Executor routes.

Do not add `enabled = true` for a Sol or Terra root. Their current model metadata selects v2. The configurator intentionally manages these routing fields:

- `features.multi_agent_v2.hide_spawn_agent_metadata`;
- `features.multi_agent_v2.tool_namespace`;
- `features.multi_agent_v2.multi_agent_mode_hint_text`;
- `features.multi_agent_v2.usage_hint_text`.

When Claude Fable 5 is selected, it additionally manages only the plugin-scoped `enabled` override for the chosen bundled MCP launcher and any launcher variant already overridden by the user. All bundled variants are disabled by default. The original override values are stored and restored by `disable`. Codex's TOML editor may retain an inert empty table header after deleting the last override; never rewrite the file merely to remove that cosmetic header.

It uses Codex App Server's `config/read` and `config/batchWrite` APIs, not a home-grown TOML rewrite. It preserves unrelated settings and comments, validates the whole effective config, and uses the user-layer version to detect races. Restore snapshots cover the four routing fields plus the narrowly scoped MCP overrides only when Fable is selected; the namespaced state also records schema/version markers, config path, selected seats, and scalar-conversion metadata when needed. If the user explicitly replaces existing hint text, the exact prior text is stored for restoration; warn them never to place credentials in routing hints.

If a user-authored mode or usage hint already exists, do not replace it automatically. Show the conflict. Use `--replace-existing-policy` only after the user explicitly approves replacing and later restoring those exact values.

## Status, change, and disable

For status:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --status

python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --status --require-effective
```

Run status from the target project. The first form is descriptive. Use `--require-effective` for automation and release gates; it returns nonzero for incompatible clients, conflicts, overrides, incomplete controls, an unavailable Fable or custom-agent route, or orphaned v0.4+ personal roles. Report the current task model as the orchestrator, Planner (`root` when omitted), configured Advisor and Executor, whether the personal policy is installed and effective in that workspace, whether effective spawn controls are visible, whether the effective tool namespace is `agents`, the target config path, and checked-client compatibility. State that neither status form proves a live route or infers v2 activation for the model selected in a task; current Sol or Terra is the intended root.

To change seats, run normal `setup` again. The configurator keeps the original restore snapshot rather than treating its own managed values as user settings.

For disable, dry-run and then apply. A literal `disable` request authorizes a clean restore:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --disable

python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --disable --apply
```

Disable must remain available even if an older client is incompatible with the active policy. Refuse to erase managed fields that the user edited after setup; explain the conflict instead.

For personal v0.4 custom roles, preview and apply removal with `configure_orchestration.py --scope personal --personal-route-names --remove-saved-roles`. For older fixed-name personal roles, run a separate preview without `--personal-route-names`. Project removal uses `--scope project --root <trusted-project> --remove-saved-roles`. Delete only files that the configurator fully validates as managed; edited or user-owned files require manual review.

## Claude Fable 5 Planner or Advisor

Use this built-in route when the user names Claude Fable 5. Do not create a custom provider or custom-agent file for it.

In every user-facing status or result, use the exact name `Claude Fable 5`. Report authentication as `first-party login ready`; do not expose or restate Claude account-plan metadata.

Prerequisites:

- the official `claude` CLI is installed;
- `claude auth status` reports a first-party Pro or Max login;
- a Python 3.11+ launcher is available.

The plugin packages three disabled MCP launcher variants for macOS, Linux, and Windows. Setup enables exactly one compatible variant through the plugin's namespaced config when either Fable seat is selected. At planning or review time the MCP server removes API-key and Bedrock/Vertex/Foundry override variables, re-checks first-party login, and invokes `claude -p --model claude-fable-5` with `--safe-mode`, no tools, no session persistence, prompt suggestions disabled, and JSON output. Each saved seat pins its model and effort; the root cannot replace them through tool arguments.

Fable effort is configurable per setup. The default is `high`; supported Claude Code values are `low`, `medium`, `high`, `xhigh`, and `max`. Accept `ultra` as an alias for `max`, save the effective Claude Code value, and disclose the alias mapping in setup output. Existing saved `max` routes remain valid.

The bridge exposes only bounded, read-only planning operations. `create_plan` accepts one self-contained packet and requires `PLAN_DRAFT`. `revise_plan` requires the task, canonical current plan, latest critique, and compact findings history, then requires `PLAN_REVISION` plus a findings ledger and revised plan. `review_plan` remains the Advisor operation and requires `PLAN_APPROVED` or `PLAN_REVISE`. Every call uses the same full saved-state validator as native status/disable, then requires runtime `modelUsage` to contain the pinned `claude-fable-5` primary plus only the bridge's explicit exact helper allowlist. Return every observed ID in `used_models`; an unknown additional or missing primary model makes the seat unavailable. Any auth, transport, state, format, or model-confirmation failure makes that seat unavailable; it never counts as approval. The bridge returns no account identifier or credential.

The managed workflow reserves these MCP calls for the root Codex model. Current MCP requests do not carry caller identity, so the bridge cannot independently authenticate root versus child; caller isolation is instruction-enforced. The bridge still mechanically prevents tools, edits, permission prompts, and session persistence. Never describe the caller boundary as engine-enforced.

## Durable or cross-provider custom agents

Direct `model` routing is same-provider. Except for the built-in Claude Fable 5 MCP route above, a different provider needs an already authenticated Codex-compatible provider and a loaded custom agent that pins `model_provider`.

For a cross-provider Planner, create a bounded personal custom role through the arbitrary-role flow, start a new task so it loads, and pass its exact name with `--planner-agent`. The older standalone managed-role helper below continues to own only its existing Advisor and Executor files; do not expand its migration/removal transaction just to create a Planner.

Use the existing standalone-agent configurator for this extended path. Personal scope is required for machine-local provider IDs and affects all projects, so the user's explicit cross-provider `setup` request must name or confirm the existing provider ID. Never create provider definitions, collect keys in chat, or write credentials.

First preview and apply the namespaced custom agents:

```bash
python3 <skill-dir>/scripts/configure_orchestration.py \
  --scope personal \
  --personal-route-names \
  --codex-bin <active-codex-binary> \
  --executor-model <exact-id> \
  --executor-effort <effort> \
  --executor-provider <existing-provider-id> \
  --advisor-model <exact-id> \
  --advisor-effort <effort> \
  --advisor-provider <existing-provider-id>
```

When this cross-provider/custom-agent setup omits an advisor, pass `--remove-advisor` so a previously managed advisor is not left as a misleading saved seat. Apply only after a clean preview. Then point the native policy at the loaded role names:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --executor-agent <reported-executor-agent-name> \
  --advisor-agent <reported-advisor-agent-name> \
  --apply
```

Omit `--advisor-agent` when none is configured. `--personal-route-names` generates stable CODEX_HOME-specific names and prints them for the native command. The native configurator verifies exactly one matching personal file and refuses a same-name project role in the current workspace. A custom-agent file is a stronger durable model/provider pin than a direct tool hint, but runtime identity is confirmed only when the host exposes it. Start a new task so Codex loads the role files.

These are two separate storage transactions. If the native command fails after the role transaction applied, immediately preview and then apply:

```bash
python3 <skill-dir>/scripts/configure_orchestration.py \
  --scope personal \
  --personal-route-names \
  --codex-bin <active-codex-binary> \
  --remove-saved-roles
```

Remove only files the configurator validates as managed. If cleanup fails or the operation was interrupted, stop and run native `--status --require-effective`; report each orphaned managed role for manual review. Never claim the two stores changed atomically. On Windows, new managed roles can be created, but updating or removing an existing role fails closed; explain that limitation before choosing the custom-agent path.

The standalone configurator also retains project-scoped saved roles, safe removal, and opt-in migration for releases 0.1–0.3. It must never change the root model, permissions, credentials, or global agent limits.

## Preserve Codex's decisions

The current task model remains the root. It owns intent, planning, architecture, decomposition, delegation, integration, review, final verification, and the final answer.

Codex decides whether a plan helps, whether any work is safely delegable, how many independent slices exist, and whether parallelism is worth its context and integration cost. Keep simple, tightly coupled, context-heavy, and root-owned work with the root.

This skill and its saved policy must never:

- create a second orchestrator;
- force a spawn or fixed worker count;
- create or change Goal state;
- weaken approvals or permissions;
- create nested executor teams;
- let a Planner or Advisor contact the other role directly;
- let an advisor direct executors;
- parallelize overlapping writes;
- silently substitute the root model for an unavailable child route.

An explicit `no subagents` instruction always wins. A current-task seat override wins over the saved default for that task only.

## Spawn routed children correctly

Inspect the callable subagent interface. A saved current preset should expose the routed tool under `agents`; if only `collaboration` is exposed, do not assume the expanded direct route works. For a task-local fallback, use whichever callable namespace is actually present and pass exact route controls only when its schema exposes them.

Every spawn that supplies `model`, `reasoning_effort`, or `agent_type` through this skill must use:

```text
fork_turns = "none"
```

A small positive partial fork is technically valid in Codex, but this skill deliberately requires `none`: it minimizes duplicated context and makes the root send a deliberate self-contained packet. Never use the default `all` with a different route. Full-history forks inherit the root model and Codex rejects the override.

For a direct Planner, Advisor, or Executor route, pass the exact configured model and concrete effort. For a custom route, pass the exact namespaced `agent_type`. Do not force a service tier; supported children may inherit Fast/priority from the parent, so tell users who prioritize allowance savings not to run the root in Fast mode.

Direct model overrides keep the root's provider. Before a direct spawn, establish that the target model is on the same provider. If it differs or cannot be established, mark the route unavailable and require a custom agent that pins `model_provider`.

After spawning, use the tool result or client metadata to confirm the accepted route. Distinguish:

- `native policy installed`: the managed user policy exists; v2 activation still depends on the selected root and effective workspace config;
- `pinned custom agent available`: a matching role is loaded, but has not run;
- `route accepted`: the current tool accepted and validated the requested route controls;
- `used and confirmed`: use only when the client explicitly exposes effective runtime model/provider/effort metadata;
- `inherited root — requested child model was not used`;
- `unavailable`: the requested route cannot run here;
- `root`: no Planner route is configured, so the root plans;
- `none`: no Advisor is configured.

Tool acceptance proves the requested route was valid and accepted, not necessarily that the client exposes post-start runtime identity. Child prose claiming a model name is not proof. If an exact route fails, report it to the root. An unavailable configured Planner or Advisor halts before Executor work unless the user explicitly made that seat best-effort for the current task; apply the bounded degradation rules below and disclose it. An unavailable Executor may leave work with the root only when the user did not require delegation or that Executor route. Never describe an unavailable route as successful.

## Planner and Advisor workflow

Planner is optional. When no Planner route is configured, the root creates and revises the plan. When configured, send the Planner one self-contained packet containing user intent, acceptance criteria, repository facts, constraints, proposed executor slices, risks, and verification. Require `PLAN_DRAFT`. Planner and Advisor report only to the root. They never edit, execute, spawn, contact one another, contact Executors, or release Executor work.

Advisor is optional. If none is configured, the root validates the Planner's draft and may continue. For a non-trivial plan with an Advisor, use this bounded approval loop:

1. Number the canonical plan version and send it to a fresh, stateless Advisor call.
2. Require `PLAN_APPROVED` or `PLAN_REVISE` as the first-line signal.
3. `PLAN_APPROVED` makes that exact version the approved plan. Stop reviewing immediately.
4. For `PLAN_REVISE`, assign stable IDs to material findings and send the canonical current version, latest critique, and compact cumulative findings ledger back to the same Planner route. If Planner is omitted, the root revises.
5. Require `PLAN_REVISION`, a complete `FINDINGS_LEDGER`, and the revised plan. Every latest finding must be `INCORPORATED` or `REJECTED` with a concrete reason. Reject stale source versions, missing or duplicated findings, and empty rationales.
6. Increment the version and send the new current plan plus compact ledger to a fresh Advisor call. Ask it to confirm or contest prior dispositions rather than repeat accepted findings.
7. Stop early on approval. Never exceed five total Advisor reviews.

Carry only the original constraints, current plan, and compact ledger between fresh calls; do not duplicate complete transcripts. The root owns the canonical plan, versions, ledger, round count, semantic validation, and Executor release. Planner and Advisor never contact one another directly.

If review five still returns `PLAN_REVISE`, halt before Executor work. Give the user the latest plan and version, complete ledger, latest unresolved findings, and choices to override, re-scope, or change a route. Never label it approved.

A configured Planner or Advisor is required by default. Route failure, malformed output, missing context, stale version, or invalid ledger halts before Executor work. Only an explicit current-task best-effort instruction permits degradation:

- if the configured Planner fails, disclose it and let the root assume Planner duties for the remaining rounds without resetting the five-review budget;
- if the Advisor fails, disclose it, end the loop, and label the latest validated plan `NOT_ADVISOR_APPROVED` before any allowed continuation.

Do not persist a best-effort flag. An explicit task override applies only to that task.

Reject persistent setup or task-local activation when configured Planner and Advisor routes are identical: the same direct model ID, same custom-agent name, or Fable in both seats. Independent critique is the reason for the Advisor role.

Fable Planner uses `create_plan` and `revise_plan`; Fable Advisor uses `review_plan`. These operations are seat-bound: never send a supplied Fable Planner to `review_plan`, and never use an Advisor route to create or revise the plan. The policy authorizes only the root to make these read-only calls; Executors must never use or direct them.

## Executor handoff

Give each executor one bounded packet with:

- objective and boundaries;
- only the context and repository facts it needs;
- owned files or explicit read-only scope;
- dependencies and stop conditions;
- acceptance criteria and smallest useful verification;
- required handoff format.

Require it to preserve unrelated work, stay inside the slice, avoid the advisor, avoid descendants, and report blockers rather than guess. The handoff includes status, work completed, files or evidence, checks run, and remaining risks.

Parallelize only genuinely independent slices with non-overlapping write ownership. The root inspects, integrates, and verifies every handoff. Executor completion is never final acceptance.

## Task-local and older-client fallback

When the persistent policy is unavailable, apply the supplied seats only to the work in the same invocation. Do not claim that a mutable team was saved.

Use the strongest exact control the current client exposes:

1. a matching loaded namespaced custom agent;
2. accepted direct `model` and `reasoning_effort` inputs with `fork_turns = "none"`;
3. a clearly labeled prompt preference when exact routing is unavailable;
4. `unavailable` when the provider or model cannot be reached.

For task-local `auto`, omit the reasoning-effort input. Never pass the literal string `auto` to a spawn tool; the effective inherited or host-chosen effort remains unverified unless the client exposes it.

Report a compact activation status and continue the included task:

```text
Codex Orchestration
Orchestrator: <active model or current task model> — active
Planner: <model>@<effort> — <route state>, or root
Executor: <model>@<effort> — <route state>
Advisor: <model>@<effort> — <route state>, or none
Delegation: Codex decides when it helps; Plan and Goal behavior unchanged
```

Never report a prompt preference or saved file as a model that actually ran. Report an exact tool call as `route accepted`; reserve runtime confirmation for explicit effective metadata.

## Keep savings language honest

The purpose is to spend high-end capacity where judgment matters and use an efficient coding model for eligible execution volume. Do not create agents solely to hit a percentage.

The “about 65%” example is a model-weighted credit calculation: at the published Luna rate of 20% of Sol, a comparable token mix with 20% on Sol and 80% on Luna costs `0.20 + (0.80 × 0.20) = 0.36`, about 64% fewer credits before orchestration overhead.

Never call that 65% fewer raw tokens, a guaranteed five-hour or weekly-limit saving, a fixed monetary saving, or five times more completed work. Advisor calls, duplicated context, retries, tools, Fast service tier, and unnecessary workers can reduce or erase the benefit.

## Resources

- `scripts/configure_native_routing.py`: one-time native setup, status, update, and disable.
- `scripts/fable_advisor_mcp.py`: fail-closed Claude Fable 5 planning and review bridge.
- `scripts/configure_orchestration.py`: namespaced custom agents, provider pins, safe removal, and legacy migration.
- `scripts/inspect_models.py`: fallible host-catalog diagnostics.
- [providers-and-models.md](references/providers-and-models.md): detailed capability, provider, compatibility, persistence, and usage boundaries.
