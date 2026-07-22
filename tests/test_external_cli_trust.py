from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/codex-orchestration/skills/codex-orchestration/scripts/external_cli_trust.py"
SPEC = importlib.util.spec_from_file_location("external_cli_trust", SCRIPT)
assert SPEC and SPEC.loader
TRUST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRUST)


class ExternalCliTrustTests(unittest.TestCase):
    def executable(self, root: Path, version: str = "Official CLI 1.2.3") -> Path:
        if os.name == "nt":
            return Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        path = root / "official-cli"
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
        path.chmod(0o700)
        return path

    def test_attest_and_verify_pin_path_bytes_publisher_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.executable(Path(directory))
            record = TRUST.attest(path, publisher="Example, Inc.")
            self.assertEqual(record["path"], str(path.resolve()))
            self.assertTrue(record["fingerprint"].startswith("sha256:"))
            self.assertEqual(record["publisher"], "Example, Inc.")
            expected_version = (
                f"Python {platform.python_version()}"
                if os.name == "nt"
                else "Official CLI 1.2.3"
            )
            self.assertEqual(record["version"], expected_version)
            self.assertEqual(TRUST.verify(record), path.resolve())

    def test_changed_bytes_or_version_fail_and_require_retrust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.executable(root)
            if os.name == "nt":
                path = root / "changed-cli.exe"
                shutil.copy2(source, path)
                target, digest = TRUST.fingerprint(path)
                record = {
                    "path": str(target),
                    "strategy": "sha256",
                    "fingerprint": f"sha256:{digest}",
                    "publisher": "Example, Inc.",
                    "version": f"Python {platform.python_version()}",
                }
            else:
                path = source
                record = TRUST.attest(path, publisher="Example, Inc.")
            with path.open("r+b", buffering=0) as handle:
                original = handle.read(1)
                self.assertEqual(len(original), 1)
                handle.seek(0)
                handle.write(bytes((original[0] ^ 1,)))
                handle.flush()
                os.fsync(handle.fileno())
            _, changed_digest = TRUST.fingerprint(path)
            self.assertNotEqual(
                f"sha256:{changed_digest}", record["fingerprint"]
            )
            with self.assertRaisesRegex(TRUST.CliTrustError, "CLI_CHANGED"):
                TRUST.verify(record)

    def test_fingerprint_requests_binary_descriptor_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.executable(Path(directory))
            real_open = os.open
            synthetic_binary_flag = 1 << 29
            observed_flags: list[int] = []

            def tracked_open(
                candidate: object,
                flags: int,
                mode: int = 0o777,
                **kwargs: object,
            ) -> int:
                observed_flags.append(flags)
                return real_open(
                    candidate,
                    flags & ~synthetic_binary_flag,
                    mode,
                    **kwargs,
                )

            with (
                mock.patch.object(
                    TRUST.os,
                    "O_BINARY",
                    synthetic_binary_flag,
                    create=True,
                ),
                mock.patch.object(TRUST.os, "open", side_effect=tracked_open),
            ):
                TRUST.fingerprint(path)

            self.assertEqual(len(observed_flags), 1)
            self.assertTrue(observed_flags[0] & synthetic_binary_flag)

    def test_hardlinked_nonexecutable_and_missing_targets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.executable(root)
            if os.name == "nt":
                copied = root / "official-cli.exe"
                shutil.copy2(path, copied)
                path = copied
            (root / "alias").hardlink_to(path)
            with self.assertRaises(TRUST.CliTrustError):
                TRUST.fingerprint(path)
            path.unlink()
            plain = root / "plain"
            plain.write_text("data", encoding="utf-8")
            with self.assertRaises(TRUST.CliTrustError):
                TRUST.fingerprint(plain)
            with self.assertRaises(TRUST.CliTrustError):
                TRUST.fingerprint(root / "missing")

    def test_symlink_is_resolved_once_and_record_pins_real_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.executable(root)
            alias = root / "official"
            try:
                alias.symlink_to(path)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            record = TRUST.attest(alias, publisher="Example, Inc.")
            self.assertEqual(record["path"], str(path.resolve()))

    @unittest.skipUnless(os.name == "nt", "Windows executable suffix policy")
    def test_windows_rejects_script_cli_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in (".bat", ".cmd", ".py"):
                with self.subTest(suffix=suffix):
                    path = root / f"untrusted{suffix}"
                    path.write_text("untrusted", encoding="utf-8")
                    with self.assertRaisesRegex(
                        TRUST.CliTrustError, "native executable"
                    ):
                        TRUST.fingerprint(path)

    def test_version_errors_withhold_output_and_sensitive_env_is_removed(self) -> None:
        secret = "TOP-SECRET-PROVIDER-OUTPUT"
        completed = mock.Mock(returncode=1, stdout=secret, stderr=secret)
        with mock.patch.dict(
            os.environ,
            {
                "PATH": "/safe/bin",
                "OPENROUTER_API_KEY": secret,
                "ANTHROPIC_AUTH_TOKEN": secret,
                "AWS_SECRET_ACCESS_KEY": secret,
                "GITHUB_TOKEN": secret,
                "DATABASE_PASSWORD": secret,
                "SIGNING_PASSPHRASE": secret,
                "API_KEY": secret,
                "TOKEN": secret,
                "SECRET": secret,
                "PASSWORD": secret,
                "KEEP_ME": "yes",
            },
            clear=True,
        ), mock.patch.object(
            TRUST.subprocess, "run", return_value=completed
        ) as run:
            with self.assertRaises(TRUST.CliTrustError) as failure:
                TRUST.version(Path("/safe/cli"))
            environment = TRUST.sanitized_environment()
        self.assertNotIn(secret, str(failure.exception))
        self.assertEqual(environment, {"PATH": "/safe/bin", "KEEP_ME": "yes"})
        self.assertEqual(run.call_args.kwargs["env"], environment)


if __name__ == "__main__":
    unittest.main()
