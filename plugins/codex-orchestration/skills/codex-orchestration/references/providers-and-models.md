# Models, Providers, and Routing Boundaries

Use this reference for setup, client compatibility, custom providers, or a route that does not behave as requested.

## The shortest correct model

1. The model selected for the Codex task is the root orchestrator.
2. A current Sol or Terra root uses multi-agent v2.
3. The saved policy tells the root which exact route to request for an optional Planner, optional Advisor, and delegated Executors.
4. Codex still decides whether a spawn helps.
5. Every different-model, different-effort, or custom-agent child uses `fork_turns = "none"` and a self-contained packet.
6. The root reviews and verifies all child work.

The policy tells Codex which routes to request for configured planning, review, and delegated execution. It does not force every task to delegate or add a second scheduler.

## Current capability matrix

These facts were source-checked and runtime-tested on July 10, 2026. Always capability-test the actual host because the fields are still evolving.

| Capability | Current behavior | Consequence |
| --- | --- | --- |
| Sol model metadata | `multi_agent_version = v2` | A Sol root uses v2 without forcing the feature flag. |
| Terra model metadata | `multi_agent_version = v2` | A Terra root also uses the native policy. |
| Luna model metadata | `multi_agent_version = v1` | Luna is suitable as a v2 child, but a Luna root does not activate this v2 policy. |
| `hide_spawn_agent_metadata = false` | Shows `agent_type`, `model`, `reasoning_effort`, and `service_tier` on v2 spawn | Required for direct route control; it does not select a route alone. |
| `tool_namespace = "agents"` | On live-tested Desktop `0.144.0-alpha.4`, the default `collaboration` namespace rejected expanded model/effort metadata; `agents` accepted it and spawned Luna at `xhigh`. | Required for this validated direct-routing path. It changes the callable namespace but does not select Luna. |
| `usage_hint_text` | Appended to the spawn tool description | Carries the exact Planner/Advisor/Executor routes where the root chooses children. |
| `multi_agent_mode_hint_text` | Replaces the default proactive/explicit mode hint and is sent to root and child tasks | Must contain both root and child boundaries. |
| Claude Fable 5 MCP route | Root-directed `create_plan`, `revise_plan`, and `review_plan` tools invoke the authenticated Claude Code CLI headlessly with no model tools | Built-in cross-provider Planner or Advisor exception; current MCP requests do not provide caller identity, so caller isolation is policy-enforced. |
| `fork_turns` default | `all` | Different model/effort/role overrides are rejected unless the call uses `none` or a positive partial fork. |
| Effective concurrency | Determined by the active Codex version and `agents.max_threads` configuration | This plugin never changes the limit or forces a worker count. |
| Older CLI 0.142.5 | Rejects `multi_agent_mode_hint_text` as an unknown feature-table field | Never write the global native policy without checking every known shared-config client. |

The installer does not infer this from version strings. It launches each detected binary with an isolated `CODEX_HOME` and probes whether it can parse all four managed fields. That is a config-compatibility check, not proof of a live child route.

## Why `enabled = true` is omitted

Current resolution prefers the selected model's `multi_agent_version` over the global feature flag. Sol and Terra already select v2, so the one-time setup does not need to force it.

Forcing `features.multi_agent_v2.enabled = true` can:

- show an under-development feature warning;
- conflict with an older `agents.max_threads` setting;
- change behavior for unrelated root models without the user asking.

If the user's config uses the older scalar form `multi_agent_v2 = true|false`, the configurator temporarily converts that value to the equivalent table form and records the original scalar. Disable restores the exact boolean only if no other table fields were added afterward.

## What the four managed fields do

The control surface and the route are separate:

- `hide_spawn_agent_metadata = false` exposes the model, effort, agent-type, and service-tier spawn inputs;
- `tool_namespace = "agents"` makes the expanded route callable on the currently validated Desktop build;
- `multi_agent_mode_hint_text` carries the root/child behavior and safety boundaries;
- `usage_hint_text` carries the exact optional Planner, optional Advisor, and required Executor routes.

`multi_agent_mode_hint_text` describes the policy:

- current task model is the one root orchestrator;
- Codex decides whether delegation is useful;
- optional Planner drafts and revises through the root; omitted Planner means the root plans;
- optional Advisor is directed through the root and reviews through a five-round bounded approval loop before Executor work;
- executor packets are bounded and self-contained;
- children do not create descendants;
- user overrides and `no subagents` win;
- Goal, permissions, approvals, and worker counts are not changed.

`usage_hint_text` attaches the route to the spawn tool itself:

```text
planner  -> model="gpt-5.6-sol", reasoning_effort="high", fork_turns="none"
advisor  -> model="gpt-5.6-terra", reasoning_effort="high", fork_turns="none"
executor -> model="gpt-5.6-luna", reasoning_effort="xhigh", fork_turns="none"
```

For a durable custom-agent route it uses:

```text
agent_type="codex_orchestration_executor", fork_turns="none"
agent_type="codex_orchestration_advisor", fork_turns="none"
```

For Claude Fable 5 it names the enabled bundled MCP server and tells the root to use `create_plan`/`revise_plan` for the Planner seat or `review_plan` for the Advisor seat. These are root tool calls, not `spawn_agent`, so `fork_turns` does not apply.

The custom mode text is visible in spawned children too. That is why it says: if root, orchestrate; if child, stay within the packet and never spawn.

## Routing strength and its honest boundary

There is no global Codex field named `executor_model`. The native same-provider route combines:

- visible v2 spawn metadata under the validated `agents` namespace;
- persistent spawn-tool guidance;
- a model-visible exact `model` and `reasoning_effort` input;
- runtime catalog validation when the tool call is accepted;
- optional effective-runtime confirmation when the client exposes it.

That is strong routing, but it is not a separate engine-level scheduler. The root can still choose not to delegate. Tool acceptance proves Codex accepted and validated the requested route; it does not guarantee that every client exposes the effective post-start identity. If the model ignores the required route or the tool rejects it, report that mismatch rather than claiming success.

Setup runs before a future task chooses its root, so it cannot persist a mechanically verified future root-provider identity. Direct routes are valid only when the active task can establish that the requested model belongs to the inherited root provider. If provider identity is missing or ambiguous, fail closed and use a provider-pinned custom agent.

A custom-agent file is the stronger persistent pin for a reusable role because the role config can set `model`, `model_reasoning_effort`, and `model_provider`. A stronger live parent override can still win, so confirm the effective child metadata either way.

## Forking rules

V2 `spawn_agent` defaults to a full-history fork. Full-history children inherit the root model, provider, and reasoning effort. Codex therefore rejects `agent_type`, `model`, or `reasoning_effort` on a fork with `fork_turns = "all"`.

Use:

```text
fork_turns = "none"
```

and send a self-contained task packet. A small positive turn count also permits overrides, but `none` is the Codex-Orchestration default because it minimizes duplicate context and makes the handoff deliberate.

Correctness wins over context savings. If a bounded packet cannot carry the necessary context safely, keep the work with the root instead of forcing a cheaper child.

## Start with the executing host

Do not keep a static display-name alias table. Model IDs, efforts, access, providers, and model metadata change.

Resolve seats in this order:

1. active host's App Server `model/list` result;
2. current client model picker or accepted spawn controls;
3. a loaded namespaced custom agent;
4. exact binary catalog diagnostics;
5. official provider documentation;
6. user-supplied exact ID when the sources are ambiguous.

`scripts/inspect_models.py` and debug catalog commands are useful signals, not permanent APIs. A missing shell-CLI model does not prove a newer Desktop model is unavailable. Always report which binary and catalog supplied the model IDs for a persistent preset; do not call that a live route confirmation.

For task-local `auto`, omit the effort override and call the effective effort unverified until exposed. For persistent direct or custom-agent routing, resolve `auto` to the model's concrete catalog default so the root effort cannot leak into the child.

## Native persistence and restoration

`configure_native_routing.py` writes the personal user config because the policy is meant to work in later tasks and projects.

It uses the official App Server flow:

```text
initialize -> initialized -> config/read(includeLayers=true)
           -> config/batchWrite(expectedVersion=...)
           -> config/read verification
```

The App Server permits writes only to the user config. It performs full schema and managed-requirement validation, preserves TOML comments and unrelated fields through `toml_edit`, atomically persists the file, returns `okOverridden` when a higher layer wins, and rejects a stale user-layer version.

The configurator writes each owned nested field separately, except when converting a legacy boolean feature shape. It refuses to replace user-authored hint strings unless `--replace-existing-policy` was explicitly approved. Setup verifies both the user layer and the effective config in the current workspace; it rolls back when a project or managed layer already overrides the installed policy there.

Restore state lives at:

```text
~/.codex/.codex-orchestration-routing.json
```

It contains the prior and managed values of the four routing fields, chosen Planner/Advisor/Executor routes, schema/version markers, scalar-conversion metadata when needed, and config path. When Claude Fable 5 is selected for either planning seat, it also records only the plugin-scoped MCP launcher overrides that setup touched. It never copies provider definitions, auth stores, account identifiers, or credentials. A normal clean setup contains generated policy text, the namespace value, seat IDs, and restoration metadata. Explicit replacement must retain the user's exact old hint text so disable can restore it; routing hints must never contain credentials. State is written with a same-directory atomic replacement and restrictive file mode where supported. If persistence fails after config apply, the configurator rolls the config back using the returned version.

Disable compares every current managed value before restoration. If the user edited a managed field after setup, it stops instead of erasing that work. Without state, each surviving marker proves ownership only of that hint string. Disable may safely remove the marked string or strings, but it leaves metadata visibility and the tool namespace unchanged because their previous values are unknown.

## Shared-config compatibility

Desktop and CLI commonly share `~/.codex/config.toml`. A field supported by Desktop can prevent an older CLI from starting at all.

Setup automatically checks the installations it can identify:

- the supplied active-host binary;
- `codex` on PATH when different;
- the macOS Desktop embedded binary when present;
- every explicit `--compat-bin`.

Ask the user about alternate Desktop, IDE, container, or Windows installations that share the same home, because no open-source installer can discover every possible binary path. Pass each known path with `--compat-bin`.

If any checked binary rejects the complete preset, normal setup fails before writing. Preferred resolution: update that client. The per-task skill workflow remains available without a global policy. Successful parsing does not prove that a future task selected a v2 root or that a live model route was accepted.

`--allow-incompatible-client` is an escape hatch only after the user explicitly accepts that the named client may stop loading the shared config. Disable never blocks on this compatibility check; otherwise the policy could trap the user.

## Custom agents

Codex's reusable role format is one TOML file per custom agent:

```text
<project>/.codex/agents/*.toml
~/.codex/agents/*.toml
```

Project-scoped/legacy saves use these fixed names:

```text
codex-orchestration-executor.toml -> codex_orchestration_executor
codex-orchestration-advisor.toml  -> codex_orchestration_advisor
```

Personal roles used by the global native policy add a stable 12-character suffix derived from the canonical `CODEX_HOME` path:

```text
codex-orchestration-executor-<personal-id>.toml -> codex_orchestration_executor_<personal-id>
codex-orchestration-advisor-<personal-id>.toml  -> codex_orchestration_advisor_<personal-id>
```

This prevents accidental shadowing by the older fixed project names. The native configurator requires exactly one matching personal role and refuses a same-name project role in the current workspace. Because project roles have higher precedence, run status in each project before relying on a personal custom-agent route; a deliberately duplicated suffixed name can still shadow it.

The executor file says to implement only the root's bounded packet, preserve unrelated work, verify, report, and never spawn. The advisor file says to review only the root's packet, request a read-only sandbox, return `PLAN_APPROVED` or `PLAN_REVISE`, and never edit, delegate, or contact executors.

Custom agents load in a new task. Writing a file does not hot-load it into an existing task. A project-scoped role loads only from a trusted project. If the same role name exists in project and personal scope, report the collision instead of guessing precedence.

Treat a saved scope as one complete team anchored by its executor. A missing same-scope advisor means `advisor: none`; never silently borrow an advisor from another scope.

The standalone-agent configurator remains dry-run first, rejects symlinks and hard links, preserves supported metadata, journals multi-file transactions without storing config contents, refuses edited or user-owned files, and uses opt-in backup-first migration for known output from versions 0.1–0.3.

The role-file transaction and native App Server policy transaction are independent. After a phase-two failure, remove only fully validated newly managed roles. Native status reports collision-resistant managed personal roles that are not referenced by its current restore state, and `--require-effective` treats them as unhealthy. This recovery is compensating cleanup, not atomicity across the two stores.

On Windows, the custom-agent configurator can create a new role but refuses in-place update or removal of an existing managed role because it cannot prove the Unix inode/metadata-preservation contract. Native App Server policy setup and disable are separate and remain capability-tested through the active Codex binary.

## Provider boundaries

Direct v2 `model` overrides retain the parent's provider. They are the simplest route for an OpenAI root and OpenAI Luna/Terra child.

Claude Fable 5 is the explicit built-in exception for Planner or Advisor. The plugin does not pretend it is a Codex model or translate Anthropic into the Responses protocol. Instead, a disabled-by-default local MCP server invokes the official `claude` CLI with the user's first-party Pro or Max login. Setup enables one Python 3.11+ launcher variant, and disable restores every prior plugin override value. Codex's TOML editor can retain an inert empty table header after its final key is deleted; the configurator does not risk a broad TOML rewrite for cosmetic cleanup.

The bridge removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and Bedrock/Vertex/Foundry selection variables from the child environment. It re-checks `claude auth status`, pins `claude-fable-5` and the saved effort, disables tools and session persistence, disables prompt suggestions, and requires JSON runtime metadata to contain that primary model. Claude Code currently reports the internal helper `claude-haiku-4-5-20251001` during valid Fable calls; the bridge permits only that exact helper ID and returns every observed ID in `used_models`. Missing Fable or any unknown additional model fails closed. Helper rotation therefore requires a reviewed plugin update rather than a wildcard. Setup and status never make a model call.

The saved policy authorizes the root to call these planning tools and prohibits children from doing so. Current MCP requests provide no caller identity to the server, so that specific caller boundary is instruction-enforced, not server-authenticated. The bridge mechanically uses the same full saved-state validator as native status/disable, restricts the operation surface, and runs Fable without tools or persistence.

Saved state compatibility is explicit: schema 1 must carry policy version 1 and predates Fable and Planner; schema 2 must carry policy version 2 and may authorize only the historical Fable Advisor shape; schema 3 must carry policy version 3 and adds Planner. Schema and policy values must be actual JSON integers, not booleans or floats. Legacy state cannot contain fields introduced later; nested snapshots, scalar conversion, MCP launchers, and routes must match an emitted contract; and managed policy strings must carry the plugin marker before status, update, disable, or Fable trusts them. Unknown extensions intentionally fail closed.

Fable setup defaults to `high`. It accepts the Claude Code effort values `low`, `medium`, `high`, `xhigh`, and `max`; the user-facing label `ultra` normalizes to the effective Claude Code value `max` because the CLI has no separate Ultra setting. Setup checks the installed CLI's advertised choices before persisting the route. The bridge reads only the normalized saved value, so tool callers cannot raise the effort at review time. Existing saved `max` routes remain compatible.

A cross-provider seat normally needs:

1. a provider already defined and authenticated in the user's Codex config;
2. a personal custom agent that pins the provider, model, and effort;
3. a new task that loads that agent;
4. v2 spawn with the matching `agent_type` and `fork_turns = "none"`.

Never create provider definitions, request keys in chat, write credentials, or imply that an OpenAI login grants access to another provider.

Codex custom providers currently use the Responses wire protocol. An Anthropic Messages endpoint is not automatically compatible. Use a supported integration that the user has configured and tested, such as an appropriate Amazon Bedrock route where available.

## Planner and Advisor permissions

A task-local Planner or Advisor is planning-only by instruction. Do not claim it is mechanically read-only unless the effective child sandbox confirms that.

A saved advisor requests `sandbox_mode = "read-only"`, but live parent permission overrides may be reapplied to children. Keep the behavioral prohibition on edits and mutation even with the requested sandbox.

The Claude Fable 5 bridge is mechanically narrower than a child: its tools accept only bounded plan or review inputs, launch Claude with safe mode and no tools, and expose no edit or shell operation. It still has open-world model access, so every call must be deliberate and self-contained.

Planner or Advisor failure is never approval. Configured seats are required for a non-trivial Executor plan unless the user explicitly marks one best-effort for the current task. Transport failure, malformed output, missing context, stale plan versions, or wrong routes stop Executor work by default.

Every Advisor call is fresh and stateless. The root carries the canonical current plan, numbered version, and compact cumulative findings ledger. `PLAN_REVISE` returns to the same Planner route; `PLAN_APPROVED` stops the loop. The root allows at most five Advisor reviews. Review five without approval halts with the current plan, ledger, and unresolved findings instead of silently executing.

## Goals and task lifetime

This skill does not create, start, pause, clear, or alter a Goal. If the user already runs a Goal, the routing policy works inside the same Codex delegation flow.

Even when the write API requests user-config reload, this transient installer cannot retroactively rewrite the developer policy already compiled into another task. Start a new task after setup, update, disable, or custom-agent changes.

A personal policy can be overridden by a trusted project's `.codex/config.toml` or a managed layer. Run status from the target workspace. “Policy installed” describes the user layer; “effective in this workspace” additionally confirms that no higher-precedence layer replaces the managed fields there. Neither status proves that the model selected for a future task activates v2.

Named profile-v2 files are separate selected user layers. The default command does not start App Server with `--profile`, so its write/readback does not verify a named profile. A profile user must inspect that layer separately and ensure it does not override the four routing fields, or use the task-local fallback.

## Concurrency and service tier

The effective concurrency limit belongs to the active Codex version and `agents.max_threads` configuration. This plugin never changes that limit or forces a worker count. Codex should parallelize only independent slices with non-overlapping write ownership.

Child service tier can inherit from the parent when supported. There is no portable “force standard tier” spawn setting that works across current catalogs. If allowance savings are the priority, do not enable Fast/priority on the root.

## Truthful route states

Use precise language:

- `native policy installed`: managed user policy exists; activation still depends on root model and effective workspace config;
- `policy effective`: the managed fields win in the current workspace; this is still not a live spawn;
- `pinned custom agent available`: matching role loaded, not yet used;
- `route accepted`: exact controls were accepted and validated by the current tool;
- `unverified prompt preference`: no exact control available;
- `used and confirmed`: only when the client explicitly exposes effective runtime model/provider/effort metadata;
- `inherited root — requested child model was not used`;
- `unavailable`: provider/model/selector cannot run;
- `none`: advisor disabled.

Requested text, a config file, or child prose alone is not proof that a model ran.

## Usage and savings language

Keep these concepts separate:

- **Raw tokens:** every input, cached input, output, context, and tool-result token. Subagents can increase this total.
- **Codex credits:** token usage weighted by model-specific rates.
- **Included limits:** shared five-hour usage plus any applicable weekly limits; real consumption depends on model, context, reasoning, tools, caching, tier, and plan.
- **Other-provider usage:** separate billing or allowance.

The defensible “about 65%” example is:

```text
20% Sol + 80% Luna at 20% of Sol's token credit rate
= 0.20 + (0.80 × 0.20)
= 0.36, or about 64% fewer credits before orchestration overhead
```

Never promise 65% fewer raw tokens, a fixed weekly saving, a universal monetary saving, or five times more completed work.

## Primary sources

- [OpenAI: Subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [OpenAI: Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [OpenAI: Codex pricing and usage limits](https://learn.chatgpt.com/docs/pricing)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
