# Release process

1. Replace `Unreleased` in `CHANGELOG.md` with the release date.
2. Confirm `.codex-plugin/plugin.json`, the changelog, the installed package, and lifecycle fixture all use the same semantic version.
3. Run:

   ```bash
   python3 -m compileall -q plugins tests scripts
   python3 -m ruff check plugins tests scripts
   python3 -m unittest discover -s tests -v
   python3 tests/plugin_lifecycle_smoke.py
   python3 scripts/release_check.py
   ```

4. From a new Desktop task, verify one direct same-provider child route. Record `route accepted`; record `used and confirmed` only if the client exposes effective child model/provider/effort metadata.
5. Merge only after every protected check passes.
6. Create a signed annotated tag named `v<manifest-version>` at the reviewed merge commit.
7. Re-run `python3 scripts/release_check.py --require-tag` and publish a GitHub release from that tag using the matching changelog section.
8. Install from the public marketplace in a clean Codex home, start a new task, and verify setup, `status --require-effective`, and disable.

Never move a published release tag. If a release is bad, fix forward with a new version and retain the old tag as provenance.
