---
name: codex-orchestration
description: Use for natural-language questions or requests about whether Kimi K3 or another audited External Model is available or callable as Designer or another role, and for assigning models to Planner, Advisor, Designer, Executor, researcher, reviewer, writer, or supervisor roles. Also use when the user invokes Codex Orchestration to update the plugin, create custom roles, define a workflow, or set up, inspect, repair, change, disable, or temporarily override model routing. Keep the selected task model as root and preserve Codex's Goal, permissions, integration, and verification behavior.
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
/codex-orchestration setup designer: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High
/codex-orchestration Planner: Claude Fable 5 High, Designer: Kimi K3
/codex-orchestration --update
/codex-orchestration create project role: researcher
/codex-orchestration create personal roles: researcher, writer, reviewer
/codex-orchestration configure external role researcher with OpenRouter model moonshotai/kimi-k3 at max
/codex-orchestration call researcher at max — <one bounded task>
/codex-orchestration status
/codex-orchestration repair
/codex-orchestration disable
/codex-orchestration remove custom roles personally
/codex-orchestration executor: GPT-5.6 Terra high — <one task only>
is Kimi available to use as Designer?
can I use Kimi K3 for design?
```

Implicit invocation is discovery, not mutation authority. A natural-language
availability question authorizes only read-only inspection. It never authorizes
setup, repair, provider preparation, authentication, Gate 0 spend, role creation,
configuration writes, disconnect, or removal.

For a question such as `is Kimi available to use as Designer?`, enter this skill
implicitly and run `external status` from the installed skill before answering.
Never infer External Model availability from the currently exposed MCP or subagent
tool list. A visible Fable tool is not an exhaustive provider or role inventory.
Report the result in four separate terms:

- `supported`: the exact display name maps to a bundled audited manifest;
- `configured`: the exact provider/model/effort and role exist without drift;
- `locally ready`: status is `READY` and read-only `resolve` succeeds for the exact
  role and effort in the current workspace;
- `callable now`: a sealed `invoke` has successfully attested the active binary,
  required CLI controls and feature catalog, unchanged registry, and accepted the
  model call. Read-only discovery must report this as unconfirmed rather than make
  a model call.

Say Kimi K3 is available to use as Designer only when support, configuration, local
readiness, and callability are all true. Otherwise,
say it is supported but not yet callable, name the exact lifecycle state, and give
the next action. Never answer that the plugin does not expose Kimi merely because a
Kimi-specific tool is absent; the route is materialized through the External Model
lifecycle and a provider-pinned custom agent.

`--update` securely refreshes this plugin from its canonical Git marketplace. `setup` installs or updates the personal one-time routing policy. `create project role` or `create personal role` creates native Codex custom-agent files. `status` inspects built-in routing. `repair` restores only saved managed hint bytes after narrow drift validation. `disable` restores pre-setup values.

`remove custom roles` cleans only verified plugin-managed advisor/executor files. Arbitrary native roles are user-owned. An invocation with native seats and work but no control verb is a current-task override and must not rewrite config. Explicit External Model seat labels are the exception: they may perform only the clean preparation, connection, and readiness writes authorized in the External Model lifecycle below.

Explicit seat labels are authoritative. `planner:` configures only Planner, `advisor:` configures only Advisor, `designer:` configures only Designer, and `executor:` configures only Executor. Never infer or change a seat from a model's historical use, default role, cached description, or provider; in particular, never reinterpret a supplied `planner:` model as an Advisor or a supplied `designer:` model as an Executor. Saved defaults may fill only omitted seats and never override a seat supplied in the current invocation.

The executor is required for setup or a task-local override. It is not required for a custom-role creation request. Planner, advisor, and designer are optional: omitted planner means the current root model plans, omitted advisor means `advisor: none`, and omitted designer means `designer: none`. Do not ask separate planner or advisor questions, or a separate designer question, unless the user asks for help choosing them.

For both persistent setup and task-local overrides, reject an identical Planner and Advisor route: the same direct model ID, the same custom-agent name, or Claude Fable 5 in both seats. Independent critique is required.

If the executor is missing, ask only:

```text
Which executor model and effort should Codex use? You can optionally include a planner, advisor, and designer; omission uses the root as planner and no advisor or designer.
```

Because explicit skills may not reload from a bare reply, include a ready-to-copy line using the exact label shown by the client and preserve the original work:

```text
<exact-skill-label> setup executor=<model>@<effort-or-auto>, planner=<model>@<effort-or-auto>|root, advisor=<model>@<effort-or-auto>|none, designer=<model>@<effort-or-auto>|none
```

For a task-local request, append `— <original task>`. Keep every supplied modifier. Do not lose the user's task while collecting a model choice.

Before applying setup or starting task-local work, validate the normalized mapping
internally against the user's explicit labels. If an exact native seat cannot run,
report that seat as unavailable and stop under the required-route rules; never move
its model to another seat. For an External Model seat still crossing a valid
lifecycle boundary, report its exact lifecycle state and next action, not unavailable.
Status and diagnostic commands may show omitted defaults explicitly,
including `Designer: none` and `Advisor: none`; a successful activation
confirmation follows the concise contract below instead.

### Activation confirmation

After all requested task-local routes have been validated as callable in the
current task, respond with one plain line per explicitly supplied model-bearing seat
and preserve the user's seat order. Use this exact shape:

```text
Planner — Fable 5 high: Activated
Designer — Kimi K3: Activated
Executor — GPT-5.6 Sol high: Activated
```

Keep the role and model recognizable to the user. Normalize stable product
punctuation such as `GPT-5.6`, and render a supplied effort in lower-case readable
words; internal values such as `xhigh` remain routing details. Do not
append a defaulted effort that the user omitted; for example, the implicit Kimi K3
`max` remains part of route validation but not the display line.
Do not print omitted, `none`, or implicit-root seats. On a role-selection-only invocation, the
activation lines are the entire successful response: do not add a heading,
preamble, route internals, or delegation boilerplate.

Use `Activated` only after that exact route is locally ready and its sealed invoke
preflight succeeds for the active binary in the current task. It means available
for this task, not `used and confirmed` runtime identity.
If authentication, qualification, connection, restart, resolution, or another
required boundary remains, report the exact lifecycle state and next action instead of `Activated`.
Never mix a false activation line into a blocker response. For a
request that also contains task work, print the activation lines first and then
continue that work.

If an old prompt contains `orchestrator:`, explain that the current task model already owns that role. Ignore that seat instead of switching or persisting it.

Normalize `Extra High` to `xhigh`. For Claude Fable 5, accept `Low`, `Medium`, `High`, `XHigh`, `Max`, or `Ultra`. Omission or `Auto` means `High`; `Ultra` is a user-facing alias for Claude Code's actual `max` setting and must be reported as that mapping. Route Fable with `--planner-fable --planner-effort <normalized-effort>` or `--advisor-fable --advisor-effort <normalized-effort>`, not through the Codex model catalog. Resolve every other display name to an exact ID only through the executing host's model catalog, picker, a loaded custom agent, or official provider documentation. Never invent an ID. For persistent direct routing, resolve `auto` to the catalog's concrete default.

Persistent Designer accepts only a direct same-provider model, not a Fable MCP
or unqualified custom-agent route. Route it with `--designer-model` plus
`--designer-effort`. A Designer route may share a model with another seat; only
Planner and Advisor require independent routes. For a cross-provider Designer,
create and invoke a task-local External Model role named `designer`; `resolve` must
reject matching project agents in the current workspace or any ancestor immediately
before returning its route. Codex does not yet expose a scope-qualified agent identity,
so persisting an agent name would let a later project's agent shadow it.

Read [providers-and-models.md](references/providers-and-models.md) before setup, when clients disagree, when a model is absent, when providers differ, or when custom agents or legacy migration are involved.

## Update the plugin

Treat `/codex-orchestration --update` as an explicit request to update only this
plugin. It cannot be combined with setup, status, disable, seat settings, custom
role operations, or task work. Resolve the absolute Codex binary used by the active
host. First run `codex plugin list --json` and require exactly one enabled
`codex-orchestration@codex-orchestration` entry whose marketplace source type is
`git` and source is the canonical HTTPS GitHub repository. Refuse local, disabled,
missing, duplicate, or unexpected sources without mutation. Then run only:

```bash
codex plugin marketplace upgrade codex-orchestration --json
codex plugin add codex-orchestration@codex-orchestration --json
codex plugin list --json
```

Use that active absolute binary for all commands and let Codex's native plugin
manager own transport, process containment, cache writes, and installation. Do not
wrap these commands in a custom downloader, Git client, script, or credential-bearing
environment. Require the upgrade result to select only this marketplace with no
errors, the add result to report a SemVer newer than or equal to the original, and
the final inventory to retain the exact canonical source and enabled state at that
version. Never run `plugin remove`, rewrite Codex config, read credentials, or
inspect/touch routing, chats, or sessions. If a native command or post-check fails,
report the exact phase as failed and do not claim rollback.

On success, report the old and new versions and tell the user to restart Codex
Desktop and start a new task. The current task keeps the already loaded skill
instructions; no updater can replace those in place. If the plugin is installed
from a local or noncanonical marketplace, stop and explain that automatic update
is intentionally unavailable for that source.

## External Model roles

Use this path when the user wants Codex Orchestration to own a model that should not
appear in the Desktop model picker. Read
[external-models.md](references/external-models.md) completely before preparing,
qualifying, creating, resolving, disconnecting, or removing an External Model role.

The root model and its ChatGPT/OpenAI login remain untouched. Never write top-level
`model` or `model_provider`, never edit the Desktop picker, and never inspect,
migrate, archive, or delete a chat or session. An External Model is represented only
by a reviewed provider adapter, strict non-secret state, and provider-pinned personal
agent variants.

Accept natural-language forms such as:

```text
configure external role researcher with <provider> model <exact-id> at <effort>; job: <purpose>
configure external role designer with <provider> model <exact-id> at <effort>; job: <design purpose>
call researcher at max — <bounded task>
use reviewer@high for <bounded task>
external status
disconnect external role researcher
remove external provider openrouter
```

Only bundled provider manifests are eligible. Do not turn an arbitrary URL, model
name, shell command, project file, or subscription CLI into a provider. Resolve the
exact model and supported efforts from the manifest and its cited evidence. Reject
all unsupported effort values; never clamp, alias, or silently fall back.

### External models supplied as seat labels

Treat a supplied built-in seat whose model unambiguously matches a bundled external
model as an explicit External Model seat assignment. In particular, normalize
`Designer: Kimi K3`, case-insensitively, to role `designer`, provider `openrouter`,
model `moonshotai/kimi-k3`, and effort `max`; an omitted or `auto` effort also means
`max`. Reject every other explicit Kimi K3 effort. This is a manifest-backed display
name mapping, not permission to guess model IDs or accept fuzzy, dated, or `latest`
aliases.

An external Designer is not a native direct-model Designer: never pass it to `--designer-model`,
the root-provider model catalog, or the persistent routing schema; always inspect `external status` first and compare the requested role, provider,
model, and effort with the strict registry result. Then follow exactly one state:

- If the exact role is `READY`, invoke it only with the configurator's sealed
  `invoke` subcommand and an absolute active-host `--codex-bin`; pass the bounded
  packet on stdin. Never execute an External Model role with `agents.spawn_agent`
  (or any native spawn-agent namespace). `resolve` remains a read-only diagnostic.
- If the exact role is `RESTART_REQUIRED`, tell the user to fully quit and reopen
  Codex and start a new task. In that new task, preview and apply `ready`; then use
  sealed `invoke` only after readiness succeeds.
- If the provider is exact, authenticated, and `CAPABILITY_VERIFIED` or `READY`, but
  role `designer` is absent, the explicit seat assignment authorizes clean role
  creation: preview and apply `connect` with the bounded purpose "Produce a design
  handoff for the root; do not edit implementation code, direct other roles, or
  spawn descendants." Report the resulting `RESTART_REQUIRED` boundary. It does
  not authorize replacing or disconnecting an existing role.
- If the provider is absent, treat the explicit seat assignment like a literal
  configure request: preview and apply only clean provider preparation, then follow
  the existing hidden authentication flow. Preparation may add the exact audited
  OpenRouter provider entry when absent, but never modifies or removes a pre-existing provider entry. If authentication is missing, print the required no-paste message
  and enrollment command, then stop.
- If the exact tuple is not qualified, request separate explicit billing approval
  immediately before Gate 0. A seat assignment never authorizes Gate 0 billing,
  credential entry, retries after a failed probe, or any other spend.
- If the role exists with another provider/model, any file or helper drifts, a
  project agent shadows it, or status is ambiguous, stop with the exact collision or
  integrity blocker. Never overwrite, repair, disconnect, or substitute a route.

At every non-ready state, describe the next exact action; do not report the requested external seat as unavailable merely because its personal agent has not been created
or loaded yet; preserve the original task and every supplied seat while crossing an
authentication, qualification, or restart boundary. A seat-only invocation is a
role-provisioning request only when it contains only External Model seat labels; that
form does not require Executor. If the invocation also supplies any native seat or
contains task work, preserve every supplied seat and that work, then collect a missing Executor using the existing question and ready-to-copy invocation after reporting
or progressing the external role state.

External seat assignments remain task-local. The returned custom-agent name must not be persisted in native routing state because a project agent could shadow that
unqualified name in a later workspace. This rule does not change native same-provider
Designer setup or Claude Fable 5 Planner/Advisor routing.

Native setup is preview-first and uses
`scripts/external_configurator.py` from this skill's real installed directory. Its
stages are `prepare`, external authentication, explicitly authorized billable
`gate0`, `connect`, a new Codex task, and `ready`. A literal configure request
authorizes clean preview and preparation, but not entering a key or spending on Gate
0. Obtain separate explicit approval for `--acknowledge-billing` immediately before
that probe. Apply role creation only after Gate 0 succeeds for the exact
provider/model/effort tuple.

When authentication is missing, say exactly this before stopping:

```text
External provider authentication is required. Do not paste the API key into this chat. Run the displayed enrollment command in a trusted local terminal; its hidden local prompt stores the key in your operating-system credential store. Tell me when that command succeeds.
```

Never ask the user to paste, upload, dictate, or save a provider key in chat. Never
place one in a command argument, environment file, TOML, registry, journal, prompt,
test, log, Git file, issue, or pull request. Do not run the enrollment command for
the user: they must enter the value through the OS prompt outside chat. The durable
provider table may contain only documented command-backed auth fields pointing to
the stable helper under `CODEX_HOME` or an explicitly trusted absolute user helper.

`gate0` runs one fixed, ephemeral, read-only request in a temporary `CODEX_HOME` and
may incur provider cost. Treat success as `CAPABILITY_VERIFIED` and route acceptance,
not runtime model confirmation. OpenRouter officially lists the exact Kimi K3 tuple
`moonshotai/kimi-k3` with only `max` reasoning. For this model, `auto` resolves to
`max`; reject every other explicit effort instead of clamping it. The bundled
adapter is no longer experimental, but each installation remains unqualified until
that exact tuple passes its explicitly authorized Gate 0. Never substitute a dated
or `latest` Kimi alias.

`connect` creates one personal provider-pinned custom-agent variant for every
manifest-validated effort. After a new task and exact integrity check, `resolve`
maps the requested role and effort for diagnostics. Execute only with sealed
`invoke`, never a native spawn-agent tool. Report `route accepted` when the direct
CLI accepts it. Report `used and confirmed` only from mechanical
host/provider/rollout metadata; model self-identification is never evidence.

For an unavailable provider, effort, auth helper, role file, or readiness state,
stop and report the exact blocker. `CLI_CHANGED`, `CONFIG_DRIFT`, `ROLE_COLLISION`,
and `RECOVERY_REQUIRED` are not best-effort states. Preview disconnect and removal,
then apply only exact plugin-owned bytes and exact provider config. Preserve edited
or ambiguous data for manual recovery. An intentionally replaced user helper may be
accepted only through preview/apply `trust-helper` at the same absolute path, which
clears qualification and requires authentication plus Gate 0 again.

Claude Fable 5 remains the sealed first-party subscription adapter. It continues to
use only its Planner/Advisor MCP operations and existing first-party login,
no-tools, no-session-persistence, runtime-model-metadata contract. Do not route it
through the native External Model provider configurator and do not generalize its
adapter to arbitrary CLIs.

## Create arbitrary custom roles

Use native Codex custom-agent files for roles beyond the built-in planner, advisor, designer, and executor seats. Examples include researcher, reviewer, writer, supervisor, security auditor, browser debugger, or domain expert.

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
2. Resolve the exact executor and optional Planner, Advisor, and Designer IDs and efforts from that host.
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

Add `--designer-model` and `--designer-effort` for a persistent same-provider
Designer. Designer omission persists `designer: none`. Designer cannot use the
Fable MCP route or a persistent custom-agent name. Use a task-local External Model
role named `designer` for cross-provider design work so the root can validate its
personal file and reject project shadowing immediately before the bounded call.

The configurator capability-tests the complete four-field preset on the active target, `codex` on PATH when different, the known macOS Desktop binary when present, and every explicit `--compat-bin`. A successful isolated config probe means that client can parse the preset; it is not a live child-model confirmation. Report `route accepted` or `used and confirmed` only from the exact live spawn evidence defined below. Ask about other Codex/IDE installations that share this config only when the environment suggests they exist, and pass their binaries explicitly. If the request or active host indicates a named `--profile`, explain that normal setup manages the default user layer and is not verified for that profile; do not add a routine question for users with no profile signal. If a checked client rejects any managed field, stop before apply. Recommend updating it or using the task-local fallback. `--allow-incompatible-client` requires a separate explicit user decision because it can make the shared config unreadable to that client.

For the current validated v2 direct route, set `tool_namespace = "agents"`. Live testing on Desktop `0.144.0-alpha.4` showed that the default reserved `collaboration.spawn_agent` schema rejected expanded model/effort metadata, while `agents` accepted the same request and spawned Luna at `xhigh`. Treat this as a required control-surface setting for that tested path, not as the executor selection. `usage_hint_text` carries the actual Planner, Advisor, Designer, and Executor routes.

Do not add `enabled = true` for a Sol or Terra root. Their current model metadata selects v2. The configurator intentionally manages these routing fields:

- `features.multi_agent_v2.hide_spawn_agent_metadata`;
- `features.multi_agent_v2.tool_namespace`;
- `features.multi_agent_v2.multi_agent_mode_hint_text`;
- `features.multi_agent_v2.usage_hint_text`.

When Claude Fable 5 is selected, it additionally manages only the plugin-scoped `enabled` override for the chosen bundled MCP launcher and any launcher variant already overridden by the user. All bundled variants are disabled by default. The original override values are stored and restored by `disable`. Codex's TOML editor may retain an inert empty table header after deleting the last override; never rewrite the file merely to remove that cosmetic header.

It uses Codex App Server's `config/read` and `config/batchWrite` APIs, not a home-grown TOML rewrite. It preserves unrelated settings and comments, validates the whole effective config, and uses the user-layer version to detect races. Restore snapshots cover the four routing fields plus the narrowly scoped MCP overrides only when Fable is selected; the namespaced state also records schema/version markers, config path, selected seats, and scalar-conversion metadata when needed. If the user explicitly replaces existing hint text, the exact prior text is stored for restoration; warn them never to place credentials in routing hints.

If a user-authored mode or usage hint already exists, do not replace it automatically. Show the conflict. Use `--replace-existing-policy` only after the user explicitly approves replacing and later restoring those exact values.

## Status, repair, change, and disable

For status:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --status

python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --status --require-effective
```

Run status from the target project. The first form is descriptive. Use `--require-effective` for automation and release gates; it returns nonzero for incompatible clients, conflicts, overrides, incomplete controls, an unavailable Fable or custom-agent route, or orphaned v0.4+ personal roles. Report the current task model as the orchestrator, Planner (`root` when omitted), configured Advisor, Designer, and Executor, whether the personal policy is installed and effective in that workspace, whether effective spawn controls are visible, whether the effective tool namespace is `agents`, the target config path, and checked-client compatibility. State that neither status form proves a live route or infers v2 activation for the model selected in a task; current Sol or Terra is the intended root.

When status reports `managed fields conflict with local restore state`, do not run
setup or disable over the conflict and do not assume authentication failed. A literal
`repair` request makes the valid saved plugin state authoritative only for the
following narrow recovery. Dry-run first, then apply only after the preview says the
drift is limited to saved managed mode/usage hints:

```bash
python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --repair

python3 <skill-dir>/scripts/configure_native_routing.py \
  --codex-bin <active-codex-binary> \
  --repair --apply
```

Repair must require valid saved state and both live hints to retain the plugin
ownership marker. It must refuse namespace, spawn-metadata, Fable launcher,
scalar-shape, missing/unmarked hint, or other managed drift. It writes only the
different mode/usage fields through App Server compare-and-swap, leaves the original
restore snapshot and seat records byte-for-byte unchanged, checks user and effective
readback, rolls the two fields back when a higher layer overrides them, and preserves
a concurrent config or saved-state edit instead of overwriting it. It never reads or
changes authentication, credentials, chats, or sessions. After apply, run status with
`--require-effective` and fully quit and reopen Codex before starting a new task.

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

In user-facing diagnostic status or operation results, use the exact name `Claude Fable 5`.
The concise activation confirmation preserves the supplied `Fable 5` label when that is what the user wrote, as shown in its exact example.
Report authentication as `first-party login ready`; do not expose or restate Claude account-plan metadata.

Prerequisites:

- the official `claude` CLI is installed;
- `claude auth status` reports a first-party Pro or Max login;
- a Python 3.11+ launcher is available.

The plugin packages three disabled MCP launcher variants for macOS, Linux, and Windows. Setup enables exactly one compatible variant through the plugin's namespaced config when either Fable seat is selected. At planning or review time the MCP server removes API-key and Bedrock/Vertex/Foundry override variables, re-checks first-party login, and invokes `claude -p --model claude-fable-5` with `--safe-mode`, no tools, no session persistence, prompt suggestions disabled, and JSON output. Each saved seat pins its model and effort; the root cannot replace them through tool arguments.

Fable effort is configurable per setup. The default is `high`; supported Claude Code values are `low`, `medium`, `high`, `xhigh`, and `max`. Accept `ultra` as an alias for `max`, save the effective Claude Code value, and disclose the alias mapping in setup output. Existing saved `max` routes remain valid.

The bridge exposes only bounded, read-only planning operations. `create_plan` accepts one self-contained packet and requires `PLAN_DRAFT`. `revise_plan` requires the task, canonical current plan, latest critique, and compact findings history, then requires `PLAN_REVISION` plus a findings ledger and revised plan. `review_plan` remains the Advisor operation and requires `PLAN_APPROVED` or `PLAN_REVISE`. Every call uses the same full saved-state validator as native status/repair/disable, then requires runtime `modelUsage` to contain the pinned `claude-fable-5` primary plus only the bridge's explicit exact helper allowlist. Return every observed ID in `used_models`; an unknown additional or missing primary model makes the seat unavailable. Any auth, transport, state, format, or model-confirmation failure makes that seat unavailable; it never counts as approval. The bridge returns no account identifier or credential.

Plugin and policy updates cannot replace the MCP process already loaded into the
current task. If a Fable call fails after an update or repair, run fresh native
status. When status reports Claude Fable 5 `ready — first-party login`, classify the
current tool failure as a stale loaded bridge, not an authentication failure. Do not
request re-authentication. Fully quit and reopen Codex, then start a new task so the
installed bridge, policy, and saved state load together. Ask for login only when the
fresh status check itself reports authentication unavailable.

The managed workflow reserves these MCP calls for the root Codex model. Current MCP requests do not carry caller identity, so the bridge cannot independently authenticate root versus child; caller isolation is instruction-enforced. The bridge still mechanically prevents tools, edits, permission prompts, and session persistence. Never describe the caller boundary as engine-enforced.

## Durable or cross-provider custom agents

Direct `model` routing is same-provider. The audited External Model path above may prepare one bundled provider safely. Every unbundled provider still needs an already authenticated Codex-compatible provider and a loaded custom agent that pins `model_provider`.

For a cross-provider Planner, create a bounded personal custom role through the arbitrary-role or audited External Model flow, start a new task so it loads, and pass its exact name with `--planner-agent`. For a cross-provider Designer, create a task-local External Model role named `designer` and invoke it only after current-project validation; do not persist its unqualified agent name. The older standalone managed-role helper below continues to own only its existing Advisor and Executor files.

Use the existing standalone-agent configurator for an unbundled provider path. Personal scope is required for machine-local provider IDs and affects all projects, so the user's explicit cross-provider `setup` request must name or confirm the existing provider ID. Never create an unreviewed provider definition, collect keys in chat, or write credentials.

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
- let Designer contact Planner, Advisor, or Executor directly;
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

For a direct Planner, Advisor, Designer, or Executor route, pass the exact configured model and concrete effort. For a custom route, pass the exact namespaced `agent_type`. Do not force a service tier; supported children may inherit Fast/priority from the parent, so tell users who prioritize allowance savings not to run the root in Fast mode.

Direct model overrides keep the root's provider. Before a direct spawn, establish that the target model is on the same provider. If it differs or cannot be established, mark the route unavailable and require a custom agent that pins `model_provider`.

After spawning, use the tool result or client metadata to confirm the accepted route. Distinguish:

- `native policy installed`: the managed user policy exists; v2 activation still depends on the selected root and effective workspace config;
- `pinned custom agent available`: a matching role is loaded, but has not run;
- `route accepted`: the current tool accepted and validated the requested route controls;
- `used and confirmed`: use only when the client explicitly exposes effective runtime model/provider/effort metadata;
- `inherited root — requested child model was not used`;
- `unavailable`: the requested route cannot run here;
- `root`: no Planner route is configured, so the root plans;
- `none`: no Advisor or Designer is configured for that seat.

Tool acceptance proves the requested route was valid and accepted, not necessarily that the client exposes post-start runtime identity. Child prose claiming a model name is not proof. If an exact route fails, report it to the root. An unavailable configured Planner or Advisor halts before Executor work unless the user explicitly made that seat best-effort for the current task; apply the bounded degradation rules below and disclose it. A configured Designer failure blocks work that explicitly requires its design handoff, but does not block unrelated Executor work; the root owns design when Designer was omitted. An unavailable Executor may leave work with the root only when the user did not require delegation or that Executor route. Never describe an unavailable route as successful.

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

## Designer handoff

Designer is optional and root-directed. Use it after any required plan approval
when visual design, UX, interaction flow, information architecture, or a design
system would materially improve the result. Give it one bounded packet containing:

- approved requirements and the exact design question;
- target users, platform, constraints, and acceptance criteria;
- required deliverables and handoff format;
- explicit ownership of any design artifacts it may edit;
- implementation boundaries and known dependencies.

Designer may edit only explicitly delegated design artifacts. Otherwise it returns
a design specification or handoff. It never revises the canonical plan, changes
implementation code, releases Executor, contacts Planner, Advisor, or Executor, or
spawns descendants. The root validates the design handoff, resolves conflicts with
the approved plan, and decides what implementation packet Executor receives. A
Designer may use the same model as another seat because independent critique is not
its purpose; the Planner/Advisor route-separation rule remains unchanged.

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

When every supplied fallback route is ready, use the same concise activation
confirmation contract above and continue the included task. When any route is not
ready, report only the affected seat's exact route state, blocker, and next action;
do not label it `Activated`.

Never report a prompt preference or saved file as a model that actually ran. Report an exact tool call as `route accepted`; reserve runtime confirmation for explicit effective metadata.

## Keep savings language honest

The purpose is to spend high-end capacity where judgment matters and use an efficient coding model for eligible execution volume. Do not create agents solely to hit a percentage.

The “about 65%” example is a model-weighted credit calculation: at the published Luna rate of 20% of Sol, a comparable token mix with 20% on Sol and 80% on Luna costs `0.20 + (0.80 × 0.20) = 0.36`, about 64% fewer credits before orchestration overhead.

Never call that 65% fewer raw tokens, a guaranteed five-hour or weekly-limit saving, a fixed monetary saving, or five times more completed work. Advisor calls, duplicated context, retries, tools, Fast service tier, and unnecessary workers can reduce or erase the benefit.

## Resources

- `scripts/configure_native_routing.py`: one-time native setup, status, seat changes, and disable.
- `scripts/fable_advisor_mcp.py`: fail-closed Claude Fable 5 planning and review bridge.
- `scripts/configure_orchestration.py`: namespaced custom agents, provider pins, safe removal, and legacy migration.
- `scripts/inspect_models.py`: fallible host-catalog diagnostics.
- `scripts/external_configurator.py`: preview-first External Model provider, Gate 0, role, status, recovery, and removal lifecycle.
- `scripts/external_auth_helper.py`: stable OS credential-store reader for documented command-backed provider auth.
- `scripts/external_subscription.py`: sealed dispatch through the existing Claude Fable 5 bridge.
- [providers-and-models.md](references/providers-and-models.md): detailed capability, provider, compatibility, persistence, and usage boundaries.
- [external-models.md](references/external-models.md): External Model trust lanes, lifecycle, commands, secret handling, Kimi status, and adapter-extension contract.
