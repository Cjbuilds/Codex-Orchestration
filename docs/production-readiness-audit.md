# Production-readiness audit

Audit date: 2026-07-12. Baseline: `a674a81` (`0.4.0`).

## Release blockers found

| Severity | Shortcoming | Resolution in 0.5.0 |
| --- | --- | --- |
| High | `main` was an unprotected mutable distribution branch. | Require pull requests, current required checks, resolved conversations, admin enforcement, and block force-push/deletion. |
| High | Same-provider routing could be mistaken for an engine-enforced executor selector. | Describe it as policy-guided routing and distinguish config compatibility, effective policy, accepted route, and confirmed runtime identity. |
| Medium | Restore-state persistence failure ignored the config rollback status and could report false success. | Validate rollback status and report that managed fields may remain whenever rollback is not proven. |
| Medium | Status always exited zero for conflicts, overrides, incomplete controls, or unavailable roles. | Add `--require-effective` and negative-path coverage. |
| Medium | Cross-provider setup spans separate role-file and native-policy transactions. | Detect orphaned managed roles, require bounded cleanup on phase-two failure, and document interruption recovery. |
| Medium | GitHub Actions used mutable major tags and the repository did not require SHA pinning. | Pin actions to full reviewed SHAs, restrict Actions, and add Dependabot updates. |
| Medium | Windows support was claimed without CI or the custom-role update/removal limitation. | Add Windows/macOS portability checks and document the Windows fail-closed boundary. |
| Medium | Released version `0.4.0` had no immutable tag or GitHub release. | Add a tag/version release gate; `0.5.0` must ship from a signed `v0.5.0` tag and matching GitHub release. |
| Low | Code scanning, dependency alerts, private vulnerability reporting, ownership, contribution, and release policies were absent. | Add CodeQL, Dependabot, `SECURITY.md`, `CODEOWNERS`, `CONTRIBUTING.md`, and `RELEASE.md`; enable repository security features. |
| Low | CI had no static-quality baseline. | Add a pinned Ruff check and Dependabot updates for the development tool. |
| Low | The documented fixed v2 concurrency count was stale. | Defer to the effective Codex/config limit instead of hard-coding a count. |

## Deliberate boundaries that remain

- Codex currently exposes no global engine field that hard-wires one executor model. The native path installs durable routing policy on the spawn tool; the root still decides whether to delegate and supplies the route.
- Setup-time config parsing cannot prove a future signed-in task will accept a route or expose its effective child identity. A live release check is required.
- Direct model overrides inherit the root provider. Cross-provider use requires a provider-pinned custom agent that the user configured and authenticated separately.
- Custom-agent updates and removal remain fail-closed on Windows because the implementation cannot preserve the same inode/metadata guarantees there. Native App Server policy setup is a separate path.
- The two cross-provider storage systems cannot be committed atomically by the current public interfaces. Status and bounded managed-role cleanup provide recovery without deleting edited or user-owned files.

These are platform boundaries, not hidden guarantees. A future Codex-native executor selector or transactional custom-agent API would justify revisiting them.
