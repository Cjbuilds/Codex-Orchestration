# External Models

External Models lets Codex Orchestration assign audited non-picker models to bounded
roles. The current Codex task model remains root. The subsystem never writes the
top-level Codex `model` or `model_provider`, never adds an external model to the
Desktop picker, and never reads or deletes chats, sessions, or OpenAI auth.

## Trust lanes

Only two lanes are accepted:

1. A bundled, reviewed native provider manifest using HTTPS and the Responses API,
   plus command-backed authentication and provider-pinned personal custom agents.
2. A bundled, reviewed first-party subscription CLI adapter. Claude Fable 5 retains
   its no-tools, no-session-persistence, first-party-login, runtime-metadata bridge;
   Kimi K3 Designer uses a separate deny-all ACP bridge with first-party OAuth and
   mechanical runtime-model confirmation.

An arbitrary URL, model ID, effort, shell command, project-local helper, or generic
subscription CLI is not a provider. Additions require code review, exact schemas,
negative tests, and a new plugin release.

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

## Explicit API-backed entry

An External Model request must name the provider and exact model, for example
`OpenRouter model moonshotai/kimi-k3 at max`. It remains a task-local provider-pinned
role and never puts that model in the Desktop picker. The shorter built-in label
`Designer: Kimi K3` is intentionally not an External Model request: version 0.8.0
routes that label through the sealed Kimi Code OAuth/ACP Designer bridge described
in the main skill contract. Never silently substitute one lane for the other.

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

The explicit configure request authorizes clean preparation and clean role creation.
It never authorizes credential entry,
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

## OpenRouter Kimi K3 status

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

## Subscription Kimi K3 Designer status

The separate built-in `Designer: Kimi K3` route uses Kimi Code CLI 0.27.0 or newer,
`acpx` 0.12.0 or newer, and the existing Kimi Code OAuth subscription. It does not
prepare OpenRouter, request an API key, run Gate 0, or create a provider-pinned
custom agent. Omitted effort, `auto`, and `max` all select the audited `max` route;
every other explicit effort is rejected.

Setup verifies the non-secret local catalog tuple `managed:kimi-code`,
`kimi-code/k3`, OAuth, and default effort `max`, then enables one disabled-by-default
MCP launcher. Each call starts a fresh ACP session in an empty temporary directory,
pins K3, denies permissions, disables terminal capability, passes no MCP servers,
rejects any tool/filesystem/terminal request or event, verifies the ACP-selected
runtime model, and requires a `DESIGN_HANDOFF` signal. ACP does not expose a separate
runtime effort field, so the adapter rechecks the catalog's audited `max` default on
every call and reports that distinction honestly.
