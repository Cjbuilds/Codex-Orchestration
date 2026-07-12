# Codex Orchestration

Bring compatible models into Codex, give each one a role, describe the workflow, and let Codex coordinate the work.

## What is Codex Orchestration?

Codex Orchestration turns one Codex task into a multi-model team.

The model you select when you start the task is the orchestrator. It owns the goal, plan, decisions, handoffs, verification, and final answer.

You can add other models as advisors, executors, researchers, reviewers, writers, supervisors, or any focused role that fits your work.

Tell Codex the order: who plans, who critiques, who researches, who builds, and who verifies. Codex handles the handoffs, waits for results, resolves feedback, and returns one answer.

It works with normal tasks and Codex Goals. Once the task starts, Codex runs the workflow autonomously within your instructions and permissions.

You can use models from OpenAI or any compatible provider already configured and authenticated in Codex. A model name alone does not create provider access.

## How it works

```text
             YOU
       Task or Codex Goal
              |
              v
     ROOT MODEL / ORCHESTRATOR
        understands and plans
              |
              v
   +---------------------------+
   | Optional specialist roles |
   | advisor | researcher      |
   | reviewer | supervisor     |
   +---------------------------+
              |
        evidence + critique
              |
              v
     ORCHESTRATOR DECIDES
       and improves the plan
              |
              v
   +---------------------------+
   | Execution roles           |
   | executor | writer | maker |
   +---------------------------+
              |
              v
     ORCHESTRATOR VERIFIES
              |
              v
          FINAL RESULT
```

The roles and order are yours. Codex remains the lead and adapts the workflow when the task needs it.

## Why use it?

- **Better results:** different models challenge the plan from different perspectives.
- **Faster work:** independent research, review, and execution can run in parallel.
- **Lower model-weighted cost:** reserve the strongest model for judgment and use efficient models for high-volume execution.
- **Clear ownership:** every model gets a bounded role, expected output, and place in the workflow.
- **Flexible teams:** change the models, roles, or sequence for each task or project.

Multi-agent work can use more raw tokens. Savings depend on the models, workload, context, retries, and service tier; they are not guaranteed.

## Install

```bash
codex plugin marketplace add Cjbuilds/Codex-Orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new Codex task after installation.

In the desktop app, type `/` and choose **Codex Orchestration**. CLI and IDE users can invoke it through `/skills` or `$`.

Setup requires Python 3.11 or newer. Use `python3` on macOS/Linux or an available `py -3.11` or `python` launcher on Windows.

## Quick start

### OpenAI-only team

Use the model selected for the task as orchestrator and Luna as the default executor:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High
```

Add a second OpenAI model as advisor:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High, advisor: GPT-5.6 Terra High
```

Setup previews and validates the personal routing policy before applying it. Start a new task afterward.

### Claude Fable 5 as advisor

Use Claude Fable 5 to critique plans while your selected Codex model stays in charge:

```text
/codex-orchestration setup executor: GPT-5.6 Luna Extra High, advisor: Claude Fable 5 Extra High
```

This route uses the official Claude Code CLI through a bundled, read-only MCP bridge. It requires a compatible first-party Claude login and does not need an Anthropic API key in Codex.

The bridge accepts one plan-review packet, disables Claude tools and session persistence, verifies the runtime model, and requires `PLAN_APPROVED` or `PLAN_REVISE`.

If Claude is unavailable or returns an invalid result, Codex treats the advisor as unavailable. Failure is never approval.

## Bring any model and create any role

Codex supports custom agents with their own model, effort, instructions, sandbox, tools, MCP servers, and skills.

Ask Codex Orchestration to create project roles for you:

```text
/codex-orchestration create these project roles:

- researcher
  model: <model-id>
  provider: <configured-provider-id>
  effort: high
  sandbox: read-only
  job: gather evidence and cite sources

- writer
  model: <model-id>
  effort: medium
  job: turn approved research into the final draft

- reviewer
  model: <model-id>
  effort: high
  sandbox: read-only
  job: check accuracy, gaps, and weak claims
```

Codex previews the native custom-agent files under `.codex/agents/`. After you approve the files, start a new task so Codex loads the roles.

Ask for personal roles only when you want them available across projects. Those files live under `~/.codex/agents/`.

The plugin safely manages its built-in advisor and executor seats. Other role names are normal Codex custom agents and remain user-owned.

### Common roles

| Role | Good at |
| --- | --- |
| Advisor | Critiquing a plan before expensive work starts |
| Executor | Implementing a bounded part of an approved plan |
| Researcher | Gathering evidence, APIs, papers, or repository context |
| Reviewer | Finding bugs, security risks, regressions, or missing tests |
| Writer | Producing documentation, articles, reports, or launch copy |
| Supervisor | Checking progress and reporting exceptions to the root |

Roles should be narrow. The orchestrator remains the only model that owns the whole task.

## Describe the workflow at the start of a task

Once the models and roles are available, tell Codex how to use them.

```text
Use this workflow for the task:

1. The model I selected for this task is the root orchestrator.
2. The root creates the plan.
3. Claude Fable 5 reviews the plan as advisor.
4. The root accepts only valid feedback and revises the plan.
5. GPT-5.6 Luna Extra High executors implement independent slices.
6. The root integrates, tests, verifies, and gives me the final answer.

Do not skip a required review. Do not let child agents create their own teams.
```

Or define a custom sequence:

```text
Researcher -> root synthesis -> reviewer -> writer -> root verification
```

Codex follows the saved and task-level instructions, while keeping authority, safety, and final judgment with the root model.

## Use it with Codex Goals

Set a Goal normally, then tell Codex to use your team:

```text
/goal Ship the authentication redesign with tests and migration notes.

Use my configured advisor and executor workflow until the Goal is genuinely complete.
```

The Goal remains owned by Codex. This plugin does not create, pause, clear, or change Goal limits unless you explicitly use Codex's Goal controls.

## Roles and permissions

Subagents inherit the current task's permission mode. Choose the parent permission mode before delegation.

A custom role may request a narrower sandbox such as `read-only`. It cannot silently expand the authority granted by the parent task.

Codex Orchestration never bypasses approvals, changes credentials, creates provider access, or weakens your security settings.

## Model and provider routes

| Route | How it works |
| --- | --- |
| Same-provider Codex model | Exact model and effort are requested when Codex spawns the role. |
| Claude Fable 5 advisor | Bundled root-only MCP bridge uses the authenticated Claude Code CLI. |
| Other provider | A native custom agent pins an already configured `model_provider`. |
| Project role | Stored in `.codex/agents/` and loaded for that trusted project. |
| Personal role | Stored in `~/.codex/agents/` and loaded across projects. |

Custom Codex providers use a compatible wire protocol and authentication setup. A raw provider endpoint or API key is not automatically interchangeable with Codex.

## Useful controls

```text
/codex-orchestration status
/codex-orchestration status --require-effective
/codex-orchestration setup executor: GPT-5.6 Terra High
/codex-orchestration disable
/codex-orchestration remove custom roles personally
```

`status --require-effective` is the automation and release gate. It exits nonzero for incompatible clients, conflicts, overrides, incomplete controls, unavailable routes, or orphaned managed roles.

`disable` restores the values that existed before built-in routing setup. It leaves unrelated Codex configuration alone.

## Important boundaries

- Codex must already be able to access the model through its current provider, a configured compatible provider, or the bundled Fable route.
- The saved workflow is policy-guided routing, not a separate scheduler or an engine-level global executor switch.
- Codex decides whether delegation is useful, how many independent roles to run, and when direct work is safer.
- Exact runtime identity is called confirmed only when the client exposes effective model, provider, and effort metadata.
- Full-history forks can inherit the root route, so different routed roles use bounded, self-contained handoffs.
- Existing custom-agent updates and removal remain fail-closed on Windows when safe metadata preservation cannot be proven.

If you say `no subagents`, that always wins.

## Update

```bash
codex plugin marketplace upgrade codex-orchestration
codex plugin add codex-orchestration@codex-orchestration
```

Start a new task so Codex loads the updated plugin and roles.

## Uninstall

First disable the saved routing policy:

```text
/codex-orchestration disable
```

Preview and remove plugin-managed custom roles if you created them. Review user-owned arbitrary roles separately.

```bash
codex plugin remove codex-orchestration@codex-orchestration
codex plugin marketplace remove codex-orchestration
```

Removing the plugin does not silently delete configuration that may still affect later tasks.

## Develop and validate

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m compileall -q plugins tests scripts
python3 -m ruff check plugins tests scripts
python3 -m unittest discover -s tests -v
python3 tests/plugin_lifecycle_smoke.py
python3 scripts/release_check.py
```

See the [production-readiness audit](docs/production-readiness-audit.md), [security policy](SECURITY.md), and [release process](RELEASE.md).

Technical details live in [providers and models](plugins/codex-orchestration/skills/codex-orchestration/references/providers-and-models.md).

## Design sources

- [OpenAI: Subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
- [OpenAI: Custom model providers](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)
- [OpenAI: Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [OpenAI: Build plugins](https://learn.chatgpt.com/docs/build-plugins)

## License

MIT
