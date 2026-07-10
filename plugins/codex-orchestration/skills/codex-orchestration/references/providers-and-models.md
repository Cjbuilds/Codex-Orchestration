# Models, Providers, and Routing Boundaries

Use this reference when a requested seat is not a simple, same-provider task preference.

## Contents

- [Start with the executing host](#start-with-the-executing-host)
- [Current model versus child models](#current-model-versus-child-models)
- [Task-local model steering](#task-local-model-steering)
- [Saved custom agents](#saved-custom-agents)
- [Project scope](#project-scope)
- [Personal and cross-provider scope](#personal-and-cross-provider-scope)
- [Persistence and migration safety](#persistence-and-migration-safety)
- [Advisor permissions](#advisor-permissions)
- [Goals and task lifetime](#goals-and-task-lifetime)
- [Usage and savings language](#usage-and-savings-language)
- [Truthful seat states](#truthful-seat-states)
- [Primary sources](#primary-sources)

## Start with the executing host

The model chosen for the current task is the orchestrator. Do not infer its name when the client does not expose it.

Resolve advisor and executor models from the host that will actually run them:

1. Check an already loaded, namespaced custom agent.
2. Check the current client's model picker or child-routing interface.
3. Inspect the exact Codex binary used by that client when available.
4. Treat `scripts/inspect_models.py` and `codex debug models` as fallible debug signals, not stable product APIs.
5. Use official provider documentation to normalize a display name.
6. Ask for an exact ID if the sources disagree or the mapping is ambiguous.

A missing shell-CLI entry does not prove that a newer Desktop model is unavailable. A model shown by Desktop does not prove that an older PATH `codex` can validate it. Report the binary path, version, and catalog source used for saved configuration.

Do not maintain a static alias table. Catalogs, model names, efforts, account access, and provider integrations change.

## Current model versus child models

The current task model remains the root orchestrator. Never persist a new root `model`, `model_reasoning_effort`, or `model_provider` on behalf of this skill.

OpenAI currently describes Sol as the quality/depth choice, Terra as the balanced choice, and Luna as the speed/affordability choice. That makes Sol-orchestrator/Luna-executor a useful example, not a universal default.

Use an efficient executor only when the root can give it a bounded objective, enough context, clear ownership, acceptance criteria, and verification. A cheaper model is not automatically appropriate just because Codex can spawn it.

The advisor is a root-facing second opinion. It never coordinates with executors. A different model family may add a useful lens, but only when the route is already available and the extra review is worth its latency and usage.

## Task-local model steering

Codex documents two ways to influence child model choice:

- steer the choice in the prompt;
- pin `model` and `model_reasoning_effort` in a custom-agent file.

Treat prompt steering as best-effort until the client confirms the accepted child route. If the active child interface exposes direct model controls, use them conditionally and confirm acceptance; do not promise those implementation details exist on every surface.

For task-local `auto`, omit the reasoning-effort override. The literal string `auto` is not a live effort value. Omission may inherit the root session's effort or another host-resolved value, so keep the effective effort unverified until the client exposes it. For saved agents, resolve `auto` to a concrete catalog-supported default so a root effort override cannot leak into the child.

If a child needs context that forces it to inherit the root model, report:

```text
inherited root — requested child model was not used
```

Do not downgrade this to a partial success. Correct context is more important than forcing a cheaper route.

## Saved custom agents

Codex's documented reusable format is one standalone TOML file per custom agent:

```text
<project>/.codex/agents/*.toml
~/.codex/agents/*.toml
```

Every file requires `name`, `description`, and `developer_instructions`. It may also pin `model`, `model_reasoning_effort`, and `sandbox_mode`.

Codex-Orchestration uses namespaced identities so it does not replace built-in or generic user roles:

```text
filename: codex-orchestration-executor.toml
name:     codex_orchestration_executor

filename: codex-orchestration-advisor.toml
name:     codex_orchestration_advisor
```

The executor instructions constrain only the child: perform the assigned slice, do not broaden scope or spawn descendants, verify, and report to the root.

The advisor instructions constrain only the child: review the supplied packet, do not edit or delegate, and return `PLAN_APPROVED` or `PLAN_REVISE` to the root. The file requests `sandbox_mode = "read-only"`.

That sandbox setting is a requested default, not an absolute guarantee. Codex documents that live parent sandbox and permission overrides can be reapplied to children. Keep the review-only behavioral instructions even when a read-only sandbox is configured.

Custom agents load when a new task starts. Writing a file does not hot-load it into the current task. The `name` field is the source of truth; the matching filename is a convention. Save project agents at the trusted workspace or repository root; an untrusted or unrelated directory may not load project-scoped agents.

Treat the file's model and effort as configuration pins, not proof of the model that ran. A stronger live client override may take precedence. Confirm the effective route after spawning whenever the client exposes it.

If the same custom-agent name exists in more than one loaded scope or file, refuse to guess precedence and resolve the collision first. When there is no collision, treat each scope as described below.

Treat each scope as a complete team record anchored by its managed executor. If the project executor exists, use only the project team; a missing project advisor means saved `advisor: none`, not “look for a personal advisor.” Fall back to a personal team only when no managed project executor exists. An advisor without a same-scope managed executor is incomplete state and must not be merged implicitly.

## Project scope

Project scope is the portable default. Normal setup writes only the two namespaced files under `.codex/agents/` and leaves `.codex/config.toml` byte-for-byte unchanged or absent.

Project setup must not write:

- a root model or effort;
- `agents.max_threads` or `agents.max_depth`;
- provider selection or provider definitions;
- credentials;
- built-in-agent overrides;
- unrelated tools, MCP servers, skills, permissions, or instructions.

Project configuration loads only for a trusted project, following Codex's normal trust boundary.

## Personal and cross-provider scope

Personal scope changes future behavior across projects, so require explicit approval before applying it.

Write `model_provider` only when the provider ID is already built in or defined in the user's personal Codex configuration. Never create a provider definition, endpoint, authentication setting, or credential. Never ask the user to paste an API key into chat.

A first-use inline request cannot switch providers by itself because a task-local child model preference has no separate portable provider route. A model from Anthropic or another provider normally needs:

1. an existing authenticated Codex-compatible provider;
2. a personal custom agent pinned to that provider and model;
3. a new task that loads the agent;
4. a child interface able to select the loaded agent.

OpenAI credentials do not grant Anthropic access. Do not assume that Codex's supported provider protocol is interchangeable with Anthropic's native Messages API. Use only a provider integration the user has already configured and tested.

## Persistence and migration safety

The configurator is dry-run by default. A normal project save may apply after a clean preview because the user explicitly asked to save for that project. Personal apply requires a separate approval.

Managed files are replaceable only when their complete marker, schema, identity, instructions, and allowed keys match this release. Refuse altered or user-owned files. Reject symlinks, duplicate names, conflicting backups, malformed TOML, incomplete legacy provenance, and concurrent changes.

This is safe configuration persistence, not a same-user security sandbox. Run it only in a trusted workspace that is not being deliberately path-swapped by another process under the same OS account; such a process already has equivalent access to the user's Codex files.

On macOS and Linux, updates use same-directory atomic swaps, preserve and verify supported security metadata, fsync changed directory entries, and keep a content-free recovery journal across the multi-file publish boundary. An interrupted prepared transaction rolls back to the old set; an interrupted fully committed transaction keeps the new set and finishes cleanup. Do not print journal-adjacent file contents.

On Windows, initial creation into absent files is supported. Updating or removing an existing managed file fails closed because Python's portable copy APIs do not preserve and verify custom NTFS security descriptors. Do not bypass this restriction; require a manual, user-reviewed replacement.

The configurator requires Python 3.11 or newer. Select an available host launcher (`python3`, `py -3.11`, or `python`) rather than assuming Windows provides a `python3` command.

`--remove-saved-roles` previews and removes only fully validated namespaced executor/advisor files in the selected scope. It leaves root config and legacy artifacts alone, refuses edited files, and follows the same Windows update/removal restriction.

Legacy migration is opt-in and always requires user approval after the backup/deletion preview, including in project scope. It may remove only exact output from known previous releases. Preserve root model/provider/effort, global agent limits, comments, unrelated tables, and unknown or edited files. Older releases may have changed root settings; the migrator cannot safely reconstruct what existed before them.

## Advisor permissions

Task-local subagents inherit the parent permission mode and tools. Therefore:

- call a task-local advisor `review-only by instruction`;
- do not claim its sandbox is read-only unless the effective child state is confirmed;
- give it no reason to use mutating tools;
- treat mutation as a protocol failure;
- keep all advice root-facing.

For a saved advisor, request a read-only sandbox and retain the same instructions because live parent overrides may win.

## Goals and task lifetime

This skill does not create, start, pause, or modify a Goal. If the user already started one, use the selected seats inside that active workflow.

A task-local skill invocation is workflow context for the current request, not a documented durable team object. Include the actual work in the same invocation or mention the skill again later. Saved agents are the reusable path and require a new task.

Starting a new task to load saved agents does not move an active Goal or its task history.

## Usage and savings language

Keep four concepts separate:

- **Raw tokens:** all input, cached input, output, context, and tool-result tokens processed. Subagents can increase this total.
- **Codex credits:** token usage translated through model-specific rates. The current rate card prices Luna at 20% of Sol for input, cached input, and output.
- **Included limits:** shared five-hour usage plus any applicable weekly limits. Real consumption varies by model, context, reasoning, tools, retrieval, caching, and plan.
- **Other-provider usage:** separate allowance or billing that cannot be merged into a universal percentage.

The defensible “about 65%” example is credit math, not message-range math:

```text
20% Sol + 80% Luna at 20% of Sol's token credit rate
= 0.20 + (0.80 × 0.20)
= 0.36, or about 64% fewer credits before orchestration overhead
```

Never promise 65% fewer raw tokens, 65% more included allowance, a fixed weekly saving, lower API spend across providers, or 5× more completed work. Advisor calls, copied context, retries, and parallel workers may shrink or erase the saving.

## Truthful seat states

Use precise states:

- `pinned custom agent available`: matching saved role loaded, not yet used;
- `live route available`: current interface accepted exact child controls;
- `unverified prompt preference`: prompt steering only;
- `used and confirmed`: child route confirmed after spawn;
- `inherited root — requested child model was not used`;
- `unavailable`: provider/model/selector inaccessible;
- `none`: optional advisor disabled.

Never infer the child model from requested text or a saved file alone.

## Primary sources

- [OpenAI: Subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI: Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [OpenAI: Codex pricing and usage limits](https://learn.chatgpt.com/docs/pricing)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
