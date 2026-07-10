# Codex-Orchestration

One model leads. An optional second model challenges the plan. A faster or cheaper model can handle well-scoped execution work.

The important part: **the model you selected when you started the Codex task is already the orchestrator.** This plugin never asks you to configure another one.

Codex-Orchestration builds on Codex's own skills, subagents, and custom agents.

## Install once

```bash
codex plugin marketplace add Cjbuilds/Codex-Orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new Codex task after installation.

## Use it

In the Codex desktop app, type `/` and select **Codex Orchestration** when it appears. You can also type `$` and choose the skill, or use `@` to select the plugin and its bundled skill.

For the smoothest task-local run, include the work in the same prompt:

```text
/codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: none — implement the authentication changes below.
```

In CLI or IDE, open `/skills` or type `$`, then choose the name your client shows. A plugin install currently appears as:

```text
$codex-orchestration:codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: none — fix the failing tests.
```

The exact qualified label is client-generated, so the picker is the source of truth.

If you call the skill without settings and have no saved roles, Codex asks once for the missing choices. It gives you a ready-to-copy invocation like this:

```text
/codex-orchestration executor=<model>@<effort-or-auto>, advisor=<model>@<effort-or-auto>|none — <your task>
```

Send that full line with your choices and task. A bare answer such as `Luna xhigh` may not reload this explicit-only skill. Codex reuses the exact `/` or `$` label your client showed, keeps the original task, and leaves placeholders only for choices you still need to fill. If your first request also said `save for this project`, `save personally`, or made a role required/best-effort, it keeps that wording too.

Already supplied the executor? Codex asks only whether you want an advisor. It never asks for an orchestrator. `Extra High` is normalized to `xhigh`. For a task-local request, `auto` means “do not force an effort”; the host may inherit the session effort or choose another effective value. A saved role resolves `auto` to a concrete supported default before writing.

## What happens when you call it?

Codex first reports the routing status of each requested role:

```text
Codex Orchestration
Orchestrator: current task model — active
Executor: gpt-5.6-luna@xhigh — unverified prompt preference
Advisor: none
Delegation: allowed when useful, never forced
```

The skill keeps a requested setup separate from what actually ran:

| State | What it means |
| --- | --- |
| **Unverified prompt preference** | Codex steers a child toward that model for this request. It is not an exact route until the client confirms it. |
| **Pinned custom agent available** | A loaded saved agent sets the child model and effort. It has not run yet, and stronger live client overrides can still win. |
| **Inherited root** | The child used the orchestrator model; the requested child model was not used. |

The other honest outcomes are `live route available`, `used and confirmed`, and `unavailable`. The skill never reports a model as used merely because you typed its name.

Choosing an executor does not force Codex to use one. If the route is unavailable, Codex says so and can still handle simple or root-owned work itself. If you explicitly require that executor route, it pauses instead of silently substituting the orchestrator model. Supplying an advisor makes its review a gate for a meaningful plan unless you mark that review best-effort.

## The flow

```text
         You choose a model and start a Codex task
                            |
                            v
                ORCHESTRATOR (already selected)
             understands | plans | makes decisions
                            |
                   Is the work simple?
                    /              \
                  yes               no
                  |                  |
       ORCHESTRATOR does it      Advisor configured?
                  |               /             \
                  |             no               yes
                  |             |         ADVISOR checks plan
                  |             |                 |
                  |             |        ORCHESTRATOR reviews
                  |             |        advice; revises if needed
                  |             |                 |
                  |             +--------+--------+
                  |                      |
                  |           Would delegation help?
                  |               /             \
                  |             no               yes
                  |             |                 |
                  |    ORCHESTRATOR works    EXECUTOR receives
                  |                           bounded slice(s)
                  |             |                 |
                  +-------------+-----------------+
                                |
                                v
                  ORCHESTRATOR integrates if needed,
                       verifies and answers you
```

Codex decides whether the task needs a plan, whether a subagent would help, how many independent slices exist, and whether work is safe to run in parallel. Invoking this skill gives Codex permission to delegate when useful; it does not require delegation.

If you explicitly say “no subagents,” that wins.

## The three roles, in plain English

### Orchestrator: the lead

This is the current task model. It understands what you want, makes the important tradeoffs, breaks down the work when useful, chooses what to delegate, and owns the final answer.

It also reviews every executor handoff. Executors do not merge their own conclusions into the final result.

### Advisor: a second pair of eyes

The advisor is optional. For a meaningful plan, it checks for missed requirements, risky assumptions, shallow executor tasks, overlapping file ownership, and weak verification.

It reports only to the orchestrator. It does not assign work to executors or supervise them. A task-local advisor is **review-only by instruction**. A saved advisor also requests a read-only Codex sandbox, although the live parent permission mode can override child defaults.

Its first line is one of:

```text
PLAN_APPROVED   No material gap was found in the supplied plan.
PLAN_REVISE    Material gaps were found; concise fixes follow.
```

The orchestrator decides which advice to accept and revises its own plan. One confirmation pass is allowed after a material revision; there is no endless critic loop.

A different model family can provide a useful second lens, but cross-provider routing must already be configured before the task starts. Installing this plugin does not grant access to another provider.

### Executor: the builder

An executor gets a bounded handoff: the objective, relevant context, constraints, owned files, acceptance criteria, and verification command.

The executor may make local implementation decisions inside that slice. It does not redesign the whole plan, contact the advisor, spawn another team, or broaden the scope. It returns the changed files, the checks it ran, and any remaining risk to the orchestrator.

Codex should parallelize only genuinely independent work. Write-heavy slices need non-overlapping ownership; tightly coupled changes stay sequential or with the orchestrator.

## Save roles for future tasks

Task preferences are convenient, but they are not a durable team configuration. If you want exact reusable model pins, ask the skill to save them:

```text
/codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: none, save for this project
```

The skill previews the files before applying them. Project scope creates only:

```text
.codex/agents/codex-orchestration-executor.toml
.codex/agents/codex-orchestration-advisor.toml   # only when configured
```

These are namespaced, standalone Codex custom agents. Normal setup does **not** edit `.codex/config.toml`, does not change your root model, and does not change `agents.max_threads` or `agents.max_depth`.

Save project roles at the workspace or repository root and make sure Codex trusts that project. Start a new task after saving. In future tasks, invoke Codex Orchestration with your work; the skill can use the loaded saved roles without asking for their models again.

A saved scope is one complete team. With a managed project executor, a missing project advisor file means saved `advisor: none`; Codex does not pull in a personal advisor behind your back. It checks a personal saved team only when no project team exists. If both scopes define the same agent name, it reports the collision instead of guessing which one Codex loads.

Inline choices win for the current invocation. `advisor: none` disables a saved advisor for that request without deleting its file.

The saved model is a Codex configuration pin, not permission to make a false runtime claim. A stronger live client override can take precedence, so the skill still confirms the actual child route when the client exposes it.

Personal scope is also available, but Codex asks for explicit approval because it affects every project. The plugin never writes credentials or creates provider definitions.

If an older Codex-Orchestration release is detected, migration is opt-in. The configurator previews backups and removes only files whose complete contents prove that this project created them. Unknown or edited files are left for manual review.

<details>
<summary>Persistence safety and Windows note</summary>

Writes are staged and journaled. If the process is interrupted between file swaps, the next approved save restores the old set or finishes cleanup of a fully committed set before doing new work. The journal stores paths, hashes, and file identities—not config contents or credentials.

Run saves only in a trusted workspace. The configurator rejects symlinks, hard links, and ordinary concurrent changes it observes, but it is not a sandbox against another hostile process running as the same OS user and deliberately swapping parent directories during the save.

On Windows, creating saved roles is supported with Python 3.11+ (`py -3.11` or another available Python launcher). This release intentionally refuses automated updates or removals of existing managed files because it cannot yet preserve and verify custom NTFS security descriptors. Review and replace those files manually instead of bypassing the check.

</details>

## Using an advisor from another provider

A first-time inline request cannot switch providers by itself. For example, an Anthropic advisor needs an existing authenticated, Codex-compatible provider and a personal custom agent loaded before the task starts.

Configure and test that provider separately using [Codex's custom-provider guide](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers). Codex custom providers currently use the Responses wire protocol; a native Anthropic Messages endpoint and key are not interchangeable. A supported integration such as the built-in Amazon Bedrock provider may be another route.

Ask the skill to save it, confirm the existing provider ID, review the preview, and then start a new task:

```text
/codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: Anthropic Fable 5 xhigh, save personally
```

Model display names are examples, not hardcoded aliases. Codex resolves exact IDs from the host that will execute the work and asks when a name is ambiguous. An API model ID alone does not prove that your Codex client can use it.

## Can this save about 65%?

**It can reduce Codex credit consumption by about 65% in an executor-heavy example. It does not remove 65% of the raw tokens.**

OpenAI's [published token credit rates](https://learn.chatgpt.com/docs/pricing#what-are-tokens-and-credits), checked July 9, 2026, price GPT-5.6 Luna at 20% of GPT-5.6 Sol for input, cached input, and output tokens. If 20% of a comparable token mix stays with Sol and 80% moves to Luna:

```text
relative credits = 0.20 + (0.80 × 0.20) = 0.36
illustrative reduction                         = 64%
```

That is the basis for “about 65%.” It is a scenario, not a guarantee. Advisor calls, duplicated context, retries, tools, and extra subagent work add overhead and can reduce or erase the saving. OpenAI explicitly notes that subagent workflows use more total tokens than comparable single-agent runs.

OpenAI's current Plus ranges also list 15–90 Sol messages and 50–280 Luna messages per shared five-hour window. Those ranges show why smaller models can make limits last longer; they are not interchangeable per-message units or a promise of 3× completed work. Additional weekly limits may apply.

Keep the claims separate:

- Fewer **credits for the same token mix** can be estimated from the published rate card.
- Fewer **raw tokens** are not guaranteed; multi-agent work often uses more.
- Included **five-hour or weekly allowance** depends on the real task, context, reasoning, tools, caching, and plan.
- A different provider has its own allowance or bill.

## What this plugin deliberately does not do

- It does not configure a second orchestrator.
- It does not add a second scheduler or provider proxy.
- It does not force three, five, or any fixed number of agents.
- It does not create or change a Goal. It works inside a Goal the user already started.
- It does not replace Codex planning, permissions, approvals, or verification.
- It does not let executors coordinate around the orchestrator.
- It does not claim a requested model ran until the client accepts or confirms the route.
- It does not ask for API keys in chat or commit credentials to the project.

This restraint follows the central lesson from Anthropic's orchestrator-worker work: let the lead model choose task-specific slices, keep worker boundaries clear, synthesize results centrally, and add multi-agent complexity only when it materially helps.

## Update

```bash
codex plugin marketplace upgrade codex-orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new task after updating so Codex loads the new skill and any saved agents.

If you saved roles with version 0.1 or 0.2, invoke the updated skill with complete current choices, the original scope, and `migrate legacy`. For example:

```text
/codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: none, save for this project, migrate legacy
```

It detects the old files, shows the migration preview, and asks for approval before changing them. Start a new task afterward so Codex loads the migrated agents.

Migration does not guess what your root settings were before an older release. It preserves any old root model, provider, effort, and `agents.max_*` values in `.codex/config.toml`; review those manually if a prior release changed them.

See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Uninstall

Remove saved roles before removing the plugin:

```text
/codex-orchestration remove saved roles for this project
```

Use `/codex-orchestration remove saved roles personally` as a separate, explicitly approved action if you created a personal team. The skill previews both removals and deletes only its fully validated namespaced files. On Windows, where automated removal of existing files is intentionally blocked, manually review and remove these exact project files—or their `~/.codex/agents/` personal equivalents—instead:

```text
.codex/agents/codex-orchestration-executor.toml
.codex/agents/codex-orchestration-advisor.toml
```

If you previously used version 0.1 or 0.2, follow the migration step above first—or manually review its legacy output—before removing the current roles. Preserved `.codex/config.toml` settings and `.bak.codex-orchestration` migration backups are intentionally never guessed at or auto-deleted.

Then uninstall:

```bash
codex plugin remove codex-orchestration@codex-orchestration
codex plugin marketplace remove codex-orchestration
```

Uninstalling the plugin itself does not silently delete saved project or personal agent files. Start a new task after cleanup so already loaded agents disappear from task state.

## Develop and validate

From a cloned checkout:

```bash
python3 -m unittest discover -s tests -v
```

The test suite covers packaging, generated-agent contracts, safe migration, dry runs, idempotency, conflicts, provider boundaries, symlinks, and transactional rollback. Release testing also performs isolated marketplace install and update checks.

CI additionally installs the real Codex CLI in a temporary home, installs version 0.2 from a disposable Git marketplace, runs the real marketplace upgrade to 0.3, verifies discovery and cached contents, runs the installed configurator, and removes the test installation.

## Design sources

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI: Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [OpenAI: Subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Codex pricing and usage limits](https://learn.chatgpt.com/docs/pricing)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

## License

MIT
