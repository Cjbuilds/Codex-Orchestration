from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
    / "inspect_models.py"
)
SPEC = importlib.util.spec_from_file_location("inspect_models", SCRIPT_PATH)
assert SPEC and SPEC.loader
inspect_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect_models)


class InspectModelsTests(unittest.TestCase):
    def fake_binary(self) -> tempfile.NamedTemporaryFile:
        binary = tempfile.NamedTemporaryFile()
        os.chmod(binary.name, 0o700)
        return binary

    def test_catalog_reports_exact_binary_version_and_source(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-test",
                    "default_reasoning_level": "high",
                    "supported_reasoning_levels": [{"effort": "high"}],
                }
            ]
        }
        with self.fake_binary() as binary, mock.patch.object(
            inspect_models.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
                subprocess.CompletedProcess([], 0, "codex-cli 9.9.9\n", ""),
            ],
        ) as run:
            result, executable, version = inspect_models.load_catalog(
                binary.name, None, False
            )

        self.assertEqual(result, payload)
        self.assertEqual(executable, str(Path(binary.name).resolve()))
        self.assertEqual(version, "codex-cli 9.9.9")
        self.assertEqual(run.call_args_list[0].args[0][1:], ["debug", "models"])
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 30)

    def test_provider_id_is_validated_before_command_execution(self) -> None:
        with self.fake_binary() as binary, mock.patch.object(
            inspect_models.subprocess, "run"
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "Invalid provider ID"):
                inspect_models.load_catalog(binary.name, 'bad"provider', False)
        run.assert_not_called()

    def test_timeout_is_reported_without_hanging(self) -> None:
        with self.fake_binary() as binary, mock.patch.object(
            inspect_models.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("codex", 30),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                inspect_models.load_catalog(binary.name, None, False)

    def test_malformed_catalog_shapes_fail_cleanly(self) -> None:
        for payload, expected in (
            ([], "root is not an object"),
            ({"models": "wrong"}, "models array"),
            ({"models": ["wrong"]}, "invalid model entry at index 0"),
            ({"models": [{}]}, "invalid model entry at index 0"),
        ):
            with self.subTest(payload=payload):
                with self.fake_binary() as binary, mock.patch.object(
                    inspect_models.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps(payload), ""
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        inspect_models.load_catalog(binary.name, None, False)

    def test_malformed_reasoning_effort_fails_cleanly(self) -> None:
        payload = {
            "models": [
                {
                    "slug": "gpt-test",
                    "supported_reasoning_levels": [{"effort": ["high"]}],
                }
            ]
        }
        with self.fake_binary() as binary, mock.patch.object(
            inspect_models.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid reasoning level"):
                inspect_models.load_catalog(binary.name, None, False)

    def test_invalid_utf8_subprocess_output_fails_cleanly(self) -> None:
        invalid_utf8 = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        with self.fake_binary() as binary, mock.patch.object(
            inspect_models.subprocess,
            "run",
            side_effect=invalid_utf8,
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid UTF-8"):
                inspect_models.load_catalog(binary.name, None, False)

    def test_json_output_includes_catalog_provenance(self) -> None:
        for bundled, expected_source in (
            (False, "codex debug models"),
            (True, "codex debug models --bundled"),
        ):
            with self.subTest(bundled=bundled), mock.patch.object(
                inspect_models,
                "load_catalog",
                return_value=({"models": []}, "/exact/codex", "codex-cli 1.2.3"),
            ), mock.patch.object(
                inspect_models.sys,
                "argv",
                [
                    "inspect_models.py",
                    "--json",
                    *(["--bundled"] if bundled else []),
                ],
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    result = inspect_models.main()

            self.assertEqual(result, 0)
            output = json.loads(stdout.getvalue())
            self.assertEqual(output["codex_binary"], "/exact/codex")
            self.assertEqual(output["codex_version"], "codex-cli 1.2.3")
            self.assertEqual(output["catalog_source"], expected_source)


if __name__ == "__main__":
    unittest.main()
