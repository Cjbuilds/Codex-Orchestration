# Security policy

## Supported versions

Security fixes are made on the latest released version. Upgrade before reporting a problem that is already fixed on `main`.

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Use [GitHub private vulnerability reporting](https://github.com/Cjbuilds/Codex-Orchestration/security/advisories/new) and include:

- the affected version and Codex client version;
- operating system and installation scope;
- a minimal reproduction;
- the security impact and any known workaround.

Do not include credentials, tokens, or private configuration. You should receive an acknowledgement within seven days. A coordinated disclosure date will be agreed after the impact and fix are verified.

## Security boundaries

Codex-Orchestration changes only documented routing fields, explicitly prepared
`model_providers.<id>` tables, plugin-managed personal agent files, and strict
non-secret state under `CODEX_HOME`. External setup never writes top-level `model`
or `model_provider`, never edits OpenAI authentication, and never reads, migrates,
or deletes chat/session storage.

The explicit update control first requires exactly one enabled installed plugin with
the canonical HTTPS Git marketplace identity. It then delegates refresh, transport,
process containment, cache mutation, and installation exclusively to Codex's native
`plugin marketplace upgrade` and `plugin add` commands, followed by a strict native
inventory check for canonical source, nondecreasing SemVer, and retained enabled
state. The skill introduces no downloader, Git client, subprocess wrapper, or
rollback claim and does not construct a credential-bearing environment. It never
invokes plugin removal, rewrites config, reads credentials, or reads/writes routing,
provider, chat, or session state.

Provider API keys are accepted only by a hidden local prompt outside chat and are
stored in the operating-system credential store. Codex retrieves a key at request
time through documented command-backed auth and a stable helper under `CODEX_HOME`;
the provider table stores only the helper
path and non-secret arguments. The plugin rejects secret-capable registry fields,
provider ID collisions, unsafe URLs, unknown manifest fields, symlinks, hardlinks,
stale compare-and-swap digests, unqualified adapters, unsupported efforts, and
changed helper or CLI bytes. A user-supplied helper is executable code and must be
explicitly trusted; byte drift changes its status to `CLI_CHANGED` and requires
re-trust.

The command-backed helper necessarily returns the credential over captured stdout
to the local readiness check or Codex provider process that invoked it. Those are
trusted recipients; the value is kept in memory only, discarded immediately, and
never included in diagnostics, model prompts, state files, or decorated output.

Role resolution is a fresh authorization check, not a registry lookup: it compares
the bundled adapter version and capability declaration, live App Server provider
table, qualification/readiness state, credential-helper identity, credential
availability, and selected personal-agent digest. Any mismatch blocks delegation.

External provider preparation and removal use exact App Server readback plus a
content-free recovery journal. Role files and registry state use a recoverable
multi-file transaction. Recovery rolls forward or back only when every digest and
ownership check matches; ambiguity becomes `RECOVERY_REQUIRED` without overwriting
user data. On Windows, replacement stages copy and canonically verify the existing
owner, group, DACL, and mandatory integrity label before publication; inability to
read, apply through Windows' `SetNamedSecurityInfoW` API, or re-read that
access-control metadata fails closed and rolls the transaction back.

Gate 0 is an explicit, potentially billable, ephemeral `codex exec` probe in an
isolated temporary `CODEX_HOME`. The pinned CLI must advertise every required flag
before the billable command starts. Decorated output is discarded, and only a
bounded, regular, single-link `--output-last-message` artifact can satisfy the fixed
signal. A successful response proves route acceptance, not the model's runtime
identity. Native providers remain
`ROUTE_ACCEPTED` unless the host exposes mechanical provider/model metadata; model
self-report is never confirmation.

Native setup/status/repair/disable and bundled bridge authorization retain their full-state
validators. Repair is allowed only when valid saved state exists, both live hint
strings retain the ownership marker, and namespace, spawn metadata, bundled launcher
enablement, scalar-conversion shape, and all other managed values still match. It
restores only drifted mode/usage bytes through App Server compare-and-swap, verifies
user and effective readback, rolls back on an override, preserves a concurrent edit,
detects concurrent saved-state replacement without overwriting it, and never changes
restore state, authentication, credentials, chats, or sessions.
The bundled Fable Planner/Advisor bridge disables tools and session
persistence, strips provider override credentials, and requires runtime usage
metadata to contain the pinned Fable primary plus only explicitly allowlisted Claude
Code helpers. The managed workflow authorizes only root to call planning tools, but
MCP does not provide caller identity; that caller boundary remains
instruction-enforced rather than server-authenticated.

The bundled Kimi K3 Designer bridge accepts only the exact local Kimi Code OAuth
catalog route and rejects API-key-backed or ambiently overridden configurations. It
scrubs provider keys and every caller-supplied `KIMI_MODEL_*` value, then injects
only its own documented `KIMI_MODEL_THINKING_EFFORT=max` wire control. It invokes
`acpx` with permissions denied,
terminal disabled, no MCP servers, one turn, and a disposable empty working
directory, then rejects tool, permission, filesystem, and terminal events in the
ACP transcript. It mechanically verifies the ACP-selected `kimi-code/k3` model and
requires a bounded `DESIGN_HANDOFF` result. ACP does not emit a separate effort
identity; the bridge therefore requires catalog support for `max`, controls the
wire effort itself, and never represents that as runtime effort telemetry. The OAuth
credential remains owned by Kimi Code CLI and is never read, copied, logged, or
returned by the plugin.

Routing schema/policy version 5 adds the sealed `kimi_cli` Designer route while
retaining strict validation for schemas 1–4. Legacy schemas cannot smuggle newer
fields. Persistent Designer accepts only a direct same-provider model or the exact
bundled Kimi route, never the privileged Fable MCP route or a project-shadowable
unqualified agent name.
Cross-provider/custom Designers remain task-local and require current-project
validation immediately before use. Designer authority is
policy-bounded: it reports only to root, cannot contact other seats or spawn
descendants, may edit only explicitly delegated design artifacts, and cannot alter
the canonical plan, implementation code, approvals, or Executor release. These
behavioral limits are instruction-enforced; normal Codex sandbox and approval
controls remain the mechanical boundary.

External providers receive delegated prompt content and may retain it under their
own policies. OS credential stores, first-party subscription CLIs, Codex itself, and
provider endpoints are trusted dependencies. The plugin does not weaken sandbox or
approval settings and cannot guarantee that policy-guided delegation is
engine-enforced. See the README and External Models reference for the operational
contract.
