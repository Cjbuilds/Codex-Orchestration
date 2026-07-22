# External Models

External Models lets Codex Orchestration assign audited non-picker models to bounded
roles. The current Codex task model remains root. The subsystem never writes the
top-level Codex `model` or `model_provider`, never adds an external model to the
Desktop picker, and never reads or deletes chats, sessions, or OpenAI auth.

## Trust lanes

Only three lanes are accepted:

1. A bundled, reviewed native provider manifest using HTTPS and the Responses API,
   plus command-backed authentication and provider-pinned personal custom agents.
2. A bundled, reviewed first-party subscription CLI adapter. Claude Fable 5 is the
   only adapter in this lane and retains its no-tools, no-session-persistence,
   first-party-login, runtime-metadata bridge.
3. A bundled, reviewed sealed HTTP role adapter for an official provider whose wire
   protocol Codex cannot load natively. Z.AI/BigModel GLM-5.2 is the only adapter in
   this lane. It uses OS-store authentication, exact-tuple Gate 0, no model tools,
   and response-model metadata validation.

An arbitrary URL, model ID, effort, shell command, project-local helper, or generic
subscription CLI is not a provider. Additions require code review, exact schemas,
negative tests, and a new plugin release.

## Official GLM-5.2 custom roles

Z.AI/BigModel exposes GLM-5.2 through one audited Chat Completions endpoint:
`https://open.bigmodel.cn/api/paas/v4/chat/completions`. The credential identity is
`zai`. Coding Plan is not configured or used.

Codex currently accepts only the Responses wire protocol and rejects
`wire_api = "chat"`; therefore GLM is not installed as a Codex model provider or
personal agent. `zai_glm_roles.py` is a separate sealed, no-tools role-call boundary
that leaves the root model and picker unchanged and never uses OpenRouter.

The exact supported tuples are `glm-5.2` / effort `high|max`; omitted or `auto`
resolves to `high`. Deep thinking is enabled, matching GLM-5.2's official effort
control. The manifests record the official 1M context and 128K maximum output,
while each role sets a smaller bounded output limit. There is no channel-control or
automatic endpoint-fallback surface.

The lifecycle is:

```text
UNCONFIGURED -> AUTH_REQUIRED -> TUPLE_QUALIFIED -> ROLE_READY -> USED_CONFIRMED
```

Preparation is preview-first, installs the audited stable credential helper, and
stores no secret. The user enrolls the key outside chat through the OS credential
store. Gate 0 is one separately authorized potentially billable fixed request and
qualifies only the exact model/effort/endpoint tuple. Role creation is
clean-add only. The exact-signal probe keeps Thinking enabled at the exact
qualified effort and retains strict equality. Calls
read one bounded regular task file or a strict versioned context envelope, send no tools, and accept output only when the
official response metadata names exact model `glm-5.2`.

The sealed adapter is distinct from a direct selectable `zai/glm-5.2` child when the
active host exposes that route. Direct selection uses the host's sub-agent transport;
it does not inherit the sealed registry, credential helper, qualification, or
runtime-evidence contract.

Completed role calls expose usage as evidence distinct from model identity. The
sealed adapter copies only validated non-negative integer `prompt_tokens`,
`completion_tokens`, `total_tokens`, and optional
`prompt_tokens_details.cached_tokens` into a new summary. `usage_state=REPORTED`
means those counters were supplied and validated; an absent top-level usage object
produces `usage_state=NOT_REPORTED` with `usage=null` while `USED_CONFIRMED` remains
based on exact response-model metadata. Present malformed usage fails closed before
content release. Raw provider objects, request IDs, and unknown metadata are never
forwarded.

Credential readiness is a separate nonsecret three-state contract:

```text
READY | AUTH_REQUIRED | CREDENTIAL_STORE_UNREACHABLE
```

On Linux, a completed `secret-tool lookup` with no value and no diagnostic is a
genuine missing credential. Secret Service transport, D-Bus, permission, locked
collection, timeout, helper-launch, and indeterminate failures are unreachable, not
missing. The helper returns exit 2 only for `AUTH_REQUIRED` and exit 3 for
`CREDENTIAL_STORE_UNREACHABLE`; provider output is never copied into diagnostics.

The root reacts only to `authentication_state` or the dedicated exit code. When the
store is unreachable in a restricted sandbox, it requests ordinary Codex permission
to rerun the complete official GLM `status` command host-side. If host status is
`READY`, the complete role `call` command also runs host-side so the OS lookup and
HTTPS request stay in one trusted process. The root never runs the credential
helper's `get` action directly and never transports a bearer through tool output,
prompts, environment variables, configuration, or shell interpolation. An
unreachable host retry remains fail-closed and never becomes enrollment advice.

### Stateless context integrity

New orchestrated sealed GLM calls use `codex-orchestration.context/v1`; the legacy
plain task-file path remains available only for backward compatibility. The strict
packet carries role, phase, round, source version, objective, context, constraints,
complete current artifact when required, cumulative findings ledger, exact open
finding IDs, and a code-owned output protocol. Duplicate/unknown keys, stale internal
artifact versions, incomplete open-finding sets, role/phase mismatches, and malformed
types fail closed.

A read-only `context` preview returns only safe schema/version/digest/size metadata.
The subsequent call must supply the same expected source version and SHA-256. The
model's final nonempty response line acknowledges both values, and the adapter
revalidates that acknowledgement before releasing content. This proves exact packet
binding, not semantic completeness; the root remains responsible for including the
full authoritative state.

Packets and outputs are never stored. Retries are new stateless calls and must resend
the complete current packet rather than a shortened delta. Before reading the bearer,
the adapter uses the complete serialized request's UTF-8 byte length as a conservative
prompt-token upper bound, adds configured output tokens, and fails closed if that sum
exceeds the manifest context window. Nothing is silently truncated or summarized.
See [context-packets.md](context-packets.md).

## Lifecycle

The native lifecycle is:

```text
UNCONFIGURED -> AUTH_REQUIRED -> AUTH_READY -> CAPABILITY_VERIFIED
             -> ROLE_STAGED -> RESTART_REQUIRED -> READY
             -> ROUTE_ACCEPTED -> USED_CONFIRMED (only with mechanical metadata)
```

`CLI_CHANGED`, `CONFIG_DRIFT`, `ROLE_COLLISION`, and `RECOVERY_REQUIRED` block use.
No state may skip an unlisted transition. Provider self-report or model-authored text
never establishes `USED_CONFIRMED`.

## Seat-label entry

A built-in seat label may select a bundled External Model without putting that model
in the Desktop picker. Labels are authoritative and never reassign a model to a
different role.

The official GLM shorthand is case-insensitive `GLM-5.2`, `GLM 5.2`, or exact ID
`glm-5.2` after any of `planner:`, `advisor:`, `designer:`, or `executor:`. It maps
to the same lower-case role ID, provider `zai`, exact model `glm-5.2`, and effort
`high` when omitted or `auto`; explicit `high` and explicit `max` remain supported.
Every other effort is rejected. It uses the fixed General API endpoint through the
official sealed Chat Completions adapter at `open.bigmodel.cn`, never Coding Plan,
OpenRouter, a fallback endpoint, or native Codex model routing.

The Kimi shorthand remains case-insensitive `Designer: Kimi K3`, which means
task-local role `designer`, provider `openrouter`, exact model `moonshotai/kimi-k3`,
and effort `max`. Omitted effort or `auto` resolves to `max`; every other explicit
effort is rejected. Similar labels are accepted only after a reviewed plugin release
adds an unambiguous bundled mapping.

Root must inspect external status before acting. Dispatch by exact state:

| Exact state | Action |
| --- | --- |
| Provider absent | Preview and apply clean preparation, then stop for user-owned hidden authentication. |
| Authentication missing | Print the no-paste enrollment guidance and stop. |
| Tuple unqualified | Request separate billing approval immediately before one Gate 0; never infer approval from the seat label. |
| Qualified provider, role absent | Preview and apply `connect` for role `designer` with the bounded Designer purpose. |
| `RESTART_REQUIRED` | Require a full Desktop restart and new task; do not delegate. |
| Exact role `READY` | Run `resolve`, then delegate only to its returned loaded agent name. |
| Role mismatch, drift, shadow, or ambiguity | Stop with the exact blocker; never overwrite, disconnect, repair, or substitute. |

Official GLM seats use the shorter sealed-role lifecycle above rather than the
native table. Inspect `zai_glm_roles.py status`, require authentication and the exact
qualified tuple, then preview `seat --seat <role>`. An explicit GLM seat label
authorizes clean-add with `--apply` only when that exact role is absent. Existing
exact roles are idempotently `READY`; any mismatch blocks replacement. A task call
uses a private bounded temporary packet and the exact same-name role. Planner and
Advisor outputs additionally require their plan protocol signals before any content
is returned to root.

The explicit seat label authorizes clean preparation and clean role creation, just as
the equivalent literal configure request does. It never authorizes credential entry,
Gate 0 billing, a failed-probe retry, replacement, or deletion. External seat routes
remain task-local and are never stored in native routing state. Preserve any supplied
seats and original task across authentication, qualification, or restart boundaries.
Adding a role stages a new restart boundary and temporarily blocks other External
Model roles on that provider until `ready` succeeds or the staged role is disconnected;
native GPT routes, the root model, chats, and sessions remain untouched. Clean
preparation may add the exact audited OpenRouter provider entry when absent, but it
never modifies, replaces, or removes a pre-existing provider entry.

## Preview-first setup

Run the packaged script from the installed skill directory with Python 3.11+ and the
Codex binary used by the active host. Global options precede the subcommand.

Preview and apply provider preparation:

```bash
python3 <skill-dir>/scripts/external_configurator.py \
  --codex-bin <active-codex-binary> prepare --provider openrouter

python3 <skill-dir>/scripts/external_configurator.py \
  --codex-bin <active-codex-binary> prepare --provider openrouter --apply
```

Preparation adds only `[model_providers.openrouter]` and its command-backed `auth`
table through App Server compare-and-swap. It refuses an existing provider ID. It
installs a non-secret helper at
`<CODEX_HOME>/codex-orchestration/bin/external_auth_helper.py` and prints an
enrollment command.

Root must say:

```text
External provider authentication is required. Do not paste the API key into this
chat. Run the displayed enrollment command in a trusted local terminal; its hidden
local prompt stores the key in your operating-system credential store. Tell me when
that command succeeds.
```

Root must not run the enrollment command on the user's behalf because only the user
may enter the key outside chat. macOS uses Keychain, Linux uses Secret Service via
`secret-tool`, and Windows uses Credential Manager. Linux without Secret Service may
use an absolute, single-link executable only after previewing and explicitly adding
`--user-helper <path> --trust-user-helper`; the helper must print only the bearer
value to stdout and receives no stdin. Treat that helper as trusted code. Byte or
path drift blocks it as `CLI_CHANGED`.

After intentional same-path helper replacement, preview and apply
`trust-helper --provider <id>` (optionally with `--helper <same-absolute-path>`).
Re-trust clears qualification and requires authentication plus Gate 0 again. A path
change requires disconnecting roles and re-preparing the provider; it is never
silently accepted.

Every role resolution re-attests the provider manifest/version, exact App Server
provider table, qualification and readiness, credential-helper bytes, credential
availability, selected model capability declaration, and selected agent-file bytes.
Any missing file or drift fails closed before delegation. Adding a second role for
an already-ready provider is supported, but stages a new restart boundary; existing
roles remain blocked until the new role is validated with `ready` or disconnected.

After enrollment, Gate 0 requires explicit cost approval:

```bash
python3 <skill-dir>/scripts/external_configurator.py \
  --codex-bin <active-codex-binary> gate0 \
  --provider openrouter \
  --model moonshotai/kimi-k3 \
  --effort max \
  --acknowledge-billing
```

Before a billable command, Gate 0 verifies that the pinned Codex binary advertises
every required CLI control. It then uses a temporary `CODEX_HOME`, `codex exec
--ephemeral`, a read-only sandbox, a fixed prompt, and a bounded
`--output-last-message` artifact. Decorated stdout and stderr are discarded; only
that safe final-message artifact can satisfy the fixed signal. Success means the
exact provider/model/effort route accepted one request; it does not prove runtime
model identity.

Preview and create the role:

```bash
python3 <skill-dir>/scripts/external_configurator.py connect \
  --role researcher \
  --purpose "Gather evidence from the bounded packet and cite sources." \
  --provider openrouter \
  --model moonshotai/kimi-k3 \
  --effort max

python3 <skill-dir>/scripts/external_configurator.py connect \
  --role researcher \
  --purpose "Gather evidence from the bounded packet and cite sources." \
  --provider openrouter \
  --model moonshotai/kimi-k3 \
  --effort max \
  --apply
```

The configurator creates one personal provider-pinned agent per manifest-validated
effort. Start a new Codex task so Desktop loads those files, preview `ready`, then
apply it. Read-only `resolve --role researcher --effort max` returns the exact
loaded agent name root should delegate to. The agent file, not prompt text, binds
provider, model, and effort.

## Calling a role

Normalize forms such as these:

```text
call researcher at max — <bounded task>
use reviewer@high for <bounded task>
researcher: <bounded task> (effort max)
```

Resolve the role and effort through `external_configurator.py resolve`. Reject an
unknown role, unsupported effort, unready state, missing agent, digest drift, or
provider drift. Delegate only to the exact returned agent. Report `route accepted`
when the host accepts the spawn. Report `used and confirmed` only when mechanical
host/provider metadata names the provider and model; never rely on the model saying
its own name.

## Status, disconnect, and removal

`status` is read-only and makes no model call. It reports non-secret provider auth,
config integrity, qualification, role state, loaded variant names, and file
integrity.

Preview and apply `disconnect --role <id>` to remove only exact managed role files.
The provider stays prepared. Preview and apply `remove-provider --provider <id>` only
after its last role is disconnected. Removal requires an exact registry record and
exact current provider table. Edited or ambiguous state is preserved and becomes a
manual recovery case. Neither command traverses chat/session directories or touches
OpenAI auth.

## Stored state and secrets

The registry and recovery journal are strict schema-1 JSON files at the top of
`CODEX_HOME`, mode `0600` on POSIX. They store IDs, paths, hashes, states, timestamps,
and non-secret ownership metadata. Unknown fields and keys containing `api_key`,
`authorization`, `bearer`, `credential`, `password`, `secret`, or `token` fail
closed. They never store prompt packets, model output, keys, account IDs, or chat
content.

The bearer value crosses one unavoidable boundary: the OS credential helper prints
it to the Codex provider process on stdout, as required by Codex command-backed
authentication. Surrounding logs and errors withhold helper and provider output.

## Adding another provider

Every new native provider requires:

- one exact manifest in `providers/`, with HTTPS base URL, `wire_api = responses`,
  exact model IDs, exact effort allowlists, evidence source, and initial
  qualification state;
- an auth strategy using OS secure storage or a separately pinned absolute helper;
- an isolated Gate 0 test for every newly qualified model/effort tuple;
- malformed-schema, collision, auth failure, drift, rollback, redaction, and route
  identity tests;
- documentation of provider retention/privacy terms and the difference between
  route acceptance and runtime identity;
- a plugin version bump and fresh final-tree security review.

Every new subscription provider additionally requires an official first-party CLI,
audited login-status semantics, a fixed no-tools/no-persistence invocation, a sealed
operation allowlist, mechanical runtime model metadata, CLI re-attestation behavior,
and dedicated redaction tests. Do not generalize Fable's adapter into arbitrary CLI
execution.

## Kimi K3 status

As verified from OpenRouter's official model page and public endpoint metadata on
2026-07-18, `moonshotai/kimi-k3` is listed with context `1048576`, a live
Responses-compatible endpoint, and support for the reasoning parameter. OpenRouter
currently documents only `max` reasoning for Kimi K3. The plugin therefore accepts
`max`, lets `auto` resolve to `max`, and rejects `xhigh`, `high`, `medium`, `low`,
`minimal`, and `none` without clamping. The auto-compaction limit remains `950000`.

The bundled adapter is no longer experimental, but official listing evidence is not
per-install route qualification. Every installation starts unqualified and must pass
one explicitly authorized, potentially billable Gate 0 for the exact
OpenRouter/Kimi/max tuple before role creation. Upstream capacity may return `429`;
that is a failed probe and must not be retried without renewed approval. Do not
replace the exact model ID with a dated or `latest` alias.
