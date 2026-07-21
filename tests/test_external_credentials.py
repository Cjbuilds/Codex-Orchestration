from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/codex-orchestration/skills/codex-orchestration/scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load("external_auth_helper")
CREDENTIALS = load("external_credentials")


class ExternalCredentialTests(unittest.TestCase):
    def test_stable_helper_install_is_owned_atomic_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target, digest = CREDENTIALS.install_stable_helper(home)
            self.assertEqual(
                target,
                home / "codex-orchestration/bin/external_auth_helper.py",
            )
            self.assertEqual(len(digest), 64)
            self.assertIn(CREDENTIALS.HELPER_MARKER, target.read_bytes())
            if CREDENTIALS.os.name == "posix":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            same_target, same_digest = CREDENTIALS.install_stable_helper(home)
            self.assertEqual((same_target, same_digest), (target, digest))

    def test_helper_install_refuses_unmanaged_symlink_and_hardlink(self) -> None:
        for attack in ("unmanaged", "symlink", "hardlink"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                binary = home / "codex-orchestration/bin"
                binary.mkdir(parents=True)
                target = binary / "external_auth_helper.py"
                outside = home / "outside"
                outside.write_text("outside", encoding="utf-8")
                if attack == "unmanaged":
                    target.write_text("unmanaged", encoding="utf-8")
                elif attack == "symlink":
                    try:
                        target.symlink_to(outside)
                    except OSError:
                        if os.name == "nt":
                            continue
                        raise
                else:
                    target.hardlink_to(outside)
                with self.assertRaises(CREDENTIALS.CredentialSetupError):
                    CREDENTIALS.install_stable_helper(home)

    def test_auth_config_is_nonsecret_and_uses_stable_absolute_command(self) -> None:
        helper = (
            Path(tempfile.gettempdir()).resolve()
            / "safe/codex/home/codex-orchestration/bin/external_auth_helper.py"
        )
        config = CREDENTIALS.auth_config(helper, "openrouter", platform="linux")
        self.assertEqual(config["command"], str(helper))
        self.assertEqual(config["args"], ["get", "--provider", "openrouter"])
        self.assertNotIn("env_key", config)
        self.assertNotIn("token", repr(config).lower())

    def test_windows_uses_pinned_python_for_all_managed_helper_commands(self) -> None:
        fixture_root = Path(tempfile.gettempdir()).resolve() / "safe"
        helper = (
            fixture_root
            / "codex/home/codex-orchestration/bin/external_auth_helper.py"
        )
        interpreter = (fixture_root / "python/python.exe").resolve()
        config = CREDENTIALS.auth_config(
            helper,
            "openrouter",
            platform="win32",
            python_executable=interpreter,
        )
        expected_prefix = [str(interpreter), str(helper)]
        self.assertEqual(config["command"], expected_prefix[0])
        self.assertEqual(
            config["args"], [expected_prefix[1], "get", "--provider", "openrouter"]
        )
        self.assertEqual(
            CREDENTIALS.enrollment_command(
                helper,
                "openrouter",
                platform="win32",
                python_executable=interpreter,
            ),
            [*expected_prefix, "enroll", "--provider", "openrouter"],
        )
        completed = mock.Mock(returncode=0, stdout="configured\n", stderr="")
        with mock.patch.object(
            CREDENTIALS.subprocess, "run", return_value=completed
        ) as run:
            self.assertTrue(
                CREDENTIALS.credential_ready(
                    helper,
                    "openrouter",
                    platform="win32",
                    python_executable=interpreter,
                )
            )
        self.assertEqual(
            run.call_args.args[0],
            [*expected_prefix, "status", "--provider", "openrouter"],
        )
        with mock.patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "sentinel-auth-readiness-secret"}
        ), mock.patch.object(
            CREDENTIALS.external_cli_trust,
            "sanitized_environment",
            return_value={"PATH": "/safe/bin"},
        ), mock.patch.object(
            CREDENTIALS.subprocess, "run", return_value=completed
        ) as sanitized_run:
            self.assertTrue(
                CREDENTIALS.credential_ready(
                    helper,
                    "openrouter",
                    platform="win32",
                    python_executable=interpreter,
                )
            )
        self.assertEqual(sanitized_run.call_args.kwargs["env"], {"PATH": "/safe/bin"})
        self.assertNotIn(
            "sentinel-auth-readiness-secret",
            repr(sanitized_run.call_args.kwargs["env"]),
        )

    def test_stable_helper_verification_detects_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target, digest = CREDENTIALS.install_stable_helper(home)
            self.assertEqual(CREDENTIALS.verify_stable_helper(home), (target, digest))
            target.write_bytes(target.read_bytes() + b"\n# drift\n")
            with self.assertRaisesRegex(
                CREDENTIALS.CredentialSetupError, "drifted"
            ):
                CREDENTIALS.verify_stable_helper(home)

    def test_macos_enrollment_uses_native_prompt_and_never_an_argument(self) -> None:
        calls: list[list[str]] = []

        def capture(command: list[str]) -> None:
            calls.append(command)

        with mock.patch.object(HELPER.Path, "is_file", return_value=True), mock.patch.object(
            HELPER, "_run_interactive", side_effect=capture
        ):
            HELPER.dispatch("enroll", "openrouter", platform="darwin")
        self.assertEqual(calls[0][-1], "-w")
        self.assertNotIn("sensitive-test-value", calls[0])

    def test_status_discards_secret_and_errors_never_echo_provider_output(self) -> None:
        secret = "sensitive-test-value"
        with mock.patch.object(HELPER, "_run_capture", return_value=secret):
            stdout = io.StringIO()
            with mock.patch.object(
                HELPER.Path, "is_file", return_value=True
            ), mock.patch.object(HELPER.sys, "platform", "darwin"), mock.patch(
                "sys.stdout", stdout
            ):
                self.assertEqual(HELPER.main(["status", "--provider", "openrouter"]), 0)
            self.assertNotIn(secret, stdout.getvalue())

        completed = mock.Mock(returncode=1, stdout=secret, stderr=secret)
        with mock.patch.object(HELPER.subprocess, "run", return_value=completed):
            with self.assertRaises(HELPER.HelperError) as failure:
                HELPER._run_capture(["credential-store"])
        self.assertNotIn(secret, str(failure.exception))

    def test_invalid_provider_and_missing_linux_store_fail_closed(self) -> None:
        with self.assertRaises(HELPER.HelperError):
            HELPER.dispatch("get", "../escape", platform="darwin")
        with mock.patch.object(HELPER.shutil, "which", return_value=None):
            with self.assertRaisesRegex(HELPER.HelperError, "trusted user helper"):
                HELPER.dispatch("get", "openrouter", platform="linux")

    @unittest.skipUnless(
        sys.platform == "win32", "requires the real Windows Credential Manager"
    )
    def test_windows_credential_manager_round_trip(self) -> None:
        provider = f"codex_test_{uuid.uuid4().hex[:12]}"
        value = f"integration-test-value-{uuid.uuid4().hex}"
        enrolled = False
        try:
            with mock.patch.object(HELPER.getpass, "getpass", return_value=value):
                HELPER._windows_enroll(provider)
            enrolled = True
            self.assertEqual(HELPER._windows_get(provider), value)
        finally:
            if enrolled:
                HELPER._windows_delete(provider)


if __name__ == "__main__":
    unittest.main()
