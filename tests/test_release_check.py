from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "release_check.py"
SPEC = importlib.util.spec_from_file_location("release_check", SCRIPT)
assert SPEC and SPEC.loader
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class TempRepository:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Preflight Test")
        self.git("config", "user.email", "preflight@example.invalid")

    def close(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            shell=False,
        )
        return completed.stdout.strip()

    def write_release(self, version: str, *, payload: str = "payload") -> None:
        files = {
            "plugins/codex-orchestration/.codex-plugin/plugin.json": (
                '{"name":"codex-orchestration","version":"' + version + '"}\n'
            ),
            "plugins/codex-orchestration/.mcp.json": '{"mcpServers":{}}\n',
            "plugins/codex-orchestration/skills/example/SKILL.md": payload + "\n",
            "CHANGELOG.md": f"# Changelog\n\n## {version} — Unreleased\n",
            "tests/plugin_lifecycle_smoke.py": f'NEW_VERSION = "{version}"\n',
            (
                "plugins/codex-orchestration/skills/codex-orchestration/scripts/"
                "configure_native_routing.py"
            ): (
                "REQUEST = {\"clientInfo\": {\"name\": \"test\", "
                f'\"version\": \"{version}\"}}}}\n'
            ),
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class ReleaseCheckTests(unittest.TestCase):
    def test_checkout_release_metadata_is_consistent(self) -> None:
        self.assertEqual(RELEASE.run_check(REPO_ROOT, require_tag=False), "0.8.4")

    def test_unreleased_checkout_is_not_tag_ready(self) -> None:
        with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not tagged"):
            RELEASE.run_check(REPO_ROOT, require_tag=True)

    def test_semver_precedence_and_build_metadata(self) -> None:
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for lower, higher in zip(ordered, ordered[1:]):
            self.assertLess(RELEASE.compare_semver(lower, higher), 0)
            self.assertGreater(RELEASE.compare_semver(higher, lower), 0)
        self.assertEqual(RELEASE.compare_semver("1.2.3+one", "1.2.3+two"), 0)

    def test_semver_rejects_leading_zeroes_and_empty_identifiers(self) -> None:
        for version in ("01.2.3", "1.02.3", "1.2.03", "1.2.3-01", "1.2.3-a..b"):
            with self.subTest(version=version):
                with self.assertRaises(RELEASE.ReleaseCheckError):
                    RELEASE.SemVer.parse(version)

    def test_payload_path_detection_covers_add_change_rename_and_delete(self) -> None:
        payload = "plugins/codex-orchestration/skills/x/SKILL.md"
        manifest = "plugins/codex-orchestration/.codex-plugin/plugin.json"
        nonpayload = "README.md"
        for change in (
            [("A", (payload,))],
            [("M", (manifest,))],
            [("R100", (nonpayload, payload))],
            [("R100", (payload, nonpayload))],
            [("D", (payload,))],
        ):
            with self.subTest(change=change):
                self.assertTrue(RELEASE.payload_changed(change))
        self.assertFalse(RELEASE.payload_changed([("M", (nonpayload,))]))

    def test_equal_version_payload_change_fails(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("1.0.0", payload="base")
        base = repo.commit("base")
        repo.write_release("1.0.0", payload="changed")
        head = repo.commit("payload without bump")
        with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not greater"):
            RELEASE.validate_release_identity(
                repo.root, base_sha=base, head_sha=head
            )

    def test_version_upgrade_passes_and_downgrade_fails(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("1.0.0", payload="base")
        base = repo.commit("base")
        repo.write_release("1.1.0", payload="upgrade")
        upgraded = repo.commit("upgrade")
        self.assertEqual(
            RELEASE.validate_release_identity(
                repo.root, base_sha=base, head_sha=upgraded
            ),
            "1.1.0",
        )
        repo.git("checkout", "-q", "-b", "downgrade", base)
        repo.write_release("0.9.0", payload="downgrade")
        downgraded = repo.commit("downgrade")
        with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not greater"):
            RELEASE.validate_release_identity(
                repo.root, base_sha=base, head_sha=downgraded
            )

    def test_local_worktree_payload_change_uses_disk_manifest(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("1.0.0", payload="base")
        base = repo.commit("base")
        repo.write_release("1.0.1", payload="uncommitted")
        self.assertEqual(
            RELEASE.validate_release_identity(repo.root, base_sha=base, head_sha=None),
            "1.0.1",
        )

    def test_nonpayload_branch_passes_when_base_advanced(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("1.0.0", payload="base")
        branch_point = repo.commit("branch point")
        default_branch = repo.git("branch", "--show-current")
        repo.git("checkout", "-q", "-b", "feature", branch_point)
        (repo.root / "README.md").write_text("feature\n", encoding="utf-8")
        feature = repo.commit("nonpayload feature")
        repo.git("checkout", "-q", default_branch)
        repo.write_release("1.1.0", payload="base advanced")
        advanced_base = repo.commit("base release")
        self.assertEqual(
            RELEASE.validate_release_identity(
                repo.root, base_sha=advanced_base, head_sha=feature
            ),
            "1.0.0",
        )

    def test_branch_point_payload_change_without_bump_fails(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("2.0.0", payload="base")
        base = repo.commit("base")
        repo.write_release("2.0.0", payload="feature payload")
        head = repo.commit("feature")
        with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not greater"):
            RELEASE.validate_release_identity(
                repo.root, base_sha=base, head_sha=head
            )

    def test_payload_add_rename_and_delete_without_bump_fail(self) -> None:
        skill = Path("plugins/codex-orchestration/skills/example/SKILL.md")
        for operation in ("add", "rename", "delete"):
            with self.subTest(operation=operation):
                repo = TempRepository()
                self.addCleanup(repo.close)
                repo.write_release("1.0.0", payload="base")
                base = repo.commit("base")
                if operation == "add":
                    added = (
                        repo.root
                        / "plugins/codex-orchestration/skills/added/SKILL.md"
                    )
                    added.parent.mkdir(parents=True)
                    added.write_text("added\n", encoding="utf-8")
                elif operation == "rename":
                    (repo.root / "plugins/codex-orchestration/skills/renamed").mkdir()
                    repo.git(
                        "mv",
                        skill.as_posix(),
                        "plugins/codex-orchestration/skills/renamed/SKILL.md",
                    )
                else:
                    repo.git("rm", skill.as_posix())
                head = repo.commit(operation)
                with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not greater"):
                    RELEASE.validate_release_identity(
                        repo.root, base_sha=base, head_sha=head
                    )

    def test_missing_or_unrelated_history_fails_closed(self) -> None:
        repo = TempRepository()
        self.addCleanup(repo.close)
        repo.write_release("1.0.0")
        base = repo.commit("base")
        with self.assertRaises(RELEASE.ReleaseCheckError):
            RELEASE.validate_release_identity(
                repo.root, base_sha="missing-ref", head_sha=base
            )
        repo.git("checkout", "-q", "--orphan", "unrelated")
        repo.write_release("2.0.0")
        unrelated = repo.commit("unrelated")
        with self.assertRaises(RELEASE.ReleaseCheckError):
            RELEASE.validate_release_identity(
                repo.root, base_sha=base, head_sha=unrelated
            )

    def test_exact_commit_mode_rejects_hex_named_moving_ref(self) -> None:
        moving_ref = "a" * 40
        resolved = "b" * 40
        with mock.patch.object(RELEASE, "_git", return_value=resolved + "\n"):
            with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "moving ref"):
                RELEASE.resolve_commit(
                    Path("."), moving_ref, label="base", require_exact=True
                )

    def test_multiple_merge_bases_fail_closed(self) -> None:
        with mock.patch.object(
            RELEASE,
            "_git",
            return_value="a" * 40 + "\n" + "b" * 40 + "\n",
        ):
            with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "exactly one"):
                RELEASE.find_merge_base(Path("."), "a" * 40, "b" * 40)

    def test_shallow_repository_fails_even_when_requested_commit_exists(self) -> None:
        source = TempRepository()
        self.addCleanup(source.close)
        source.write_release("1.0.0")
        source.commit("base")
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "clone"
            completed = subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    source.root.as_uri(),
                    str(clone),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=clone,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
            with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "shallow"):
                RELEASE.validate_release_identity(
                    clone, base_sha=head, head_sha=head
                )


if __name__ == "__main__":
    unittest.main()
