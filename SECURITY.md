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

Native setup/status/disable and Fable authorization retain their full-state
validators. The bundled Fable Planner/Advisor bridge disables tools and session
persistence, strips provider override credentials, and requires runtime usage
metadata to contain the pinned Fable primary plus only explicitly allowlisted Claude
Code helpers. The managed workflow authorizes only root to call planning tools, but
MCP does not provide caller identity; that caller boundary remains
instruction-enforced rather than server-authenticated.

External providers receive delegated prompt content and may retain it under their
own policies. OS credential stores, first-party subscription CLIs, Codex itself, and
provider endpoints are trusted dependencies. The plugin does not weaken sandbox or
approval settings and cannot guarantee that policy-guided delegation is
engine-enforced. See the README and External Models reference for the operational
contract.
