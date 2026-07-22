from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_hooks.py"
SPEC = importlib.util.spec_from_file_location("install_hooks", SCRIPT)
assert SPEC and SPEC.loader
INSTALL_HOOKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL_HOOKS)


class InstallHooksTests(unittest.TestCase):
    @staticmethod
    def _isolated_environment(root: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        environment.update(
            {
                "HOME": str(root),
                "GIT_CONFIG_GLOBAL": str(root / "global.gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        return environment

    def setUp(self) -> None:
        self._git_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._git_home.cleanup)
        root = Path(self._git_home.name)
        self._environment = mock.patch.dict(
            os.environ,
            self._isolated_environment(root),
            clear=True,
        )
        self._environment.start()
        self.addCleanup(self._environment.stop)

    def test_isolated_environment_strips_repository_git_sentinels(self) -> None:
        root = Path(self._git_home.name)
        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": "sentinel-repository",
                "GIT_WORK_TREE": "sentinel-worktree",
                "GIT_INDEX_FILE": "sentinel-index",
                "PATH": os.environ["PATH"],
            },
            clear=False,
        ):
            isolated = self._isolated_environment(root)
        self.assertNotIn("GIT_DIR", isolated)
        self.assertNotIn("GIT_WORK_TREE", isolated)
        self.assertNotIn("GIT_INDEX_FILE", isolated)
        self.assertEqual(isolated["PATH"], os.environ["PATH"])
        self.assertEqual(isolated["GIT_CONFIG_GLOBAL"], str(root / "global.gitconfig"))

    def make_repository(self, directory: Path) -> None:
        result = subprocess.run(
            ["git", "init", "--quiet", str(directory)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def local_hooks_path(self, repository: Path) -> str | None:
        result = subprocess.run(
            ["git", "config", "--local", "--get", "core.hooksPath"],
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.stdout.strip() if result.returncode == 0 else None

    def test_preview_does_not_change_repository_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)

            message = INSTALL_HOOKS.configure_hooks(repository, apply=False)

            self.assertIn("Preview", message)
            self.assertIsNone(self.local_hooks_path(repository))

    def test_apply_sets_only_repository_local_hooks_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)

            message = INSTALL_HOOKS.configure_hooks(repository, apply=True)

            self.assertIn("Set repository-local", message)
            self.assertEqual(self.local_hooks_path(repository), ".githooks")

    def test_hostile_git_routing_cannot_redirect_the_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            intended = root / "intended"
            decoy = root / "decoy"
            intended.mkdir()
            decoy.mkdir()
            self.make_repository(intended)
            self.make_repository(decoy)
            subprocess.run(
                ["git", "config", "--local", "sentinel.unchanged", "yes"],
                cwd=decoy,
                timeout=10,
                check=True,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(decoy / ".git"),
                    "GIT_WORK_TREE": str(decoy),
                    "GIT_INDEX_FILE": str(decoy / ".git" / "hostile-index"),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.hooksPath",
                    "GIT_CONFIG_VALUE_0": "hostile-hooks",
                },
                clear=False,
            ):
                INSTALL_HOOKS.configure_hooks(intended, apply=True)

            self.assertEqual(self.local_hooks_path(intended), ".githooks")
            self.assertIsNone(self.local_hooks_path(decoy))
            sentinel = subprocess.run(
                ["git", "config", "--local", "--get", "sentinel.unchanged"],
                cwd=decoy,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(sentinel.stdout.strip(), "yes")

    def test_already_configured_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)
            INSTALL_HOOKS.configure_hooks(repository, apply=True)

            message = INSTALL_HOOKS.configure_hooks(repository, apply=True)

            self.assertIn("already uses", message)
            self.assertEqual(self.local_hooks_path(repository), ".githooks")

    def test_different_existing_hooks_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)
            subprocess.run(
                ["git", "config", "--local", "core.hooksPath", "custom-hooks"],
                cwd=repository,
                timeout=10,
                check=True,
            )

            with self.assertRaisesRegex(INSTALL_HOOKS.HookInstallError, "refusing"):
                INSTALL_HOOKS.configure_hooks(repository, apply=True)
            self.assertEqual(self.local_hooks_path(repository), "custom-hooks")

    def test_different_effective_global_hooks_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            self.make_repository(repository)
            environment = {
                # Git before 2.32 ignores GIT_CONFIG_GLOBAL. HOME keeps this
                # destructive-looking global-config probe inside the fixture.
                "HOME": str(root),
                "GIT_CONFIG_GLOBAL": str(root / "global.gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            with mock.patch.dict(os.environ, environment):
                subprocess.run(
                    ["git", "config", "--global", "core.hooksPath", "global-hooks"],
                    timeout=10,
                    check=True,
                )
                with self.assertRaisesRegex(
                    INSTALL_HOOKS.HookInstallError, "effective core.hooksPath"
                ):
                    INSTALL_HOOKS.configure_hooks(repository, apply=True)
            self.assertIsNone(self.local_hooks_path(repository))

    def test_worktree_override_cannot_hide_local_githooks_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)
            subprocess.run(
                ["git", "config", "extensions.worktreeConfig", "true"],
                cwd=repository,
                timeout=10,
                check=True,
            )
            subprocess.run(
                ["git", "config", "--local", "core.hooksPath", ".githooks"],
                cwd=repository,
                timeout=10,
                check=True,
            )
            subprocess.run(
                ["git", "config", "--worktree", "core.hooksPath", "custom-hooks"],
                cwd=repository,
                timeout=10,
                check=True,
            )
            with self.assertRaisesRegex(
                INSTALL_HOOKS.HookInstallError, "mismatched core.hooksPath"
            ):
                INSTALL_HOOKS.configure_hooks(repository, apply=True)

    def test_post_write_verification_errors_roll_back_hooks(self) -> None:
        verification_commands = (
            ("config", "--local", "--get", "core.hooksPath"),
            ("config", "--get", "core.hooksPath"),
        )
        for failing_command in verification_commands:
            with self.subTest(command=failing_command), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                self.make_repository(repository)
                original_git = INSTALL_HOOKS._git
                seen = 0

                def fail_second_call(
                    arguments: list[str], cwd: Path
                ) -> subprocess.CompletedProcess[str]:
                    nonlocal seen
                    if tuple(arguments) == failing_command:
                        seen += 1
                        if seen == 2:
                            raise INSTALL_HOOKS.HookInstallError(
                                "simulated verification timeout"
                            )
                    return original_git(arguments, cwd)

                with mock.patch.object(
                    INSTALL_HOOKS, "_git", side_effect=fail_second_call
                ):
                    with self.assertRaisesRegex(
                        INSTALL_HOOKS.HookInstallError,
                        "simulated verification timeout",
                    ):
                        INSTALL_HOOKS.configure_hooks(repository, apply=True)
                self.assertIsNone(self.local_hooks_path(repository))

    def test_rollback_removes_multiple_hook_values_with_unset_all(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.make_repository(repository)
            for value in (".githooks", "unexpected-hooks"):
                subprocess.run(
                    ["git", "config", "--local", "--add", "core.hooksPath", value],
                    cwd=repository,
                    timeout=10,
                    check=True,
                )

            failure = INSTALL_HOOKS.HookInstallError("verification failed")
            with self.assertRaisesRegex(
                INSTALL_HOOKS.HookInstallError, "verification failed"
            ):
                INSTALL_HOOKS._rollback_after_failed_verification(
                    repository, failure
                )
            self.assertIsNone(self.local_hooks_path(repository))

    def test_non_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                INSTALL_HOOKS.HookInstallError, "could not locate repository"
            ):
                INSTALL_HOOKS.configure_hooks(Path(temporary), apply=False)

    def test_git_execution_failure_is_reported(self) -> None:
        with mock.patch.object(
            INSTALL_HOOKS.subprocess,
            "run",
            side_effect=OSError("git unavailable"),
        ) as run:
            with self.assertRaisesRegex(
                INSTALL_HOOKS.HookInstallError, "could not run git"
            ):
                INSTALL_HOOKS.configure_hooks(Path.cwd(), apply=False)

        arguments, keywords = run.call_args
        self.assertEqual(arguments[0], ["git", "rev-parse", "--show-toplevel"])
        self.assertEqual(keywords["timeout"], 10)
        self.assertNotIn("shell", keywords)
        self.assertIn("PATH", keywords["env"])
        self.assertNotIn("GIT_DIR", keywords["env"])


if __name__ == "__main__":
    unittest.main()
