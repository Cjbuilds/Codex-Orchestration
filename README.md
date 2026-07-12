# Codex-Orchestration

Use your strongest model where judgment matters. Let a faster, cheaper model handle eligible execution work that consumes most of the context and usage.

The model you select when you start a Codex task is already the orchestrator. If you start with GPT-5.6 Sol Extra High, Sol plans, delegates, reviews, and answers. You only choose the executor and, if you want one, an advisor.

Set it up once. After that, use Codex normally—no skill tag in every prompt.

## Install

```bash
codex plugin marketplace add Cjbuilds/Codex-Orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new Codex task after installation. In the desktop app, type `/` and choose **Codex Orchestration**. You can also choose it with `$`; CLI and IDE users can find it through `/skills` or `$`.

Setup uses a small standard-library helper and requires Python 3.11 or newer. Codex uses `python3` on macOS/Linux or an available `py -3.11`/`python` launcher on Windows.

The picker is the source of truth for the exact label. Depending on the client, it may appear as `/codex-orchestration`, `$codex-orchestration`, or `$codex-orchestration:codex-orchestration`.

## Set it up once

For a Sol orchestrator and Luna executor:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High
```

That is the normal setup. The skill resolves the friendly `Extra High` label to Codex's `xhigh` effort ID. If you leave out the advisor, it means `advisor: none`; Codex does not ask a second unnecessary question.

This direct quickstart assumes the Sol root and Luna executor are available through the same OpenAI provider. Direct child model overrides keep the root provider; use the custom-agent path below when providers differ.

Add a second-opinion model when the extra review is worth it:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High, advisor: GPT-5.6 Terra high
```

Codex resolves the display names against the active model catalog, previews the personal config change, checks the active binary plus the known PATH/Desktop clients, and applies the clean setup. Pass any other installation to the configurator with `--compat-bin`. It never changes the model selected for the current task.

Then start a new task, select the model you want as orchestrator, and work normally:

```text
Implement the authentication changes and run the relevant tests.
```

You do not invoke Codex Orchestration again for ordinary work. The saved policy-guided route is already part of Codex's multi-agent flow.

Useful controls:

```text
/codex-orchestration status
/codex-orchestration status --require-effective
/codex-orchestration setup executor: GPT-5.6 Terra high
/codex-orchestration disable
```

Running `setup` again changes the saved route policy. `disable` restores the values that existed before setup and leaves unrelated Codex settings alone.

`status` distinguishes a policy that is merely installed from one that is effective in the current workspace. `status --require-effective` is the automation/release gate: it exits nonzero for incompatible clients, conflicts, overrides, incomplete controls, unavailable custom agents, or orphaned managed personal roles. Neither form proves a live child route or infers the selected task model's multi-agent version, so start the new task with a current v2 root such as Sol or Terra.

For one task only, use an inline override without changing the saved default:

```text
/codex-orchestration executor: GPT-5.6 Terra high — fix the failing tests below.
```

## What it feels like

```text
         Start a normal Codex task with GPT-5.6 Sol Extra High
                                |
                                v
                    SOL IS THE ORCHESTRATOR
              understand | plan | decide | delegate
                         /                 \
                        /                   \
            Simple or tightly          Non-trivial plan and
              coupled work              advisor configured
                  |                            |
                  v                            v
             Sol handles it            ADVISOR checks the plan
                                               |
                                               v
                                      Sol accepts or fixes advice
                                               |
                         +---------------------+
                         |
                         v
                Bounded execution would help?
                         /                 \
                       no                   yes
                       |                     |
                       v                     v
                  Sol continues       LUNA / TERRA executor(s)
                                      build bounded slice(s)
                         \                 /
                          +-------+-------+
                                  |
                                  v
                       SOL REVIEWS AND INTEGRATES
                         verifies | answers you
```

Codex still decides whether a plan is useful, whether to delegate, how many independent slices exist, and what can run safely in parallel. Plan and Goal behavior stay exactly where they already live in Codex.

“Use Luna for execution” means the saved policy tells the root to request the Luna route for every executor that Codex chooses to delegate to. It does not force a subagent for a one-line change, mechanically enforce the request, or stop the root from doing tightly coupled work. Forcing every edit through another agent would add overhead and work against Codex's own judgment.

If you say `no subagents`, that wins.

## The three roles

### Orchestrator: the lead

This is simply the model you selected for the task. It understands the request, makes the important decisions, decides whether delegation helps, integrates every handoff, runs final verification, and owns the answer.

Codex-Orchestration never asks you to configure a second top-tier orchestrator.

### Advisor: the second opinion

The advisor is optional. It sees a self-contained packet containing the requirements, repository facts, plan, proposed executor slices, risks, acceptance criteria, and verification checks.

It looks for missed requirements, bad assumptions, shallow tasks, overlapping file ownership, unsafe parallel work, and weak tests. It reports only to the orchestrator—never to an executor—and begins with one of these signals:

```text
PLAN_APPROVED   No material gap was found in the supplied plan.
PLAN_REVISE    Material gaps were found; prioritized fixes follow.
```

The orchestrator decides which advice to use. The advisor does not become a second boss, rewrite the plan behind the root's back, or supervise workers.

When configured, advisor review is a gate for a non-trivial executor plan unless the user explicitly says `advisor best-effort`. An unavailable or malformed advisor response is never treated as approval.

### Executor: the builder

An executor receives one bounded task packet: objective, relevant context, constraints, owned files, dependencies, acceptance criteria, and the smallest useful verification command.

It implements that slice and reports changed files, checks, blockers, and remaining risks to the orchestrator. It does not contact the advisor, broaden the project, or spawn another team.

## How the one-time config works

Codex-Orchestration does not add a proxy or invent a new scheduler. It configures the current Codex multi-agent tool.

The managed part of `~/.codex/config.toml` is conceptually this small:

```toml
[features.multi_agent_v2]
hide_spawn_agent_metadata = false
tool_namespace = "agents"

multi_agent_mode_hint_text = """
The current task model is the root orchestrator. Codex decides when delegation
helps. Routed children stay bounded and never spawn descendants.
"""

usage_hint_text = """
For executor work, spawn model = "gpt-5.6-luna",
reasoning_effort = "xhigh", fork_turns = "none".
Never silently substitute the root model.
"""
```

The real generated text also carries the advisor rules, task-packet contract, user-override rules, and an ownership marker used for safe updates and removal.

Four details matter:

1. `hide_spawn_agent_metadata = false` exposes the per-spawn `model`, `reasoning_effort`, `agent_type`, and service-tier inputs. It does not choose a child model.
2. `tool_namespace = "agents"` puts the configurable v2 team tools under `agents`. In the Desktop build live-tested on July 10, 2026 (`0.144.0-alpha.4`), the reserved `collaboration.spawn_agent` schema rejected the expanded model/effort route; `agents` accepted it and spawned Luna at `xhigh`. This is a control-surface setting, not an executor selector.
3. `usage_hint_text` puts the chosen Luna or Terra route directly on Codex's spawn tool. This is what tells the root which model and effort to request for delegated execution.
4. Codex-Orchestration deliberately uses `fork_turns = "none"` for every different child route. Full-history forks inherit the root and reject overrides; Codex can also accept a positive partial fork, but `none` avoids copied history and requires a deliberate self-contained packet.

The installer writes those values through Codex App Server's own `config/read` and atomic `config/batchWrite` APIs. That preserves unrelated tables, comments, inline comments, and custom multi-agent limits; validates the complete config; and detects concurrent edits. The policy takes effect in new tasks. A small namespaced state file records its schema, config path, selected seats, the four managed values, and the exact pre-setup snapshots needed by `disable`; it uses restrictive file permissions where supported. A normal clean setup contains generated policy text, the namespace value, seat IDs, and that restoration metadata. If you explicitly replace your own hint text, its exact old value must be kept for restoration—so never put credentials in routing hints.

The setup sets `tool_namespace = "agents"` because the currently validated Desktop route needs that namespace for expanded child-model controls. It changes the callable namespace; it does not name Luna, force a spawn, or replace Codex's delegation judgment.

The setup also does **not** force `enabled = true` for a Sol or Terra root. Current Sol and Terra model metadata already selects multi-agent v2. Forcing the feature globally can produce an under-development warning and can conflict with older `agents.max_threads` configuration.

## What config can and cannot prove

The full policy becomes durable spawn-tool guidance for later tasks that use a v2 root and do not override it with a higher-precedence config layer. Codex still decides whether to delegate and must issue the requested route; exact child use is confirmed only by runtime evidence. This is policy-guided routing, not an engine-level executor selector.

But these two lines alone are not an executor configuration:

```toml
[features.multi_agent_v2]
hide_spawn_agent_metadata = false
tool_namespace = "agents"
```

Those two lines configure the control surface used by the currently validated Desktop build: spawn metadata is visible and the v2 tools live under `agents`. They still do not configure an executor. Neither says “use Luna.” Neither line names an effort, a fork mode, or any root/child rule. Without `usage_hint_text`, no persistent default tells Sol to request Luna when it delegates, so the root can still spawn its inherited model.

The full generated policy adds the missing model route, effort, fork mode, root/child boundaries, advisor behavior, and failure rule. The skill is the setup and control plane: it resolves real model IDs, validates compatibility, installs safely, reports status, supports an advisor, handles cross-provider custom agents, and reverses its own changes. It is not consuming tokens in every later task.

One hard boundary remains: Codex has no global `executor_model = ...` engine switch today. Same-provider config routing is tool-level policy plus a runtime-validated `model` argument, not a hardcoded scheduler. Setup can prove config parsing and policy effectiveness; it cannot bind the provider of a future root task or prove a future spawn. When the spawn tool accepts an exact route, call that `route accepted`. Call a model `used and confirmed` only when the client explicitly exposes effective runtime metadata. A namespaced custom agent provides the stronger persistent pin needed for cross-provider routing.

## Current compatibility

| Situation | Behavior |
| --- | --- |
| Current Codex with a Sol or Terra root | One-time native policy; use Codex normally afterward. |
| Desktop `0.144.0-alpha.4`, live-tested July 10, 2026 | `agents` accepted `model=gpt-5.6-luna` plus `reasoning_effort=xhigh`; the default `collaboration` namespace rejected the expanded schema. Capability-test other builds. |
| Luna selected as the root | Luna currently declares multi-agent v1, so the v2 policy is not the right root route. Luna is best used here as a child of a Sol/Terra v2 root. |
| Older Codex that rejects `multi_agent_mode_hint_text` | The installer refuses to break its shared config and keeps the per-task skill fallback available. Update that client before enabling the persistent preset. |
| OpenAI root and OpenAI executor | Direct per-spawn model route; simplest setup. |
| Different provider for advisor or executor | Use a loaded, namespaced custom agent with an already configured provider. |
| Windows custom-agent path | New managed roles can be created, but updating or removing existing role files fails closed because inode/metadata-safe replacement is unavailable. Native App Server policy setup is a separate path. |

Desktop and CLI normally share `~/.codex/config.toml`. The setup asks the target binary, the `codex` on your PATH, and the Desktop binary when present to parse the complete four-field preset in an isolated home. That proves config compatibility, not a live child route. Report `route accepted` or `used and confirmed` only from the exact runtime evidence described above; the installer does not guess compatibility from a version number.

A trusted project's `.codex/config.toml` or a managed layer can override the personal preset in that workspace. Run `status` from the target project; setup rolls back if the policy is already overridden there, but another project can have different higher-precedence settings.

Named Codex profiles (`codex --profile ...`) are separate user layers. The one-command setup manages the default user config; a profile can override it. If you rely on named profiles, inspect that profile separately or use the task-local fallback instead of assuming the base policy wins.

Do not use Codex Fast mode when the goal is maximum allowance savings. A supported child may inherit the root's priority service tier, which can reduce the saving even when the child model is Luna.

## Older-client fallback

If a client cannot load the persistent policy, invoke the skill with the work in the same prompt:

```text
/codex-orchestration executor: GPT-5.6 Luna Extra High — implement the requested feature.
```

The skill uses the strongest child control that client exposes. On current v2 surfaces it passes the exact model and effort with `fork_turns = "none"`. If exact routing is unavailable, it says so. It never counts an inherited-root child as the requested executor.

## Advisor or executor from another provider

A model name alone cannot create provider access. An Anthropic advisor, for example, needs an existing authenticated Codex-compatible provider plus a personal custom agent loaded before the task starts.

Codex custom providers use the Responses wire protocol. A raw Anthropic Messages endpoint and key are not interchangeable. A supported route such as Amazon Bedrock may also be appropriate.

Once the provider is configured and tested, setup can save the namespaced roles:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High, advisor: Fable 5 Extra High, advisor provider: <existing-provider-id>
```

For this path, Codex-Orchestration creates only:

```text
~/.codex/agents/codex-orchestration-executor-<personal-id>.toml
~/.codex/agents/codex-orchestration-advisor-<personal-id>.toml
```

The matching role names carry the same stable, `CODEX_HOME`-specific suffix. That prevents older project-scoped Codex-Orchestration roles from accidentally shadowing the personal route. Setup verifies the personal files and refuses a same-name project collision in the current workspace.

It never writes credentials or creates a provider definition. See [Codex custom providers](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers).

Custom-agent files and the native policy live in separate storage systems, so cross-provider setup is a two-phase operation rather than one atomic commit. If the native phase fails after roles were created, Codex-Orchestration previews and removes only the newly managed roles. After an interruption, run `status --require-effective`; it reports managed personal roles that the native policy does not reference. Edited or user-owned files are never removed automatically.

Project-scoped custom agents remain available for teams that want inspectable role files in a trusted repository. Project roles have higher precedence, so a deliberately duplicated personal role name can shadow a global agent route. Run `status` in the project before relying on a custom-agent preset.

## Can it save about 65%?

It can save roughly 65% of **model-weighted Codex credits** in an executor-heavy example. It cannot promise 65% fewer raw tokens.

OpenAI's published token credit rates, checked July 9, 2026, price GPT-5.6 Luna at 20% of GPT-5.6 Sol for input, cached input, and output. If 20% of a comparable token mix stays with Sol and 80% moves to Luna:

```text
relative credits = 0.20 + (0.80 × 0.20) = 0.36
illustrative reduction                         = 64%
```

That is the “about 65%” claim. In practice, the orchestrator still pays for planning, review, integration, and verification. Advisor calls, copied context, retries, tools, and unnecessary agents add overhead. Multi-agent work can use more raw tokens even while consuming fewer high-end-model credits.

Lower model-weighted credits can make shared five-hour and applicable weekly allowances last longer, but the extension depends on the workload, plan, context, service tier, and real delegation mix; it is not guaranteed.

The benefit is simple: you do not need the frontier model for every stage. A robust plan plus a capable coding model can handle much of the execution volume, while the strongest model checks the result before it reaches you.

## What it never changes

- The current task model remains the only orchestrator.
- Codex still decides whether to plan, delegate, or work directly.
- No fixed three-agent or five-agent swarm is created.
- Goal state is not created or changed by this plugin.
- Executors never coordinate around the orchestrator.
- Permissions, approvals, tools, hooks, and credentials are not weakened.
- An accepted route is not reported as a confirmed runtime model unless the client exposes that metadata.

## Update

```bash
codex plugin marketplace upgrade codex-orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new task so Codex loads the updated skill. Run `/codex-orchestration status --require-effective` to gate an existing native preset; run `setup` again to update its managed policy.

Custom agents created by versions 0.1–0.3 remain supported. The older backup-first migration command is still available when exact legacy output is detected; edited or user-owned files are never guessed at or deleted.

## Uninstall

First disable the persistent policy:

```text
/codex-orchestration disable
```

If you created cross-provider or project custom agents, use `/codex-orchestration remove custom roles personally` or ask it to remove the roles for the current project. It previews the removal and deletes only fully validated namespaced files, including the home-specific v0.4+ names when present. On Windows, existing managed custom-role removal is intentionally unavailable and requires manual review; native policy disable remains separate.

Then remove the plugin:

```bash
codex plugin remove codex-orchestration@codex-orchestration
codex plugin marketplace remove codex-orchestration
```

Uninstalling the plugin does not silently delete a policy or custom-agent file that may still affect future tasks.

## Develop and validate

```bash
python3 -m unittest discover -s tests -v
```

The suite covers the native App Server protocol, capability detection, generated policy contract, setup/status/disable, exact restoration, custom agents, packaging, migration safety, model inspection, and an isolated real-CLI marketplace lifecycle. See the [production-readiness audit](docs/production-readiness-audit.md), [security policy](SECURITY.md), and [release process](RELEASE.md) for the maintained ship gates.

## Design sources

- [OpenAI: Subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [OpenAI: Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI: Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [OpenAI: Codex pricing and usage limits](https://learn.chatgpt.com/docs/pricing)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

## License

MIT
