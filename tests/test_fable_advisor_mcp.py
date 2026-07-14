from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
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
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("fable_advisor_mcp", SCRIPT)
assert SPEC and SPEC.loader
FABLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FABLE)


class FableAdvisorMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.write_state(advisor=self.route("high"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def route(effort: str = "high") -> dict[str, str]:
        return {
            "kind": "fable",
            "model": "claude-fable-5",
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    def write_state(self, *, schema: int = 3, **seats: object) -> None:
        fable_routes = [
            route
            for route in seats.values()
            if isinstance(route, dict) and route.get("kind") == "fable"
        ]
        managed_mcp = {
            route["server"]: True
            for route in fable_routes[:1]
            if isinstance(route.get("server"), str)
        }
        previous_mcp = {
            server: {"known": True, "present": False}
            for server in managed_mcp
        }
        payload = {
            "schema": schema,
            "policy_version": schema,
            "managed_by": "codex-orchestration",
            "config_file": str(self.home / "config.toml"),
            "executor": {
                "kind": "model",
                "model": "gpt-5.6-luna",
                "effort": "xhigh",
            },
            "advisor": None,
            "managed": {
                "mode": f"{FABLE.MANAGED_MARKER}\nmode",
                "usage": f"{FABLE.MANAGED_MARKER}\nusage",
                "metadata": False,
                "namespace": "agents",
                "mcp": managed_mcp,
            },
            "previous": {
                "mode": {"known": True, "present": False},
                "usage": {"known": True, "present": False},
                "metadata": {"known": True, "present": False},
                "namespace": {"known": True, "present": False},
                "mcp": previous_mcp,
            },
            "scalar_origin": None,
            "managed_feature": None,
            **seats,
        }
        if schema == 3 and "planner" not in payload:
            payload["planner"] = None
        (self.home / FABLE.STATE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    @staticmethod
    def completed(
        command: list[str], stdout: str, *, returncode: int = 0, stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def auth_result(self) -> subprocess.CompletedProcess[str]:
        return self.completed(
            ["claude", "auth", "status"],
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": "max",
                }
            ),
        )

    def model_result(
        self, response: str, *, model_usage: dict[str, object] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self.completed(
            ["claude"],
            json.dumps(
                {
                    "result": response,
                    "modelUsage": model_usage
                    if model_usage is not None
                    else {"claude-fable-5": {"outputTokens": 12}},
                }
            ),
        )

    def invoke_with_results(
        self,
        function: object,
        *args: str,
        model_response: str,
        model_usage: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], list[tuple[list[str], dict[str, object]]]]:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[-2:] == ["auth", "status"]:
                return self.auth_result()
            return self.model_result(model_response, model_usage=model_usage)

        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = function(*args)
        return result, calls

    def test_review_is_pinned_sanitized_read_only_and_runtime_confirmed(self) -> None:
        env = {
            "CODEX_HOME": str(self.home),
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[-2:] == ["auth", "status"]:
                return self.auth_result()
            return self.model_result("PLAN_APPROVED\nNo material gap found.")

        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = FABLE.review_plan("Review this complete plan.")

        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["model"], "claude-fable-5")
        self.assertEqual(result["used_models"], ["claude-fable-5"])
        self.assertNotIn("subscription_type", result)
        auth_command, auth_kwargs = calls[0]
        self.assertEqual(auth_command[-2:], ["auth", "status"])
        review_command, review_kwargs = calls[1]
        for flag in (
            "--safe-mode",
            "--tools",
            "--permission-mode",
            "--no-session-persistence",
            "--prompt-suggestions",
            "--output-format",
            "--system-prompt",
        ):
            self.assertIn(flag, review_command)
        self.assertNotIn("--bare", review_command)
        self.assertEqual(review_command[review_command.index("--tools") + 1], "")
        self.assertEqual(
            review_command[review_command.index("--permission-mode") + 1], "dontAsk"
        )
        self.assertEqual(
            review_command[review_command.index("--model") + 1], "claude-fable-5"
        )
        self.assertEqual(review_command[review_command.index("--effort") + 1], "high")
        self.assertEqual(
            review_command[review_command.index("--prompt-suggestions") + 1],
            "false",
        )
        self.assertEqual(
            review_command[review_command.index("--output-format") + 1], "json"
        )
        self.assertEqual(review_kwargs["input"], "Review this complete plan.")
        for kwargs in (auth_kwargs, review_kwargs):
            sanitized = kwargs["env"]
            self.assertIsInstance(sanitized, dict)
            for name in FABLE.SENSITIVE_ENV:
                self.assertNotIn(name, sanitized)

    def test_runtime_model_policy_accepts_only_fable_and_exact_allowed_helper(
        self,
    ) -> None:
        allowed_scenarios = (
            ({FABLE.FABLE_MODEL: {"outputTokens": 12}}, [FABLE.FABLE_MODEL]),
            (
                {
                    FABLE.FABLE_MODEL: {"outputTokens": 12},
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
                sorted((FABLE.FABLE_MODEL, FABLE.FABLE_HELPER_MODEL)),
            ),
        )
        for model_usage, expected_models in allowed_scenarios:
            with self.subTest(model_usage=model_usage):
                result, _ = self.invoke_with_results(
                    FABLE.review_plan,
                    "packet",
                    model_response="PLAN_APPROVED\nNo material gap found.",
                    model_usage=model_usage,
                )
                self.assertEqual(result["decision"], "PLAN_APPROVED")
                self.assertEqual(result["model"], FABLE.FABLE_MODEL)
                self.assertEqual(result["used_models"], expected_models)

        secret = "TOP-SECRET-MODEL-OUTPUT"
        rejected_scenarios = (
            (
                {
                    FABLE.FABLE_MODEL: {"outputTokens": 12},
                    "claude-haiku-4-5-20251002": {"outputTokens": 1},
                },
                "outside the allowed Fable runtime policy",
            ),
            (
                {FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1}},
                "did not confirm the pinned Claude Fable 5 primary model",
            ),
        )
        for model_usage, expected_error in rejected_scenarios:
            with self.subTest(model_usage=model_usage):
                with self.assertRaisesRegex(
                    FABLE.AdvisorError, expected_error
                ) as failure:
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response=f"PLAN_APPROVED\n{secret}",
                        model_usage=model_usage,
                    )
                self.assertNotIn(secret, str(failure.exception))

    def test_each_operation_pins_its_authorized_seat_effort(self) -> None:
        self.write_state(planner=self.route("low"))
        created, create_calls = self.invoke_with_results(
            FABLE.create_plan, "packet", model_response="PLAN_DRAFT\nDraft"
        )
        self.write_state(advisor=self.route("xhigh"))
        reviewed, review_calls = self.invoke_with_results(
            FABLE.review_plan, "packet", model_response="PLAN_APPROVED\nGood"
        )
        self.assertEqual(created["effort"], "low")
        self.assertEqual(reviewed["effort"], "xhigh")
        create_command = create_calls[1][0]
        review_command = review_calls[1][0]
        self.assertEqual(create_command[create_command.index("--effort") + 1], "low")
        self.assertEqual(
            review_command[review_command.index("--effort") + 1], "xhigh"
        )
        self.assertEqual(
            create_command[create_command.index("--system-prompt") + 1],
            FABLE.PLANNER_CREATE_SYSTEM_PROMPT,
        )
        self.assertEqual(
            review_command[review_command.index("--system-prompt") + 1],
            FABLE.ADVISOR_SYSTEM_PROMPT,
        )

    def test_seat_authorization_does_not_cross_planner_and_advisor(self) -> None:
        self.write_state(planner=self.route())
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured advisor"):
                FABLE.review_plan("packet")

        self.write_state(advisor=self.route())
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured planner"):
                FABLE.create_plan("packet")
            with self.assertRaisesRegex(FABLE.AdvisorError, "configured planner"):
                FABLE.revise_plan("task", "v1 plan", "F-1", "history")

    def test_route_validation_is_constrained_and_backward_compatible(self) -> None:
        self.assertEqual(FABLE.load_fable_route(self.home)["effort"], "high")
        with self.assertRaisesRegex(FABLE.AdvisorError, "planner.*advisor"):
            FABLE.load_fable_route(self.home, seat="executor")

        invalid = self.route()
        invalid["server"] = "unmanaged-server"
        self.write_state(advisor=invalid)
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)

        self.write_state(planner=self.route(), advisor=self.route("xhigh"))
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="planner")
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="advisor")

        self.write_state(schema=2, advisor=self.route())
        self.assertEqual(FABLE.load_fable_route(self.home)["effort"], "high")
        self.write_state(schema=2, planner=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="planner")

        self.write_state(schema=4, advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)

    def test_authorization_state_tampering_fails_before_any_subprocess(self) -> None:
        mutations = {
            "policy version": lambda payload: payload.update(policy_version=2),
            "other Codex home": lambda payload: payload.update(
                config_file=str(self.home / "other" / "config.toml")
            ),
            "wrong namespace": lambda payload: payload["managed"].update(
                namespace="collaboration"
            ),
            "unmarked policy": lambda payload: payload["managed"].update(
                mode="unmarked mode"
            ),
            "disabled launcher": lambda payload: payload["managed"]["mcp"].update(
                {"fable-advisor-python3": False}
            ),
        }
        state_path = self.home / FABLE.STATE_FILENAME
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.write_state(planner=self.route())
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                mutate(payload)
                state_path.write_text(json.dumps(payload), encoding="utf-8")
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(FABLE.subprocess, "run") as run,
                    self.assertRaises(FABLE.AdvisorError),
                ):
                    FABLE.create_plan("packet")
                run.assert_not_called()

        self.write_state(planner=self.route())
        sibling = self.home / "linked-routing-state.json"
        os.link(state_path, sibling)
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(FABLE.subprocess, "run") as run,
            self.assertRaisesRegex(FABLE.AdvisorError, "multiple hard links"),
        ):
            FABLE.create_plan("packet")
        run.assert_not_called()

        sibling.unlink()
        self.write_state(planner=self.route())
        payload = json.loads((self.home / FABLE.STATE_FILENAME).read_text())
        payload.pop("managed_by")
        (self.home / FABLE.STATE_FILENAME).write_text(json.dumps(payload))
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)

    def test_create_signal_success_and_failure(self) -> None:
        self.write_state(planner=self.route("medium"))
        result, _ = self.invoke_with_results(
            FABLE.create_plan,
            "complete packet",
            model_response="\nPLAN_DRAFT\n1. Verify inputs.",
        )
        self.assertEqual(result["signal"], "PLAN_DRAFT")
        self.assertIn("Verify inputs", result["plan"])

        with self.assertRaisesRegex(FABLE.AdvisorError, "PLAN_DRAFT"):
            self.invoke_with_results(
                FABLE.create_plan,
                "complete packet",
                model_response="Here is a draft.",
            )

    def test_revise_requires_all_inputs_and_structured_non_empty_sections(self) -> None:
        self.write_state(planner=self.route())
        for position in range(4):
            values: list[object] = ["task", "v1 plan", "F-1: fix", "prior ledger"]
            values[position] = " "
            with self.subTest(position=position):
                with self.assertRaisesRegex(FABLE.AdvisorError, "non-empty string"):
                    FABLE.revise_plan(*values)

        valid = (
            "PLAN_REVISION\n\n"
            "## FINDINGS_LEDGER\n"
            "- F-1 — INCORPORATED: add verification.\n\n"
            "## REVISED_PLAN\n"
            "Version: v2 (source v1)\n1. Add verification."
        )
        result, calls = self.invoke_with_results(
            FABLE.revise_plan,
            "original task",
            "Version v1\nplan",
            "F-1: missing verification",
            "F-0 incorporated",
            model_response=valid,
        )
        self.assertEqual(result["signal"], "PLAN_REVISION")
        self.assertIn("## REVISED_PLAN", result["revision"])
        prompt = calls[1][1]["input"]
        self.assertIn("# ORIGINAL_TASK", prompt)
        self.assertIn("# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION", prompt)
        self.assertIn("# LATEST_ADVISOR_CRITIQUE_WITH_STABLE_FINDING_IDS", prompt)
        self.assertIn("# COMPACT_CUMULATIVE_FINDINGS_HISTORY", prompt)

        malformed_responses = (
            "PLAN_DRAFT\n## FINDINGS_LEDGER\nF-1\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## FINDINGS_LEDGER\n\n## REVISED_PLAN\nplan",
            "PLAN_REVISION\n## FINDINGS_LEDGER\nF-1\n## REVISED_PLAN\n",
            (
                "PLAN_REVISION\n## REVISED_PLAN\nplan\n"
                "## FINDINGS_LEDGER\nF-1"
            ),
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                with self.assertRaises(FABLE.AdvisorError):
                    self.invoke_with_results(
                        FABLE.revise_plan,
                        "task",
                        "v1 plan",
                        "F-1",
                        "history",
                        model_response=response,
                    )

    def test_repeated_revisions_are_fresh_and_never_use_sessions(self) -> None:
        self.write_state(planner=self.route())
        response = (
            "PLAN_REVISION\n## FINDINGS_LEDGER\n"
            "F-1 — INCORPORATED: reason\n## REVISED_PLAN\nv2 plan"
        )
        all_commands: list[list[str]] = []
        for _ in range(2):
            _, calls = self.invoke_with_results(
                FABLE.revise_plan,
                "task",
                "v1 plan",
                "F-1",
                "history",
                model_response=response,
            )
            all_commands.append(calls[1][0])
        self.assertEqual(len(all_commands), 2)
        for command in all_commands:
            self.assertEqual(command.count("--no-session-persistence"), 1)
            self.assertNotIn("--resume", command)
            self.assertNotIn("--session-id", command)

    def test_malformed_json_unconfirmed_model_and_bad_review_fail_closed(self) -> None:
        bad_outputs = (
            ("not json", "malformed JSON"),
            (
                json.dumps({"result": "PLAN_DRAFT\nDraft", "modelUsage": {}}),
                "did not confirm",
            ),
        )
        self.write_state(planner=self.route())
        for stdout, message in bad_outputs:
            with self.subTest(message=message):
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(
                        FABLE, "resolve_claude", return_value=Path("/fake/claude")
                    ),
                    mock.patch.object(
                        FABLE.subprocess,
                        "run",
                        side_effect=[
                            self.auth_result(),
                            self.completed(["claude"], stdout),
                        ],
                    ),
                ):
                    with self.assertRaisesRegex(FABLE.AdvisorError, message):
                        FABLE.create_plan("packet")

        self.write_state(advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "required plan decision"):
            self.invoke_with_results(
                FABLE.review_plan, "packet", model_response="Looks good."
            )

    def test_subprocess_failures_and_timeouts_do_not_leak_prompt_output(self) -> None:
        secret = "TOP-SECRET-PLAN-CONTENT"
        failed = self.completed(
            ["claude"],
            secret,
            returncode=17,
            stderr=f"provider error included {secret}",
        )
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE.subprocess, "run", side_effect=[self.auth_result(), failed]
            ),
        ):
            with self.assertRaises(FABLE.AdvisorError) as failure:
                FABLE.review_plan(secret)
        self.assertIn("17", str(failure.exception))
        self.assertNotIn(secret, str(failure.exception))

        timeout = subprocess.TimeoutExpired(["claude"], 600, output=secret, stderr=secret)
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE.subprocess, "run", side_effect=[self.auth_result(), timeout]
            ),
        ):
            with self.assertRaises(FABLE.AdvisorError) as timed_out:
                FABLE.review_plan(secret)
        self.assertIn("timed out", str(timed_out.exception))
        self.assertNotIn(secret, str(timed_out.exception))

    def test_input_bound_is_checked_before_subprocess(self) -> None:
        with mock.patch.object(FABLE.subprocess, "run") as run:
            with self.assertRaisesRegex(FABLE.AdvisorError, "character combined limit"):
                FABLE.review_plan("x" * (FABLE.MAX_INPUT_CHARS + 1))
        run.assert_not_called()

        self.write_state(planner=self.route())
        oversized_piece = "x" * (FABLE.MAX_INPUT_CHARS // 2 + 1)
        with mock.patch.object(FABLE.subprocess, "run") as run:
            with self.assertRaisesRegex(FABLE.AdvisorError, "character combined limit"):
                FABLE.revise_plan(
                    oversized_piece, oversized_piece, "critique", "history"
                )
        run.assert_not_called()

    def test_mcp_surface_exposes_exact_bounded_tools_and_schemas(self) -> None:
        initialized = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(
            initialized["result"]["serverInfo"]["name"],
            "codex-orchestration-fable-advisor",
        )
        listed = FABLE.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tools = listed["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            ["create_plan", "revise_plan", "review_plan", "status"],
        )
        for tool in tools:
            annotations = tool["annotations"]
            self.assertTrue(annotations["readOnlyHint"])
            self.assertFalse(annotations["destructiveHint"])
            self.assertTrue(annotations["idempotentHint"])
            self.assertTrue(annotations["openWorldHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(tools[0]["inputSchema"]["required"], ["packet"])
        self.assertEqual(
            tools[1]["inputSchema"]["required"],
            ["task", "current_plan", "critique", "history"],
        )
        self.assertEqual(tools[2]["inputSchema"]["required"], ["packet"])
        for name in ("task", "current_plan", "critique", "history"):
            self.assertEqual(
                tools[1]["inputSchema"]["properties"][name]["maxLength"],
                FABLE.MAX_INPUT_CHARS,
            )

    def test_status_reports_planner_or_advisor_without_account_metadata(self) -> None:
        scenarios = (
            ({"planner": self.route("low")}, ["planner"]),
            ({"advisor": self.route("max")}, ["advisor"]),
        )
        for seats, expected in scenarios:
            with self.subTest(expected=expected):
                self.write_state(**seats)
                with (
                    mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
                    mock.patch.object(
                        FABLE,
                        "check_claude_auth",
                        return_value={
                            "auth_method": "claude.ai",
                            "api_provider": "firstParty",
                        },
                    ),
                ):
                    payload = FABLE.status()
                self.assertEqual(payload["configured_seats"], expected)
                self.assertEqual(list(payload["seats"]), expected)
                text = json.dumps(payload)
                self.assertNotIn("subscription", text.lower())
                self.assertNotIn("account_plan", text.lower())
                for seat in expected:
                    self.assertEqual(payload["seats"][seat]["model"], FABLE.FABLE_MODEL)
                    self.assertEqual(
                        payload["seats"][seat]["effort"], seats[seat]["effort"]
                    )
                if "advisor" in expected:
                    self.assertEqual(payload["effort"], seats["advisor"]["effort"])

        self.write_state(planner=self.route(), advisor=self.route("xhigh"))
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"),
        ):
            FABLE.status()

    def test_status_tool_and_argument_validation_fail_closed(self) -> None:
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE,
                "check_claude_auth",
                return_value={
                    "auth_method": "claude.ai",
                    "api_provider": "firstParty",
                },
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

        extra = FABLE.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {"secret": "x"}},
            }
        )
        self.assertTrue(extra["result"]["isError"])
        self.assertIn("Unexpected tool argument", extra["result"]["content"][0]["text"])

    def test_saved_xhigh_and_legacy_max_efforts_remain_valid(self) -> None:
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                self.write_state(advisor=self.route(effort))
                self.assertEqual(FABLE.load_fable_route(self.home)["effort"], effort)


if __name__ == "__main__":
    unittest.main()
