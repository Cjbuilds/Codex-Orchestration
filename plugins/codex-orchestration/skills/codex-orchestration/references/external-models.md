# External Models

External Models lets Codex Orchestration assign audited non-picker models to bounded
roles. The current Codex task model remains root. The subsystem never writes the
top-level Codex `model` or `model_provider`, never adds an external model to the
Desktop picker, and never reads or deletes chats, sessions, or OpenAI auth.

## Trust lanes

Only two lanes are accepted:

1. A bundled, reviewed native provider manifest using HTTPS and the Responses API,
   plus command-backed authentication and provider-pinned personal custom agents.
2. A bundled, reviewed first-party subscription CLI adapter. Claude Fable 5 is the
   only adapter in this lane and retains its no-tools, no-session-persistence,
   first-party-login, runtime-metadata bridge.

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
  --effort medium \
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
  --effort medium

python3 <skill-dir>/scripts/external_configurator.py connect \
  --role researcher \
  --purpose "Gather evidence from the bounded packet and cite sources." \
  --provider openrouter \
  --model moonshotai/kimi-k3 \
  --effort medium \
  --apply
```

The configurator creates one personal provider-pinned agent per manifest-validated
effort. Start a new Codex task so Desktop loads those files, preview `ready`, then
apply it. Read-only `resolve --role researcher --effort medium` returns the exact
loaded agent name root should delegate to. The agent file, not prompt text, binds
provider, model, and effort.

## Calling a role

Normalize forms such as these:

```text
call researcher at medium — <bounded task>
use reviewer@high for <bounded task>
researcher: <bounded task> (effort medium)
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

## Kimi K3 candidate status

The included candidate uses the user-supplied exact ID `moonshotai/kimi-k3`, context
claim `1048576`, auto-compaction limit `950000`, and `medium` effort. As of the adapter review on 2026-07-17, the
current OpenRouter listing confirmed Responses API routes for listed Kimi models but
did not list Kimi K3. Therefore the manifest remains experimental and unqualified.
Do not replace it with a dated or "latest" alias. Gate 0 must fail closed until the
exact candidate is available and authorized.
