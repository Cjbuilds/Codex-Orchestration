from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "plugins" / "codex-orchestration" / "skills" / "codex-orchestration" / "scripts" / "update_plugin.py"
SPEC = importlib.util.spec_from_file_location("update_plugin", SCRIPT)
assert SPEC and SPEC.loader
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)


class FakeCodex:
    def __init__(
        self,
        *,
        home: Path,
        git: Path,
        current: str = "0.6.0",
        candidate: str = "0.7.0",
        source_url: str = "https://github.com/Cjbuilds/Codex-Orchestration.git",
        enabled: bool = True,
    ) -> None:
        self.home = home.resolve()
        self.git = git
        self.current = current
        self.candidate = candidate
        self.source_url = source_url
        self.enabled = enabled
        self.installed = current
        self.calls: list[tuple[str, ...]] = []
        self.plugin_root = self.home / ".tmp" / "marketplaces" / "codex-orchestration" / "plugins" / "codex-orchestration"
        self.marketplace_root = self.plugin_root.parents[1]
        self._write_manifest(current)
        self._git("init", "--quiet")
        self._git("config", "user.name", "Updater Test")
        self._git("config", "user.email", "updater@example.invalid")
        self._git("remote", "add", "origin", source_url)
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "current")
        self.old_commit = self._git("rev-parse", "HEAD")
        self._write_manifest(candidate)
        (self.marketplace_root / "candidate.txt").write_text(candidate, encoding="utf-8")
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "candidate")
        self.candidate_commit = self._git("rev-parse", "HEAD")
        self._git("reset", "--hard", self.old_commit)

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            [str(self.git), *arguments],
            cwd=self.marketplace_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _write_manifest(self, version: str) -> None:
        manifest = self.plugin_root / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"name": UPDATE.PLUGIN_NAME, "version": version, "repository": UPDATE.REPOSITORY_URL}),
            encoding="utf-8",
        )

    def stage(self, *_arguments: object) -> object:
        return UPDATE.Candidate(self.candidate, self.candidate_commit)

    def entry(self) -> dict[str, object]:
        return {
            "pluginId": UPDATE.PLUGIN_ID,
            "name": UPDATE.PLUGIN_NAME,
            "marketplaceName": UPDATE.MARKETPLACE_NAME,
            "version": self.installed,
            "installed": True,
            "enabled": self.enabled,
            "source": {"source": "local", "path": str(self.plugin_root)},
            "marketplaceSource": {"sourceType": "git", "source": self.source_url},
        }

    def __call__(self, _command: object, arguments: list[str], _environment: dict[str, str]) -> object:
        command = tuple(arguments)
        self.calls.append(command)
        if command == ("plugin", "list", "--json"):
            return {"installed": [self.entry()], "available": []}
        if command == ("plugin", "marketplace", "upgrade", UPDATE.MARKETPLACE_NAME, "--json"):
            self._git("reset", "--hard", self.candidate_commit)
            return {
                "selectedMarketplaces": [UPDATE.MARKETPLACE_NAME],
                "upgradedRoots": [str(self.marketplace_root)],
                "errors": [],
            }
        if command == ("plugin", "add", UPDATE.PLUGIN_ID, "--json"):
            self.installed = UPDATE._candidate_version(self.plugin_root, UPDATE.TrustPolicy())
            return {"version": self.installed, "installedPath": "/cache/new"}
        raise AssertionError(f"unexpected Codex call: {command!r}")


class PluginUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = (Path(self.temporary.name) / "codex-home").resolve()
        self.home.mkdir()
        self.binary = Path(self.temporary.name) / "codex"
        self.binary.write_text("fake", encoding="utf-8")
        self.binary.chmod(0o755)
        found = shutil.which("git")
        assert found
        self.git = Path(found).resolve()
        self.command = (self.binary,)

    def perform(self, fake: FakeCodex, **kwargs: object) -> tuple[str, str, bool]:
        return UPDATE.perform_update(
            self.command,
            self.home,
            git=self.git,
            runner=fake,
            stager=kwargs.pop("stager", fake.stage),
            **kwargs,
        )

    def test_successful_update_is_transactional_and_preserves_sentinels(self) -> None:
        fake = FakeCodex(home=self.home, git=self.git)
        sentinels = [
            self.home / ".codex-orchestration-routing.json",
            self.home / "auth.json",
            self.home / "sessions" / "keep.jsonl",
        ]
        for index, path in enumerate(sentinels):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"sentinel-{index}", encoding="utf-8")
        result = self.perform(fake)
        self.assertEqual(result, ("0.6.0", "0.7.0", True))
        self.assertEqual(
            fake.calls,
            [
                ("plugin", "list", "--json"),
                ("plugin", "marketplace", "upgrade", UPDATE.MARKETPLACE_NAME, "--json"),
                ("plugin", "add", UPDATE.PLUGIN_ID, "--json"),
                ("plugin", "list", "--json"),
            ],
        )
        for index, path in enumerate(sentinels):
            self.assertEqual(path.read_text(encoding="utf-8"), f"sentinel-{index}")

    def test_same_version_and_downgrade_stop_before_marketplace_mutation(self) -> None:
        for index, (current, candidate, expected) in enumerate((
            ("0.6.0", "0.6.0", ("0.6.0", "0.6.0", False)),
            ("0.7.0", "0.6.0", None),
        )):
            with self.subTest(current=current, candidate=candidate):
                home = (Path(self.temporary.name) / f"version-home-{index}").resolve()
                home.mkdir()
                fake = FakeCodex(home=home, git=self.git, current=current, candidate=candidate)
                if expected is None:
                    with self.assertRaisesRegex(UPDATE.UpdateError, "downgrade"):
                        UPDATE.perform_update(self.command, home, git=self.git, runner=fake, stager=fake.stage)
                else:
                    self.assertEqual(
                        UPDATE.perform_update(self.command, home, git=self.git, runner=fake, stager=fake.stage),
                        expected,
                    )
                self.assertEqual(fake.calls, [("plugin", "list", "--json")])
                self.assertEqual(fake._git("rev-parse", "HEAD"), fake.old_commit)

    def test_disabled_plugin_is_refused_before_staging_or_mutation(self) -> None:
        fake = FakeCodex(home=self.home, git=self.git, enabled=False)
        stager = mock.Mock(side_effect=AssertionError("must not stage"))
        with self.assertRaisesRegex(UPDATE.UpdateError, "disabled"):
            self.perform(fake, stager=stager)
        stager.assert_not_called()
        self.assertEqual(fake.calls, [("plugin", "list", "--json")])

    def test_concurrent_updater_is_rejected_by_os_lock(self) -> None:
        (self.home / ".tmp").mkdir()
        with UPDATE._update_lock(self.home):
            with self.assertRaisesRegex(UPDATE.UpdateError, "already running"):
                with UPDATE._update_lock(self.home):
                    self.fail("nested update lock unexpectedly succeeded")

    def test_post_install_drift_rolls_back_snapshot_and_prior_install(self) -> None:
        fake = FakeCodex(home=self.home, git=self.git)
        original = fake.__call__
        add_count = 0

        def drift(command: object, arguments: list[str], environment: dict[str, str]) -> object:
            nonlocal add_count
            result = original(command, arguments, environment)
            if tuple(arguments) == ("plugin", "add", UPDATE.PLUGIN_ID, "--json"):
                add_count += 1
                if add_count == 1:
                    fake.enabled = False
                else:
                    fake.enabled = True
            return result

        with self.assertRaisesRegex(UPDATE.UpdateError, "verification"):
            UPDATE.perform_update(
                self.command,
                self.home,
                git=self.git,
                runner=drift,
                stager=fake.stage,
            )
        self.assertEqual(fake._git("rev-parse", "HEAD"), fake.old_commit)
        self.assertEqual(fake.installed, fake.current)
        self.assertTrue(fake.enabled)
        self.assertEqual(add_count, 2)

    def test_untrusted_marketplace_fails_before_staging(self) -> None:
        for index, (source_type, source) in enumerate((
            ("git", "https://github.com/attacker/Codex-Orchestration.git"),
            ("local", str(self.home / "checkout")),
            ("git", "https://github.com/Cjbuilds/Codex-Orchestration.git?ref=evil"),
        )):
            with self.subTest(source_type=source_type, source=source):
                home = (Path(self.temporary.name) / f"untrusted-home-{index}").resolve()
                home.mkdir()
                fake = FakeCodex(home=home, git=self.git, source_url=source)
                original_entry = fake.entry

                def entry() -> dict[str, object]:
                    value = original_entry()
                    value["marketplaceSource"] = {"sourceType": source_type, "source": source}
                    return value

                fake.entry = entry  # type: ignore[method-assign]
                with self.assertRaises(UPDATE.UpdateError):
                    UPDATE.perform_update(
                        self.command,
                        home,
                        git=self.git,
                        runner=fake,
                        stager=mock.Mock(),
                    )
                self.assertEqual(fake.calls, [("plugin", "list", "--json")])

    def test_source_and_manifest_parent_symlinks_are_rejected(self) -> None:
        fake = FakeCodex(home=self.home, git=self.git)
        plugins = fake.plugin_root.parent
        real_plugins = plugins.with_name("real-plugins")
        plugins.rename(real_plugins)
        plugins.symlink_to(real_plugins, target_is_directory=True)
        with self.assertRaisesRegex(UPDATE.UpdateError, "symlink|unsafe|outside"):
            self.perform(fake)

        second_home = (Path(self.temporary.name) / "second-home").resolve()
        second_home.mkdir()
        fake = FakeCodex(home=second_home, git=self.git)
        manifest_dir = fake.plugin_root / ".codex-plugin"
        external = Path(self.temporary.name) / "external-manifest"
        manifest_dir.rename(external)
        manifest_dir.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(UPDATE.UpdateError, "symlink|reparse|unsafe"):
            UPDATE._candidate_version(fake.plugin_root, UPDATE.TrustPolicy())

    def test_minimal_environment_drops_host_path_proxy_credentials_and_codex_overrides(self) -> None:
        injected = {
            "PATH": "/tmp/hostile",
            "HTTPS_PROXY": "http://attacker.invalid",
            "OPENROUTER_API_KEY": "secret",
            "KUBECONFIG": "/tmp/kube",
            "CODEX_MANAGED_PACKAGE_ROOT": "/tmp/override",
            "NODE_OPTIONS": "--require=/tmp/inject.js",
            "GIT_CONFIG_COUNT": "99",
        }
        isolated = Path(self.temporary.name) / "isolated"
        isolated.mkdir()
        with mock.patch.dict(os.environ, injected, clear=True):
            environment = UPDATE._safe_environment(self.home, self.command, self.git, isolated)
        self.assertEqual(environment["CODEX_HOME"], str(self.home))
        self.assertEqual(environment["HOME"], str(isolated))
        self.assertNotIn("/tmp/hostile", environment["PATH"])
        for key in injected.keys() - {"GIT_CONFIG_COUNT", "PATH"}:
            self.assertNotIn(key, environment)
        self.assertEqual(environment["GIT_CONFIG_COUNT"], "4")

    def test_bounded_process_rejects_excessive_output(self) -> None:
        environment = {"PATH": UPDATE._trusted_search_path(), "LANG": "C.UTF-8"}
        with self.assertRaisesRegex(UPDATE.UpdateError, "excessive output"):
            UPDATE._execute(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1100000)"],
                environment=environment,
                cwd=self.home,
                label="test command",
            )

    def test_timeout_terminates_the_complete_process_group(self) -> None:
        marker = Path(self.temporary.name) / "descendant-survived"
        child = (
            "import pathlib,time;time.sleep(0.8);"
            f"pathlib.Path({str(marker)!r}).write_text('unsafe')"
        )
        parent = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
            "time.sleep(10)"
        )
        environment = {"PATH": UPDATE._trusted_search_path(), "LANG": "C.UTF-8"}
        with (
            mock.patch.object(UPDATE, "COMMAND_TIMEOUT_SECONDS", 0.15),
            self.assertRaisesRegex(UPDATE.UpdateError, "process tree was terminated"),
        ):
            UPDATE._execute(
                [sys.executable, "-c", parent],
                environment=environment,
                cwd=self.home,
                label="timeout test command",
            )
        time.sleep(1)
        self.assertFalse(marker.exists())

    def test_env_shebang_is_resolved_to_an_absolute_interpreter(self) -> None:
        script = Path(self.temporary.name) / "codex-script"
        script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        script.chmod(0o755)
        interpreter = Path(self.temporary.name) / "node"
        interpreter.write_text("", encoding="utf-8")
        interpreter.chmod(0o755)
        with mock.patch.object(UPDATE, "resolve_helper", return_value=interpreter) as helper:
            command = UPDATE.resolve_command(script.resolve())
        self.assertEqual(command, (interpreter, script.resolve()))
        helper.assert_called_once_with("node", extra=(script.resolve().parent,))

    def test_semver_handles_prereleases_and_rejects_ambiguous_versions(self) -> None:
        self.assertLess(UPDATE.SemVer.parse("0.7.0-rc.1").compare(UPDATE.SemVer.parse("0.7.0")), 0)
        self.assertGreater(UPDATE.SemVer.parse("0.7.0-beta.11").compare(UPDATE.SemVer.parse("0.7.0-beta.2")), 0)
        for value in ("0.7", "00.7.0", "0.7.0-01", "0.7.0-a..b", True):
            with self.subTest(value=value), self.assertRaises(UPDATE.UpdateError):
                UPDATE.SemVer.parse(value)


if __name__ == "__main__":
    unittest.main()
