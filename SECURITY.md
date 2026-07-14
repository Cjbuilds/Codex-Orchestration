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

Codex-Orchestration writes only its documented Codex routing fields and managed custom-agent files. Native setup/status/disable and Fable authorization use the same full-state validator. Saved routing state must match a known exact-integer schema/policy pair, the fields available in that historical schema, valid restoration snapshots, and a safe scalar/MCP relationship; unknown extensions fail closed. The bundled Fable Planner/Advisor bridge disables tools and session persistence, strips provider override credentials, and requires runtime usage metadata to contain the pinned Fable primary plus only explicitly allowlisted Claude Code helpers. Unknown additional models fail closed. The managed workflow authorizes only the root Codex model to call planning tools, but the current MCP protocol does not provide caller identity to the bridge; that caller boundary is instruction-enforced rather than server-authenticated. The plugin does not create providers, handle provider credentials, weaken sandbox or approval settings, or guarantee that policy-guided routing is engine-enforced. See the README for the exact runtime-verification boundary.
