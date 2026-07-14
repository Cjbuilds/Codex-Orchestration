from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
    / "fable_advisor_mcp.py"
)
SPEC = importlib.util.spec_from_file_location("fable_advisor_mcp", SCRIPT)
assert SPEC and SPEC.loader
FABLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FABLE)


class FableAdvisorMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / FABLE.STATE_FILENAME).write_text(
            json.dumps(
                {
                    "advisor": {
                        "kind": "fable",
                        "model": "claude-fable-5",
                        "effort": "high",
                        "server": "fable-advisor-python3",
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def completed(command: list[str], stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def test_review_is_pinned_sanitized_read_only_and_runtime_confirmed(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[-2:] == ["auth", "status"]:
                return self.completed(
                    command,
                    json.dumps(
                        {
                            "loggedIn": True,
                            "authMethod": "claude.ai",
                            "apiProvider": "firstParty",
                            "subscriptionType": "max",
                        }
                    ),
                )
            return self.completed(
                command,
                json.dumps(
                    {
                        "result": "PLAN_APPROVED\nNo material gap found.",
                        "modelUsage": {"claude-fable-5": {"outputTokens": 12}},
                    }
                ),
            )

        env = {
            "CODEX_HOME": str(self.home),
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(FABLE, "resolve_claude", return_value=Path("/fake/claude")),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = FABLE.review_plan("Review this complete plan.")

        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["model"], "claude-fable-5")
        self.assertEqual(result["used_models"], ["claude-fable-5"])
        self.assertNotIn("subscription_type", result)
        review_command, review_kwargs = calls[1]
        self.assertIn("--safe-mode", review_command)
        self.assertNotIn("--bare", review_command)
        self.assertEqual(review_command[review_command.index("--model") + 1], "claude-fable-5")
        self.assertEqual(review_command[review_command.index("--effort") + 1], "high")
        self.assertEqual(
            review_command[review_command.index("--prompt-suggestions") + 1], "false"
        )
        self.assertEqual(review_kwargs["input"], "Review this complete plan.")
        sanitized = review_kwargs["env"]
        self.assertIsInstance(sanitized, dict)
        for name in FABLE.SENSITIVE_ENV:
            self.assertNotIn(name, sanitized)

    def test_invalid_decision_or_unconfirmed_model_fails_closed(self) -> None:
        auth = self.completed(
            ["claude", "auth", "status"],
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": "pro",
                }
            ),
        )
        malformed = self.completed(
            ["claude"],
            json.dumps(
                {
                    "result": "Looks good.",
                    "modelUsage": {"claude-fable-5": {}},
                }
            ),
        )
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(FABLE, "resolve_claude", return_value=Path("/fake/claude")),
            mock.patch.object(FABLE.subprocess, "run", side_effect=[auth, malformed]),
        ):
            with self.assertRaisesRegex(FABLE.AdvisorError, "required plan decision"):
                FABLE.review_plan("packet")

        unconfirmed = self.completed(
            ["claude"],
            json.dumps(
                {
                    "result": "PLAN_REVISE\nFix the verification step.",
                    "modelUsage": {"claude-haiku-4-5": {}},
                }
            ),
        )
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(FABLE, "resolve_claude", return_value=Path("/fake/claude")),
            mock.patch.object(FABLE.subprocess, "run", side_effect=[auth, unconfirmed]),
        ):
            with self.assertRaisesRegex(FABLE.AdvisorError, "did not confirm"):
                FABLE.review_plan("packet")

    def test_mcp_surface_exposes_only_bounded_tools(self) -> None:
        initialized = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "codex-orchestration-fable-advisor")
        listed = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tools = listed["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["review_plan", "status"])
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])
        review_schema = tools[0]["inputSchema"]
        self.assertEqual(review_schema["required"], ["packet"])
        self.assertFalse(review_schema["additionalProperties"])

    def test_status_does_not_expose_account_plan_metadata(self) -> None:
        with (
            mock.patch.object(FABLE, "load_fable_route", return_value={"model": "claude-fable-5", "effort": "high"}),
            mock.patch.object(
                FABLE,
                "check_claude_auth",
                return_value={"auth_method": "claude.ai", "api_provider": "firstParty"},
            ),
        ):
            response = FABLE.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "status", "arguments": {}},
                }
            )
        text = response["result"]["content"][0]["text"]
        self.assertNotIn("subscription", text.lower())
        self.assertNotIn("account_plan", text)

    def test_saved_xhigh_and_legacy_max_efforts_remain_valid(self) -> None:
        state_path = self.home / FABLE.STATE_FILENAME
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                state_path.write_text(
                    json.dumps(
                        {
                            "advisor": {
                                "kind": "fable",
                                "model": "claude-fable-5",
                                "effort": effort,
                                "server": "fable-advisor-python3",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(FABLE.load_fable_route(self.home)["effort"], effort)


if __name__ == "__main__":
    unittest.main()
