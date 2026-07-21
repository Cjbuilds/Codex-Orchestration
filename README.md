# Codex Orchestration

Bring models like Claude Fable 5 into Codex, give each model a role, and let Codex coordinate the work.

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

- Bring Fable 5 or another compatible model into Codex.
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

Add a dedicated Designer when the work needs a design handoff:

```text
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, designer: GPT-5.6 Terra High, executor: GPT-5.6 Luna Extra High
```

Or let your current Codex model plan and use Fable 5 only as Advisor:

```text
/codex-orchestration setup advisor: Claude Fable 5 High, executor: GPT-5.6 Luna Extra High
```

After setup, start another new task and use Codex normally. The saved workflow applies automatically.

Fable defaults to **High**. You can choose **Low**, **Medium**, **High**, **XHigh**, or **Max**. **Ultra** is accepted as an alias for Max because Claude Code does not expose a separate Ultra effort.

Fable 5 uses the official Claude Code CLI and a compatible first-party Claude login. You do not need to add an Anthropic API key to Codex.

## Choose your roles

```text
/codex-orchestration setup planner: <model and effort>, advisor: <model and effort>, designer: <model and effort>, executor: <model and effort>
```

- Omit `planner` to use the current Codex model as Planner.
- Omit `advisor` when you do not want plan review.
- Omit `designer` when you do not need a separate design handoff.
- `executor` is required.
- Planner and Advisor must use different configured model routes so the review is independent.

Role labels are literal. A model after `planner:` plans; a model after `advisor:` reviews; a model after `designer:` designs; a model after `executor:` implements. Codex must never move a model to a different role because that model was used differently in an older plugin version. If you omit Designer, the workflow has no Designer. If you specify Planner and Executor but omit Advisor, the workflow has no Advisor.

When every requested route is ready in the current task, the plugin confirms only
the roles you supplied, in your order:

```text
Planner — Fable 5 high: Activated
Designer — Kimi K3: Activated
Executor — GPT-5.6 Sol high: Activated
```

`Activated` means the route is ready and callable for that task. If an external
model still needs authentication, qualification, connection, or a restart, the
plugin reports that exact state and next action instead of claiming activation.

You can also ask naturally without selecting the skill first:

```text
is Kimi available to use as Designer?
```

The plugin checks its External Model registry instead of guessing from the visible
tool list. It distinguishes whether Kimi K3 is bundled and supported, configured on
this installation, and callable in the current task. A question performs read-only
status inspection only; it never authorizes configuration, credentials, or spend.

Examples:

```text
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High

/codex-orchestration setup planner: GPT-5.6 Sol Extra High, advisor: Claude Fable 5 High, executor: GPT-5.6 Luna Extra High

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

OpenRouter now officially lists the exact ID `moonshotai/kimi-k3`, a 1,048,576-token
context, a Responses-compatible endpoint, and only `max` reasoning. For this model,
`auto` resolves to `max`; every other explicit effort is rejected rather than
clamped. The bundled manifest is no longer experimental, but each installation
remains unqualified and uncallable until its exact OpenRouter/Kimi/max tuple passes
the explicitly billable isolated Gate 0. New providers or subscription CLIs still
require a reviewed bundled manifest and adapter; arbitrary URLs and arbitrary local
CLIs are not auto-trusted.

Fable 5 remains the sealed subscription exception and can be used directly as
Planner or Advisor through first-party Claude login. See the
[External Models reference](plugins/codex-orchestration/skills/codex-orchestration/references/external-models.md)
for commands, lifecycle states, extension rules, and threat boundaries.

Models already available through Codex can still become ordinary user-owned roles:

```text
/codex-orchestration create project role: researcher
```

Project roles live in `.codex/agents/`; personal roles live in
`~/.codex/agents/`. An unbundled cross-provider model still requires an existing authenticated, compatible provider. Fable 5 is the bundled cross-provider exception.

## Use it with Codex Goals

Create a Codex Goal normally, then tell Codex to use the saved workflow until the Goal is complete. Codex still owns Goal state, permissions, integration, and verification; the plugin only guides which models perform each role.

## Useful commands

```text
/codex-orchestration status
/codex-orchestration status --require-effective
/codex-orchestration repair
/codex-orchestration --update
/codex-orchestration setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, designer: GPT-5.6 Terra High, executor: GPT-5.6 Luna Extra High
/codex-orchestration Planner: Claude Fable 5 High, Designer: Kimi K3
/codex-orchestration disable
```

`Designer: Kimi K3` selects the audited task-local External Model role without
adding Kimi to the Desktop picker or replacing any GPT route. Kimi K3 supports only
`max` reasoning (`auto` maps to `max`). If the exact role is already ready, the
plugin invokes it through a sealed, tool-free `codex exec` transport with the task
packet only on stdin; it never executes an External Model through native
`agents.spawn_agent`. Otherwise it walks the secure status, preparation,
hidden authentication, separately authorized Gate 0, connection, restart, and
readiness states and tells you the exact next action instead of calling the route
unavailable. The seat label never authorizes credential entry or a paid probe.

`disable` restores the routing values that existed before setup. It does not delete user-owned custom roles.

`repair` is narrower than setup or disable. When status reports that plugin-managed
mode/usage hints conflict with otherwise intact saved state, it can restore only
those saved hint bytes after a dry run. It refuses missing state, unmarked text,
namespace or spawn-metadata drift, Fable launcher drift, concurrent edits, and
higher-layer overrides. It does not rewrite restore history or touch authentication,
credentials, chats, or sessions.

## Important limits

- Codex remains the root orchestrator and final authority.
- Planner, Advisor, and Designer report only to Codex; they do not contact one another or Executors directly.
- Designer may edit only design artifacts explicitly delegated by Codex; it does not change implementation code or release Executor.
- The workflow reserves Fable planning tools for the root Codex model by policy. Current MCP calls do not identify their caller, so this caller boundary is instruction-enforced; the bridge itself still disables tools, edits, and session persistence.
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

If a Fable call fails in the task that performed an update but fresh status reports
`first-party login ready`, the login is healthy and the already loaded MCP bridge is
stale. Fully quit and reopen Codex, then start a new task; do not re-authenticate
solely for that stale-bridge condition.

To move from version 0.6.x or older to 0.7.0, run the native Codex commands once:

```bash
codex plugin marketplace upgrade codex-orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Version **0.6.0 or newer** is required for External Model roles; version **0.7.0
or newer** adds `--update`, routing repair, and Designer; version **0.7.1 or newer**
lets the natural `Designer: Kimi K3` label enter the External Model lifecycle;
version **0.7.2 or newer** uses the concise per-role activation confirmation;
version **0.8.0 or newer** uses sealed direct CLI invocation for READY External
Model roles.
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
