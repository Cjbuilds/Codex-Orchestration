---
name: codex-orchestration
description: Explicitly keep the current Codex task model as orchestrator, require an executor choice for eligible delegated work, and optionally obtain a review-only second opinion on a non-trivial plan. Use when the user selects Codex Orchestration or invokes it with executor/advisor settings; never replace Codex planning, Goal, delegation, integration, or verification behavior.
---

# Codex Orchestration

Keep the model already selected for the current Codex task in charge. Add advisor and executor model choices around Codex's existing subagent workflow; do not build a second orchestration system.

## Parse the request

Accept natural invocations such as:

```text
/codex-orchestration executor: GPT-5.6 Luna xhigh, advisor: none — implement the requested feature
$codex-orchestration executor=gpt-5.6-luna@auto, advisor=gpt-5.6-terra@high — review and fix this branch
```

Use the exact skill label shown by the client. Desktop users can select the skill from `/`, `$`, or the plugin picker. CLI and IDE users can select it through `/skills` or `$`. Treat `/codex-orchestration` as a convenient Desktop example, not a universal alias.

An executor choice is required; actually using an executor is not. The advisor is optional, but require either a model or explicit `none`. Before resolving seats, report a collision if project and personal scopes define the same custom-agent `name`; do not guess Codex's load precedence. Then resolve values in this order:

1. Use inline values from this invocation.
2. If a fully managed project executor is loaded, treat project scope as one complete saved team. Use its advisor when present; a missing namespaced advisor means saved `advisor: none`. Do not merge in a personal advisor.
3. Otherwise do the same with a fully managed personal executor. An advisor file without its same-scope managed executor is incomplete state; report it instead of guessing.
4. Ask once for only the still-missing values.

`remove saved roles for this project` and `remove saved roles personally` are configuration-control requests, not work-routing invocations. They do not require executor/advisor values and may remove the explicitly named scope even when that removal is what resolves a cross-scope name collision. Personal removal still requires explicit approval.

When asking, give a ready-to-copy invocation because this skill is explicit-only and may not reload from a bare reply:

```text
<exact-skill-label> executor=<model>@<effort-or-auto>, advisor=<model>@<effort-or-auto>|none — <original task>
```

Reuse the exact label that invoked the skill (`/…`, `$…`, or the client-qualified plugin label), preserve supplied seat values, and insert placeholders only for missing seats. Preserve the original task text and every understood modifier, including `save for this project`, `save personally`, required/best-effort routing, migration, and explicit subagent constraints. Do not lose the user's work, persistence, or gating intent merely because a value was missing.

Inline values override saved choices only for the current request; they do not rewrite files unless the user also says `save` or `persist`.

If an old invocation includes `orchestrator:`, explain that the current task model already owns that role. Never switch, persist, or ask for another orchestrator. If the client exposes the active model, report it; otherwise say `current task model`.

Normalize `Extra High` to `xhigh`. For task-local routing, `auto` means no explicit effort override; the host may use an inherited session effort or another host-resolved value. For saved agents, resolve `auto` to the selected model's concrete catalog default before writing. Resolve display names to exact IDs only from the executing host, its picker or catalog, an already loaded custom agent, or official provider documentation. Ask for an exact ID when ambiguous. Never invent a model ID.

Apply task-local choices to the work included in this invocation. Do not promise that the skill created a durable, mutable team setting for later turns. For reusable behavior, save custom agents and start a new task.

Read [providers-and-models.md](references/providers-and-models.md) when routing is unavailable, a model is absent from the local catalog, providers differ, saved roles are requested, or host capabilities disagree.

## Preserve Codex behavior

The current task model remains the root. It owns intent, planning, architecture, decomposition, delegation, integration, review, and final verification.

Invoking this skill authorizes Codex to use its native subagents when Codex independently judges delegation materially useful. It does not require a spawn, set a worker count, or override an explicit `no subagents` instruction.

Let the root decide how much internal planning is useful and whether work is safely delegable. Do not create or change Goal state because this skill is active. If the user already started a Goal, apply the same advisor and executor choices inside it.

Never create a second orchestrator, fixed swarm, nested executor team, replacement planning loop, or parallel-write policy. Never change `agents.max_threads`, `agents.max_depth`, permissions, approvals, hooks, tools, authentication, or provider credentials.

## Establish the route honestly

Use the strongest route the current client actually supports:

1. Prefer a loaded, namespaced `codex_orchestration_advisor` or `codex_orchestration_executor` custom agent whose saved model and effort match the requested seat.
2. Otherwise use an accepted task-local model override when the active subagent interface exposes one.
3. Otherwise steer model choice in the child prompt as a best-effort preference, as allowed by Codex's model-selection guidance.
4. If the provider or model is inaccessible, mark the seat unavailable.

For a task-local `auto` effort, omit the reasoning-effort override. Do not pass the literal value `auto` as a live reasoning effort, and do not claim omission guarantees the child model's default: the effective effort is host-dependent until exposed. Saved agents must resolve `auto` to a supported concrete default before writing.

Inspect the callable subagent interface rather than assuming every Codex surface exposes the same controls. Treat implementation-specific controls as conditional adapters, not public guarantees. If a child inherits the root model, report `inherited root — requested child model was not used`; never count that as a successful executor or independent advisor route.

Do not report a model as used before Codex accepts the route. Distinguish these states:

- `pinned custom agent available`: a matching saved agent is loaded, but has not run yet.
- `live route available`: the current client exposes and accepts the requested model controls.
- `unverified prompt preference`: only prompt steering is available.
- `used and confirmed`: spawn or client metadata exposes the accepted route. Child prose alone is diagnostic, not routing proof.
- `unavailable`: the requested model/provider cannot be routed here.
- `none`: the advisor was disabled.

Report a compact activation status, then continue the work in the same invocation:

```text
Codex Orchestration
Orchestrator: <active model if exposed, otherwise current task model> — active
Advisor: <model>@<effort> — <state>, or none
Executor: <model>@<effort> — <state>
Delegation: allowed when useful; Codex planning and active Goal state unchanged
```

## Use the advisor only when it helps

Use the advisor only when configured and the root has produced a non-trivial plan or proposed executor slices worth checking. Skip it for simple work, while reporting that no plan review was needed.

Send one advisor a self-contained review packet before executor work begins. Include:

- user intent and acceptance criteria;
- important constraints and repository facts;
- the root's plan and proposed executor slices;
- dependencies, file ownership, and safe sequencing;
- material risks and verification checks.

The task-local advisor is review-only by instruction, not guaranteed read-only by its sandbox. A saved advisor requests `sandbox_mode = "read-only"`, but live parent permission overrides can still be reapplied. Tell the advisor not to edit, use mutating tools, spawn, delegate, contact executors, or make the final decision.

Use this response contract:

```text
Review only the supplied plan and executor slices. Look for material requirement
gaps, incorrect assumptions, missing dependencies, shallow or overlapping slices,
unsafe parallel writes, integration risks, and weak acceptance or verification.
Report only to the root. Do not edit, mutate, spawn, delegate, or contact executors.

Start with exactly one of:
PLAN_APPROVED
PLAN_REVISE
```

`PLAN_APPROVED` means the advisor found no material gap in the supplied packet; it is not a guarantee. `PLAN_REVISE` must give a short, prioritized list of material gaps and a concrete correction for each. Style preferences alone do not justify revision.

The root adjudicates the advice and owns every plan change. After a material revision, allow at most one confirmation review. After two valid reviews, record which advice the root accepted or rejected and proceed. Ask the user only when the advice exposes a genuine missing user choice, requirement, or authority.

Treat transport failure, malformed output, missing context, and inaccessible routing as root-side `advisor unavailable` states, never as approval. Supplying an advisor makes its review required for a non-trivial plan unless the user explicitly says best-effort; simple work can still skip review because there is no material plan to gate. When required review is unavailable, do not silently substitute the root model or release executor work; ask the user to choose `advisor: none`, save a compatible advisor for a new task, or change surfaces. If they explicitly made review best-effort, disclose the failure and continue under the root.

## Delegate bounded executor work

After Codex independently decides delegation will help, give each executor a complete slice:

- one objective and clear boundaries;
- only the context and repository facts it needs;
- owned files or an explicit read-only assignment;
- dependencies and stop conditions;
- acceptance criteria and the smallest useful verification command;
- the required handoff format.

Require the executor to stay inside the slice, preserve unrelated work, avoid contacting the advisor, avoid spawning descendants, and report blockers rather than guessing. Its handoff should contain status, work completed, files or evidence, checks run, and remaining risks.

Parallelize only independent slices. Give write-heavy executors non-overlapping ownership. Keep tightly coupled changes sequential or with the root. Do not discard context needed for correctness merely to force a cheaper model.

The root inspects each handoff, integrates the work, resolves conflicts, runs final verification, and writes the user-facing answer. Executor completion is never final acceptance.

An ordinary executor choice is a routing preference, not a requirement that every task spawn. If the route is unavailable, disclose it and let the root continue work that does not need delegation; never silently count an inherited-root child as the requested executor. If the user explicitly requires that executor route or requires delegated execution, treat unavailability as a gate and ask for a compatible route or permission for root-owned work.

## Save custom agents only when asked

Task-local preference is the default. Persist only after an explicit `save` or `persist` request.

Use project scope for `save for this project`. Use personal scope only after explicit approval because it affects every project. A cross-provider pin normally requires personal scope, an already configured Codex-compatible provider, and a new task.

Run the bundled configurator with an available Python 3.11+ interpreter from this skill's real directory, never from a repository-relative path in the user's workspace. Use `python3` on typical macOS/Linux systems; on Windows choose an available `py -3.11` or `python` launcher after checking its version:

```bash
python3 <skill-dir>/scripts/configure_orchestration.py \
  --scope project \
  --root <workspace> \
  --executor-model <exact-model-id> \
  --executor-effort <effort-or-auto> \
  --advisor-model <exact-model-id> \
  --advisor-effort <effort-or-auto>
```

Omit advisor model flags when no advisor was supplied. When the user explicitly saves `advisor: none`, pass `--remove-advisor`. Pass the exact validated `--codex-bin` when the active Desktop host and the shell `codex` differ. Report the binary/version/catalog source shown by the preview.

Run dry-run first and inspect the complete diff. An explicit `save for this project` authorizes applying a clean, non-migration project preview. Legacy migration always requires the user to approve the backup/deletion preview before `--apply`, even in project scope. Get separate approval before applying personal scope. Use `--confirm-unlisted-models` only after the executing host independently confirms each exact model; require explicit effort when a catalog cannot resolve `auto`.

Normal persistence creates namespaced standalone agents only. For project scope, use the trusted workspace or repository root rather than an arbitrary subdirectory:

```text
.codex/agents/codex-orchestration-executor.toml
.codex/agents/codex-orchestration-advisor.toml
```

It must not modify the root model, `.codex/config.toml`, provider definitions, credentials, global agent limits, built-in agents, or user-owned custom agents. The saved executor contains only bounded-child instructions. The saved advisor contains only root-facing review instructions and requests a read-only sandbox.

Do not run persistence while an untrusted process under the same OS account is mutating or swapping the workspace's `.codex` directories. The configurator rejects observed symlinks, hard links, and concurrent file changes; it is not a sandbox against a hostile same-user process with equivalent filesystem access.

If older generated output is detected, use `--migrate-legacy` only after reviewing the backup and deletion preview. Remove only artifacts whose complete known schema proves ownership. Preserve root model, provider, effort, concurrency settings, unrelated config, and edited or unknown files.

The configurator journals multi-file writes without recording config contents or credentials. A dry run must stop if it detects an interrupted transaction; an approved `--apply` recovers that transaction before evaluating new changes. Do not delete or bypass a recovery journal manually.

On Windows, allow a first save into absent files. If a managed destination already exists, the configurator fails closed because this release cannot preserve and verify custom NTFS security descriptors. Report that limitation and require manual review; do not weaken the guard.

Saved agents load only in a new task. Do not claim they became active in the current task, and do not imply that an active Goal moves to the new task.

When the user asks to remove saved roles, preview and then run the configurator with the same scope and `--remove-saved-roles`. This removes only fully validated, namespaced executor/advisor files; it refuses edited or user-owned files and leaves `.codex/config.toml` untouched. Remove saved roles before uninstalling the plugin, then start a new task.

## Keep savings claims accurate

The purpose is to spend high-end model capacity where judgment matters and use an efficient model for eligible volume. Do not delegate or invoke an advisor solely to hit a savings target.

When explaining the “about 65%” example, call it an illustrative credit calculation: published GPT-5.6 Luna token credit rates are 20% of Sol's, so a comparable mix with 20% on Sol and 80% on Luna is `0.20 + (0.80 × 0.20) = 0.36`, about 64% fewer credits before multi-agent overhead. Never call that 65% fewer raw tokens, a guaranteed five-hour or weekly-limit saving, a fixed monetary saving, or 5× more work.

Subagents can increase total tokens. Advisor calls, copied context, retries, tools, and provider-specific billing may erase some or all of the apparent saving.

## Resources

- `scripts/inspect_models.py`: inspect a Codex CLI catalog as a fallible host signal.
- `scripts/configure_orchestration.py`: preview and apply optional namespaced custom agents.
- [providers-and-models.md](references/providers-and-models.md): model, provider, routing, sandbox, Goal, and usage constraints.
