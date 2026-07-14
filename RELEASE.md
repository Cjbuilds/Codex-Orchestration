# Release process

1. Replace `Unreleased` in `CHANGELOG.md` with the release date.
2. Confirm `.codex-plugin/plugin.json`, the changelog, the installed package, and lifecycle fixture all use the same new semantic version. Never publish different plugin behavior under a version already used by another bundle; fix forward with a new version so Codex cannot reuse the old cache identity.
3. Run:

   ```bash
   python3 -m compileall -q plugins tests scripts
   python3 -m ruff check plugins tests scripts
   python3 -m unittest discover -s tests -v
   python3 tests/plugin_lifecycle_smoke.py
   python3 scripts/release_check.py
   ```

4. From a new Desktop task, verify one direct same-provider child route. Record `route accepted`; record `used and confirmed` only if the client exposes effective child model/provider/effort metadata.
5. If Claude Fable 5 is included in the release, verify both supported seat paths from a first-party Claude login: Fable Planner `create_plan`/`revise_plan` with a different Advisor, and Fable Advisor `review_plan` with root planning. Confirm the pinned primary model, exact allowlisted helper set reported by runtime metadata, effort, status, the bounded approval loop, and disable/restore. An unknown helper model is a release failure, not an implicit allowlist expansion.
6. Merge only after every protected check passes.
7. Create a signed annotated tag named `v<manifest-version>` at the reviewed merge commit.
8. Re-run `python3 scripts/release_check.py --require-tag` and publish a GitHub release from that tag using the matching changelog section.
9. Upgrade from the previous public version in a clean Codex home, reinstall the plugin, and verify the installed version and skill contents changed before starting a new task. Then verify setup, `status --require-effective`, and disable.

Never move a published release tag. If a release is bad, fix forward with a new version and retain the old tag as provenance.

Before downgrading to a release that predates Planner/state-schema-3 support, run `disable` with the current release. Older versions must fail closed on the unknown state schema rather than guessing how to restore it.
