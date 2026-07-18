from __future__ import annotations

import importlib.util
import os
from pathlib import Path
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
            self.assertEqual(record["version"], "Official CLI 1.2.3")
            self.assertEqual(TRUST.verify(record), path.resolve())

    def test_changed_bytes_or_version_fail_and_require_retrust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.executable(Path(directory))
            record = TRUST.attest(path, publisher="Example, Inc.")
            path.write_text("#!/bin/sh\nprintf 'changed\\n'\n", encoding="utf-8")
            path.chmod(0o700)
            with self.assertRaisesRegex(TRUST.CliTrustError, "CLI_CHANGED"):
                TRUST.verify(record)

    def test_hardlinked_nonexecutable_and_missing_targets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.executable(root)
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
            alias.symlink_to(path)
            record = TRUST.attest(alias, publisher="Example, Inc.")
            self.assertEqual(record["path"], str(path.resolve()))

    def test_version_errors_withhold_output_and_sensitive_env_is_removed(self) -> None:
        secret = "TOP-SECRET-PROVIDER-OUTPUT"
        completed = mock.Mock(returncode=1, stdout=secret, stderr=secret)
        with mock.patch.object(TRUST.subprocess, "run", return_value=completed):
            with self.assertRaises(TRUST.CliTrustError) as failure:
                TRUST.version(Path("/safe/cli"))
        self.assertNotIn(secret, str(failure.exception))
        with mock.patch.dict(
            os.environ,
            {
                "PATH": "/safe/bin",
                "OPENROUTER_API_KEY": secret,
                "ANTHROPIC_AUTH_TOKEN": secret,
                "KEEP_ME": "yes",
            },
            clear=True,
        ):
            environment = TRUST.sanitized_environment()
        self.assertEqual(environment, {"PATH": "/safe/bin", "KEEP_ME": "yes"})


if __name__ == "__main__":
    unittest.main()
