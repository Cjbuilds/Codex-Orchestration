# Production-readiness audit

Audit date: 2026-07-12. Baseline: `a674a81` (`0.4.0`).

## Release blockers found

| Severity | Shortcoming | Resolution |
| --- | --- | --- |
| High | The README led with internal routing details and did not explain the product's general multi-model role workflow. | Lead with a plain-language definition, a provider-neutral workflow diagram, the value proposition, and then installation and examples. |
| High | Claude Fable 5 was developed separately, so the launch branch could not truthfully advertise the requested advisor workflow. | Integrate the opt-in, root-directed Fable bridge, disabled by default, with login checks, runtime-model confirmation, and fail-closed review decisions. |
| High | `main` was an unprotected mutable distribution branch. | Require pull requests, current required checks, resolved conversations, admin enforcement, and block force-push/deletion. |
| High | Same-provider routing could be mistaken for an engine-enforced executor selector. | Describe it as policy-guided routing and distinguish config compatibility, effective policy, accepted route, and confirmed runtime identity. |
| Medium | Restore-state persistence failure ignored the config rollback status and could report false success. | Validate rollback status and report that managed fields may remain whenever rollback is not proven. |
| Medium | Duplicated partial state validation accepted impossible schema/policy, nested restore, and scalar-conversion combinations that could make disable replace unrelated configuration. | Use one shared full-state validator for native and Fable paths, with exact schema-specific keys, snapshots, scalar/MCP relationships, routes, and marker-owned policy strings. |
| Medium | Fable runtime confirmation accepted any extra model as long as `claude-fable-5` also appeared. | Require the pinned Fable primary and an exact allowlist of observed Claude Code helper models; reject every unknown additional model. |
| Medium | Planner support reused the same `0.5.0` identity as an earlier Advisor-only bundle, allowing a stale marketplace/cache to keep role-obsolete instructions. | Release the complete Planner contract as `0.5.1`, treat explicit seat labels as authoritative, and lifecycle-test upgrade from the exact affected bundle into an installed Planner-capable package. |
| Medium | Status always exited zero for conflicts, overrides, incomplete controls, or unavailable roles. | Add `--require-effective` and negative-path coverage. |
| Medium | Cross-provider setup spans separate role-file and native-policy transactions. | Detect orphaned managed roles, require bounded cleanup on phase-two failure, and document interruption recovery. |
| Medium | The skill explained only fixed advisor/executor seats, leaving arbitrary project roles, workflow ordering, Goal behavior, and permissions ambiguous. | Add native custom-role creation rules, project and personal scopes, user ownership, provider checks, bounded handoffs, and explicit Goal/permission boundaries. |
| Medium | GitHub Actions used mutable major tags and the repository did not require SHA pinning. | Pin actions to full reviewed SHAs, restrict Actions, and add Dependabot updates. |
| Medium | Windows support was claimed without CI or the custom-role update/removal limitation. | Add Windows/macOS portability checks and document the Windows fail-closed boundary. |
| Medium | Released version `0.4.0` had no immutable tag or GitHub release. | Add a tag/version release gate; `0.5.1` must ship from a signed `v0.5.1` tag and matching GitHub release. |
| Low | Code scanning, dependency alerts, private vulnerability reporting, ownership, contribution, and release policies were absent. | Add CodeQL, Dependabot, `SECURITY.md`, `CODEOWNERS`, `CONTRIBUTING.md`, and `RELEASE.md`; enable repository security features. |
| Low | CI had no static-quality baseline. | Add a pinned Ruff check and Dependabot updates for the development tool. |
| Low | The documented fixed v2 concurrency count was stale. | Defer to the effective Codex/config limit instead of hard-coding a count. |

## Deliberate boundaries that remain

- External Model READY roles use a sealed direct CLI transport, not Desktop's
  native spawn-agent server-tool path. Inputs and outputs are hard bounded, provider
  streams are withheld, command-backed auth remains secret-free, tools are disabled,
  and binary/config/role/shadow integrity fail closed. Windows process-group cleanup
  can signal the group and hard-kill the child but cannot provide Unix-style
  descendant enumeration; disabling model tools removes the supported descendant
  creation path.

- Codex currently exposes no global engine field that hard-wires one executor model. The native path installs durable routing policy on the spawn tool; the root still decides whether to delegate and supplies the route.
- Setup-time config parsing cannot prove a future signed-in task will accept a route or expose its effective child identity. A live release check is required.
- Direct model overrides inherit the root provider. Cross-provider use requires a provider-pinned custom agent that the user configured and authenticated separately.
- Claude Fable 5 is a narrow built-in exception: it uses the authenticated first-party Claude Code CLI through a read-only local MCP bridge and is available as a root-facing Planner or Advisor.
- The managed policy reserves Fable planning calls for the root, but current MCP requests carry no caller identity. The caller boundary is instruction-enforced; no-tools execution, state authorization, effort/model pinning, and runtime model verification are enforced by the bridge.
- "Any model" means a model reachable through Codex's current provider, an already configured compatible custom provider, or a deliberately bundled bridge. The plugin does not create accounts, credentials, or protocol compatibility.
- Custom-agent updates and removal remain fail-closed on Windows because the implementation cannot preserve the same inode/metadata guarantees there. Native App Server policy setup is a separate path.
- The two cross-provider storage systems cannot be committed atomically by the current public interfaces. Status and bounded managed-role cleanup provide recovery without deleting edited or user-owned files.

These are platform boundaries, not hidden guarantees. A future Codex-native executor selector or transactional custom-agent API would justify revisiting them.
