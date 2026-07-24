# Codex Orchestration

Bring models like Qwen 3.8 Max Preview, Kimi K3, and Claude Fable 5 into Codex, give each model a role, and let Codex coordinate the work.

## What is it?

Codex Orchestration adds four simple roles to a Codex task:

- **Planner** creates the plan and improves it after feedback. It is optional; when omitted, your current Codex model plans.
- **Advisor** reviews the plan, finds important gaps, and approves it when it is ready. It is optional.
- **Designer** turns approved requirements into a bounded visual, UX, interaction, information-architecture, or design-system handoff. It is optional.
- **Executor** implements the approved plan. It is required for setup.

The model selected for the Codex task remains in charge. It passes work between the roles, checks every result, and gives you the final answer.

## How it works

```text
                         YOUR TASK
                             |
                             v
                  CODEX COORDINATES THE WORK
                             |
                             v
               PLANNER CREATES THE FIRST PLAN
              Fable 5, another model, or Codex
                             |
                             v
                    ADVISOR REVIEWS IT
                       finds real gaps
                             |
                   needs work? -- yes --+
                             |            |
                            no            v
                             |      PLANNER IMPROVES IT
                             |            |
                             +<-----------+
                             |
                       PLAN APPROVED
                             |
                             v
                DESIGNER SHAPES THE EXPERIENCE
                    optional design handoff
                             |
                             v
                  EXECUTORS IMPLEMENT IT
                             |
                             v
                    CODEX TESTS & DELIVERS
```

Planner and Advisor can work through several revisions. Codex stops as soon as the Advisor returns `PLAN_APPROVED`, with a safety limit of five reviews. If approval is not reached, execution stops and Codex shows you the latest plan and unresolved issues.

## Why use it?

- Bring Qwen 3.8 Max Preview, Fable 5, or another compatible model into Codex.
- Use different models for planning, review, design, and implementation.
- Get a stronger plan before code changes begin.
- Run independent implementation work in parallel—up to 2x faster on suitable tasks.
- Move repeatable work away from the root model and potentially hit premium-model limits about 40% less often.

Results depend on the models, task, context, retries, and available parallel work. The speed and limit figures are targets, not guarantees.

## Install

```bash
codex plugin marketplace add Cjbuilds/Codex-Orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new Codex task after installation. Setup requires Python 3.11 or newer.

## Quick start

Use Fable 5 to plan, Sol to advise, and Luna to implement:

```text
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High
```

Add Kimi K3 as the dedicated Designer through an existing Kimi Code subscription:

```text
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, designer: Kimi K3, executor: GPT-5.6 Luna Extra High
```

Or keep Codex/Sol as root, use Qwen 3.8 Max Preview for independent plan review,
Kimi K3 for design, and Luna for implementation:

```text
/codex-orchestration setup advisor: Qwen 3.8 Max Preview, designer: Kimi K3, executor: GPT-5.6 Luna Extra High
```

Or let your current Codex model plan and use Fable 5 only as Advisor:

```text
/codex-orchestration setup advisor: Claude Fable 5 High, executor: GPT-5.6 Luna Extra High
```

After setup, start another new task and use Codex normally. The saved workflow applies automatically.

Fable defaults to **High**. You can choose **Low**, **Medium**, **High**, **XHigh**, or **Max**. **Ultra** is accepted as an alias for Max because Claude Code does not expose a separate Ultra effort.

Fable 5 uses the official Claude Code CLI and a compatible first-party Claude login. You do not need to add an Anthropic API key to Codex.

Kimi K3 uses the official Kimi Code CLI through ACP and its existing OAuth subscription login. It requires acpx 0.12.0 or newer, but no Kimi or OpenRouter API key.

Qwen Advisor uses Alibaba's official OpenAI-compatible Token Plan endpoint and an
existing Alibaba Token Plan. Its regional credential is enrolled through a hidden
prompt into the operating-system credential store; it is never placed in plugin state,
routing hints, or model input. Reasoning is provider-native rather than a synthetic
effort label.

## Choose your roles

```text
/codex-orchestration setup planner: <model and effort>, advisor: <model and effort>, designer: <model and effort>, executor: <model and effort>
```

- Omit `planner` to use the current Codex model as Planner.
- Omit `advisor` when you do not want plan review.
- Omit `designer` when you do not need a separate design handoff.
- `executor` is required.
- Two configured Planner and Advisor routes must differ so the review is independent. If Planner is omitted, the root owns planning and is not a configured Planner route; a fresh direct Advisor may use the same model as the root.

Role labels are literal. A model after `planner:` plans; a model after `advisor:` reviews; a model after `designer:` designs; a model after `executor:` implements. Codex must never move a model to a different role because that model was used differently in an older plugin version. If you omit Designer, the workflow has no Designer. If you specify Planner and Executor but omit Advisor, the workflow has no Advisor.

Explicit current-task model, effort, and agent choices override saved Advisor and
Executor defaults. The plugin follows the exact route and never substitutes GPT-5.5,
Terra, Qwen, Fable, or another model merely to create provider or model diversity;
an unavailable exact route is reported as unavailable.

When every requested route is ready in the current task, the plugin confirms only
the roles you supplied, in your order:

```text
Planner — Fable 5 high: Activated
Advisor — Qwen 3.8 Max Preview: Activated
Designer — Kimi K3: Activated
Executor — GPT-5.6 Sol high: Activated
```

`Activated` means the route is ready and callable for that task. If a bridge or
external model still needs setup, authentication, qualification, connection, or a
restart, the plugin reports that exact state and next action instead.

You can also ask naturally without selecting the skill first:

```text
is Kimi available to use as Designer?
```

The plugin checks native routing state, the selected MCP launcher, installed CLI
versions, and Kimi's non-secret local provider catalog instead of guessing from the
visible tool list. A question performs read-only status inspection only and never
authorizes configuration, credentials, or spend.

Examples:

```text
/codex-orchestration setup advisor: GPT-5.6 Sol Max, executor: GPT-5.6 Sol Medium

/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High

/codex-orchestration setup planner: GPT-5.6 Sol Extra High, advisor: Claude Fable 5 High, executor: GPT-5.6 Luna Extra High

/codex-orchestration setup advisor: Qwen 3.8 Max Preview, designer: Kimi K3, executor: GPT-5.6 Luna Extra High

/codex-orchestration setup designer: GPT-5.6 Terra High, executor: GPT-5.6 Luna Extra High

/codex-orchestration setup executor: GPT-5.6 Luna Extra High
```

## Bring another model into Codex

External Models are roles, not picker entries. Codex stays signed in with ChatGPT,
the selected GPT model remains root, and the plugin adds only a provider-pinned
personal agent for each validated effort. It never changes top-level `model` or
`model_provider`, and disconnect/removal never touches chats, sessions, or OpenAI
authentication.

Ask for a role in plain language:

```text
/codex-orchestration configure external role researcher with OpenRouter model moonshotai/kimi-k3 at max; job: gather evidence and cite sources

/codex-orchestration configure external role designer with OpenRouter model moonshotai/kimi-k3 at max; job: produce a bounded UX specification

/codex-orchestration call researcher at max — review this bounded research packet
```

Setup is deliberately staged: preview and prepare the audited provider adapter,
authenticate through a hidden local prompt backed by the operating-system credential
store in a trusted terminal, explicitly approve one potentially billable isolated
Gate 0 probe, create the role variants, then start a new task. Never paste an API
key into Codex chat. The repository,
provider TOML, registry, journal, logs, and tests store no key.

Once a literal provider-backed External Model role is READY, execution uses only the
sealed, tool-free `codex exec` transport with the bounded task packet on stdin. It
never runs through native `agents.spawn_agent`; `resolve` remains a read-only
diagnostic rather than an execution path.

OpenRouter now officially lists the exact ID `moonshotai/kimi-k3`, a 1,048,576-token
context, a Responses-compatible endpoint, and only `max` reasoning. For this model,
`auto` resolves to `max`; every other explicit effort is rejected rather than
clamped. The bundled manifest is no longer experimental, but each installation
remains unqualified and uncallable until its exact OpenRouter/Kimi/max tuple passes
the explicitly billable isolated Gate 0. New providers or subscription CLIs still
require a reviewed bundled manifest and adapter; arbitrary URLs and arbitrary local
CLIs are not auto-trusted.

Kimi K3 also has a separate sealed subscription route for the built-in Designer seat.
That route uses Kimi Code OAuth through acpx/ACP, not OpenRouter. Qwen 3.8 Max
Preview has a sealed Advisor route through Alibaba's Token Plan JSON API and an
OS-stored credential. Fable 5 remains the sealed Planner/Advisor route through
first-party Claude login. See the
[External Models reference](plugins/codex-orchestration/skills/codex-orchestration/references/external-models.md)
for commands, lifecycle states, extension rules, and threat boundaries.

Models already available through Codex can still become ordinary user-owned roles:

```text
/codex-orchestration create project role: researcher
```

Project roles live in `.codex/agents/`; personal roles live in
`~/.codex/agents/`. An unbundled cross-provider model still requires an existing authenticated, compatible provider. Qwen Advisor, Fable 5, and the Kimi K3 Designer bridge are the three sealed subscription exceptions.

## Use it with Codex Goals

Create a Codex Goal normally, then tell Codex to use the saved workflow until the Goal is complete. Codex still owns Goal state, permissions, integration, and verification; the plugin only guides which models perform each role.

## Useful commands

```text
/codex-orchestration status
/codex-orchestration status --require-effective
/codex-orchestration repair
/codex-orchestration --update
/codex-orchestration setup advisor: Qwen 3.8 Max Preview, designer: Kimi K3, executor: GPT-5.6 Luna Extra High
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, designer: GPT-5.6 Terra High, executor: GPT-5.6 Luna Extra High
/codex-orchestration Planner: Claude Fable 5 High, Designer: Kimi K3
/codex-orchestration disable
```

`Designer: Kimi K3` selects the audited persistent Kimi Code subscription bridge
without adding Kimi to the Desktop picker or replacing any GPT route. K3 runs at
`max` (`auto` maps to `max`) through a fresh ACP session with terminal disabled,
permissions denied, an empty disposable cwd, tool-event rejection, and mechanical
runtime-model confirmation. It uses the existing Kimi OAuth login and does not ask
for an API key or perform a billable OpenRouter Gate 0.

`Advisor: Qwen 3.8 Max Preview` selects the audited Token Plan JSON bridge without
adding Qwen to the Desktop picker. Each review makes one direct HTTPS request with no
tools, disabled session caching, an exact response-model check, and a strict two-key
JSON review envelope whose decision is `PLAN_APPROVED` or `PLAN_REVISE`. Global and
China Token Plan credentials are separate OS-store entries and never fallback to one
another.

`disable` restores the routing values that existed before setup. It does not delete user-owned custom roles.

`repair` is narrower than setup or disable. When status reports that plugin-managed
mode/usage hints conflict with otherwise intact saved state, it can restore only
those saved hint bytes after a dry run. It refuses missing state, unmarked text,
namespace or spawn-metadata drift, bundled launcher drift, concurrent edits, and
higher-layer overrides. It does not rewrite restore history or touch authentication,
credentials, chats, or sessions.

## Important limits

- Codex remains the root orchestrator and final authority.
- Planner, Advisor, and Designer report only to Codex; they do not contact one another or Executors directly.
- Designer may edit only design artifacts explicitly delegated by Codex; it does not change implementation code or release Executor.
- The workflow reserves Fable, Qwen, and Kimi bridge tools for the root Codex model by policy. Current MCP calls do not identify their caller, so this caller boundary is instruction-enforced; each bridge still mechanically applies its own tool and persistence controls.
- Advisor approval is a planning gate, not a guarantee that implementation will succeed.
- Direct model routes inherit the root provider. Audited external adapters use
  provider-pinned personal role agents and never enter the model picker.
- Other unbundled providers must already be configured and authenticated.
- The plugin never creates credentials or bypasses permissions and approvals. It can prepare a non-secret provider table and retrieve a user-enrolled key from the OS credential store at request time.
- Codex decides when delegation or parallel work is useful.
- If you say `no subagents`, Codex must not delegate.

Technical details are in [providers and models](plugins/codex-orchestration/skills/codex-orchestration/references/providers-and-models.md).

## Update

For version 0.7.0 and newer, ask the installed plugin to update itself:

```text
/codex-orchestration --update
```

The command refuses disabled, local, missing, duplicate, or unexpected sources,
then delegates refresh and installation only to Codex's native plugin manager and
verifies the final canonical source, version, and enabled state. It does not remove
the plugin or touch routing, credentials, chats, sessions, or the model picker.
Restart Codex Desktop and start a new task after an update; the task that launched
the updater keeps its already loaded instructions.

If a Fable, Qwen, or Kimi call fails in the task that performed an update but fresh status
still reports its first-party route ready, the login is healthy and the already
loaded MCP bridge is stale. Fully quit and reopen Codex, then start a new task; do
not re-authenticate solely for that stale-bridge condition.

To move from version 0.6.x or older to 0.7.0, run the native Codex commands once:

```bash
codex plugin marketplace upgrade codex-orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Version **0.6.0 or newer** is required for External Model roles; version **0.7.0
or newer** adds `--update`, routing repair, and Designer; version **0.7.2 or newer**
uses concise per-role activation confirmation; version **0.8.0 or newer** routes
`Designer: Kimi K3` through the existing Kimi Code OAuth subscription via ACP and
uses sealed direct CLI invocation for READY External Model roles;
version **0.9.0 or newer** adds the sealed Qwen 3.8 Max Preview Advisor. Version
**0.9.1 or newer** clarifies that root-owned planning may use a direct Advisor
matching the root model, while two configured Planner/Advisor routes must still
remain distinct. Version **0.9.2 or newer** binds plugin-scoped MCP setup, status,
repair, rollback, and disable to the exact executing Codex cache coordinate or saved
marketplace identity and fails closed instead of guessing when installed identities
are ambiguous or change during the transaction.
Confirm with
`codex plugin list --json`, then restart Codex Desktop and start a new task.

If the version stays old or `marketplaceSource.sourceType` is `local`, Codex is pointed at a local checkout rather than the GitHub marketplace. Run `/codex-orchestration disable` first if a saved policy is active, then remove the plugin and that marketplace registration, add `Cjbuilds/Codex-Orchestration` again, and reinstall. This does not delete the local source checkout.

Before downgrading to a version older than the currently saved routing schema, run `/codex-orchestration disable` with the current version first.

## Uninstall

First run:

```text
/codex-orchestration disable
```

Then remove the plugin:

```bash
codex plugin remove codex-orchestration@codex-orchestration
codex plugin marketplace remove codex-orchestration
```

Review and remove any user-owned custom roles separately.

## Development

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m compileall -q plugins tests scripts
python3 -m ruff check plugins tests scripts
python3 -m unittest discover -s tests -v
python3 tests/plugin_lifecycle_smoke.py
python3 scripts/release_check.py
```

See the [production-readiness audit](docs/production-readiness-audit.md), [security policy](SECURITY.md), and [release process](RELEASE.md).

## License

MIT
