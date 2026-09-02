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
DEFAULT_MODEL_USAGE = object()
DEFAULT_STRUCTURED_OUTPUT = object()
AUTO_STRUCTURED_OUTPUT = object()


class FableAdvisorMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.write_state(advisor=self.route("high"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def route(
        effort: str = "high", *, model: str = FABLE.FABLE_MODEL
    ) -> dict[str, str]:
        return {
            "kind": "fable",
            "model": model,
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    @staticmethod
    def opus_route(effort: str = "high") -> dict[str, str]:
        return {
            "kind": "claude_subscription",
            "model": "claude-opus-5",
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    def write_state(self, *, schema: int = 6, **seats: object) -> None:
        subscription_routes = [
            route
            for route in seats.values()
            if isinstance(route, dict)
            and route.get("kind") in {"fable", "claude_subscription"}
        ]
        managed_mcp = {
            route["server"]: True
            for route in subscription_routes[:1]
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
        if schema >= 3 and "planner" not in payload:
            payload["planner"] = None
        if schema >= 4 and "designer" not in payload:
            payload["designer"] = None
        (self.home / FABLE.STATE_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    @staticmethod
    def completed(
        command: list[str], stdout: str, *, returncode: int = 0, stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def auth_result(
        self, subscription_type: object = "max"
    ) -> subprocess.CompletedProcess[str]:
        return self.completed(
            ["claude", "auth", "status"],
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": subscription_type,
                }
            ),
        )

    def version_result(
        self, version: str = "2.1.257 (Claude Code)", *, returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        return self.completed(
            ["claude", "--version"],
            version,
            returncode=returncode,
        )

    @staticmethod
    def current_model_usage(output_tokens: int = 12) -> dict[str, dict[str, object]]:
        return {
            FABLE.FABLE_MODEL: {
                "outputTokens": output_tokens,
                "canonicalModel": FABLE.FABLE_MODEL,
                "provider": "firstParty",
                "costBasis": "list",
            }
        }

    def model_result(
        self,
        response: str,
        *,
        model_usage: object = DEFAULT_MODEL_USAGE,
        structured_output: object = DEFAULT_STRUCTURED_OUTPUT,
        as_events: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        payload: dict[str, object] = {
            "result": response,
            "modelUsage": model_usage
            if model_usage is not DEFAULT_MODEL_USAGE
            else self.current_model_usage(),
        }
        if structured_output is not DEFAULT_STRUCTURED_OUTPUT:
            payload["structured_output"] = structured_output
        outer: object = (
            [
                {"type": "system", "subtype": "init"},
                {"type": "result", "subtype": "success", **payload},
            ]
            if as_events
            else payload
        )
        return self.completed(
            ["claude"],
            json.dumps(outer),
        )

    def invoke_with_results(
        self,
        function: object,
        *args: str,
        model_response: str,
        model_usage: object = DEFAULT_MODEL_USAGE,
        structured_output: object = AUTO_STRUCTURED_OUTPUT,
        as_events: bool = False,
    ) -> tuple[dict[str, object], list[tuple[list[str], dict[str, object]]]]:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[-1] == "--version":
                return self.version_result()
            if command[-2:] == ["auth", "status"] or command[-3:] == [
                "auth",
                "status",
                "--json",
            ]:
                return self.auth_result()
            selected_structured_output = structured_output
            if (
                selected_structured_output is AUTO_STRUCTURED_OUTPUT
                and function is FABLE.review_plan
            ):
                lines = model_response.strip().splitlines()
                if (
                    lines
                    and lines[0] in {"PLAN_APPROVED", "PLAN_REVISE"}
                    and "\n".join(lines[1:]).strip()
                ):
                    selected_structured_output = {
                        "signal": lines[0],
                        "body": "\n".join(lines[1:]).strip(),
                    }
                else:
                    selected_structured_output = DEFAULT_STRUCTURED_OUTPUT
            elif selected_structured_output is AUTO_STRUCTURED_OUTPUT:
                selected_structured_output = DEFAULT_STRUCTURED_OUTPUT
            return self.model_result(
                model_response,
                model_usage=model_usage,
                structured_output=selected_structured_output,
                as_events=as_events,
            )

        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = function(*args)
        return result, calls

    def invoke_with_stdout(
        self, function: object, *args: str, stdout: str
    ) -> dict[str, object]:
        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE.subprocess,
                "run",
                side_effect=[
                    self.version_result(),
                    self.auth_result(),
                    self.completed(["claude"], stdout),
                ],
            ),
        ):
            return function(*args)

    def test_review_is_pinned_sanitized_read_only_and_runtime_confirmed(self) -> None:
        env = {
            "CODEX_HOME": str(self.home),
            **{name: "must-not-leak" for name in FABLE.SENSITIVE_ENV},
        }
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if command[-1] == "--version":
                return self.version_result()
            if command[-2:] == ["auth", "status"] or command[-3:] == [
                "auth",
                "status",
                "--json",
            ]:
                return self.auth_result()
            return self.model_result(
                json.dumps(
                    {
                        "signal": "PLAN_APPROVED",
                        "body": "No material gap found.",
                    }
                ),
                structured_output={
                    "signal": "PLAN_APPROVED",
                    "body": "No material gap found.",
                },
            )

        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = FABLE.review_plan("Review this complete plan.")

        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["model"], FABLE.FABLE_MODEL)
        self.assertEqual(result["used_models"], [FABLE.FABLE_MODEL])
        self.assertNotIn("subscription_type", result)
        version_command, version_kwargs = calls[0]
        self.assertEqual(version_command, ["/fake/claude", "--version"])
        auth_command, auth_kwargs = calls[1]
        self.assertEqual(auth_command[-3:], ["auth", "status", "--json"])
        review_command, review_kwargs = calls[2]
        for flag in (
            "--print",
            "--safe-mode",
            "--tools",
            "--permission-mode",
            "--no-session-persistence",
            "--prompt-suggestions",
            "--output-format",
            "--json-schema",
            "--system-prompt",
        ):
            self.assertIn(flag, review_command)
        self.assertNotIn("--bare", review_command)
        self.assertEqual(review_command[review_command.index("--tools") + 1], "")
        self.assertEqual(
            review_command[review_command.index("--permission-mode") + 1], "dontAsk"
        )
        self.assertEqual(
            review_command[review_command.index("--model") + 1], FABLE.FABLE_MODEL
        )
        self.assertEqual(review_command[review_command.index("--effort") + 1], "high")
        self.assertEqual(
            review_command[review_command.index("--prompt-suggestions") + 1],
            "false",
        )
        self.assertEqual(
            review_command[review_command.index("--output-format") + 1], "json"
        )
        self.assertEqual(
            json.loads(review_command[review_command.index("--json-schema") + 1]),
            FABLE.PLAN_REVIEW_SCHEMA,
        )
        self.assertEqual(review_kwargs["input"], "Review this complete plan.")
        for kwargs in (version_kwargs, auth_kwargs, review_kwargs):
            sanitized = kwargs["env"]
            self.assertIsInstance(sanitized, dict)
            for name in FABLE.SENSITIVE_ENV:
                self.assertNotIn(name, sanitized)

    def test_auth_and_model_subprocesses_receive_only_platform_runtime_environment(
        self,
    ) -> None:
        hostile = {
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret",
            "ANTHROPIC_API_KEY": "provider-secret",
            "CLAUDE_CONFIG_DIR": "/hostile/config",
            "CLAUDE_CODE_CLIENT_KEY": "client-secret",
            "ANTHROPIC_UNKNOWN_GATEWAY_HEADER": "smuggled",
            "HTTP_PROXY": "http://hostile.invalid",
            "HTTPS_PROXY": "https://hostile.invalid",
            "SSL_CERT_FILE": "/hostile/ca.pem",
            "NODE_EXTRA_CA_CERTS": "/hostile/node-ca.pem",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "GOOGLE_APPLICATION_CREDENTIALS": "/hostile/google.json",
            "AZURE_CLIENT_SECRET": "azure-secret",
            "OTEL_EXPORTER_OTLP_HEADERS": "authorization=secret",
            "APPDATA": r"C:\hostile\roaming",
            "LOCALAPPDATA": r"C:\hostile\local",
        }
        scenarios = (
            (
                "posix",
                {
                    "PATH": "/trusted/bin",
                    "LANG": "en_US.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "LC_CTYPE": "UTF-8",
                    "HOME": "/trusted/home",
                    "TMPDIR": "/trusted/tmp",
                    "USER": "hostile-user",
                    "LOGNAME": "hostile-logname",
                    "SystemRoot": r"C:\should-not-pass",
                    **hostile,
                },
                {
                    "PATH": "/trusted/bin",
                    "LANG": "en_US.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "LC_CTYPE": "UTF-8",
                    "HOME": "/trusted/home",
                    "TMPDIR": "/trusted/tmp",
                    "USER": "trusted-user",
                    "LOGNAME": "trusted-user",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                },
            ),
            (
                "nt",
                {
                    "path": r"C:\trusted\bin",
                    "lang": "en-US",
                    "lc_all": "C",
                    "lc_ctype": "UTF-8",
                    "systemroot": r"C:\Windows",
                    "comspec": r"C:\Windows\System32\cmd.exe",
                    "pathext": ".COM;.EXE",
                    "temp": r"C:\trusted\temp",
                    "tmp": r"C:\trusted\tmp",
                    "userprofile": r"C:\Users\trusted",
                    "home": r"C:\hostile\home-redirection",
                    **{name.lower(): value for name, value in hostile.items()},
                },
                {
                    "PATH": r"C:\trusted\bin",
                    "LANG": "en-US",
                    "LC_ALL": "C",
                    "LC_CTYPE": "UTF-8",
                    "SystemRoot": r"C:\Windows",
                    "ComSpec": r"C:\Windows\System32\cmd.exe",
                    "PATHEXT": ".COM;.EXE",
                    "TEMP": r"C:\trusted\temp",
                    "TMP": r"C:\trusted\tmp",
                    "USERPROFILE": r"C:\Users\trusted",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                },
            ),
        )
        executable = Path("/fake/claude")
        for platform, inherited, expected in scenarios:
            with self.subTest(platform=platform):
                calls: list[tuple[list[str], dict[str, object]]] = []

                def fake_run(
                    command: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    calls.append((command, kwargs))
                    if command[-1] == "--version":
                        return self.version_result()
                    if command[-2:] == ["auth", "status"] or command[-3:] == [
                        "auth",
                        "status",
                        "--json",
                    ]:
                        return self.auth_result()
                    return self.model_result(
                        json.dumps(
                            {
                                "signal": "PLAN_APPROVED",
                                "body": "No material gap found.",
                            }
                        ),
                        structured_output={
                            "signal": "PLAN_APPROVED",
                            "body": "No material gap found.",
                        },
                    )

                with (
                    mock.patch.dict(os.environ, inherited, clear=True),
                    mock.patch.object(FABLE.os, "name", platform),
                    mock.patch.object(
                        FABLE,
                        "_canonical_posix_identity",
                        return_value="trusted-user",
                    ),
                    mock.patch.object(
                        FABLE,
                        "load_fable_route",
                        return_value={"model": FABLE.FABLE_MODEL, "effort": "high"},
                    ),
                    mock.patch.object(FABLE, "resolve_claude", return_value=executable),
                    mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
                ):
                    result = FABLE.review_plan("Review this complete plan.")

                self.assertEqual(result["decision"], "PLAN_APPROVED")
                self.assertEqual(len(calls), 3)
                for _, kwargs in calls:
                    self.assertEqual(kwargs["env"], expected)

    def test_auth_accepts_only_exact_first_party_pro_max_or_team_tuples(self) -> None:
        valid_subscriptions = ("pro", "max", "team")
        executable = Path("/fake/claude")
        for subscription in valid_subscriptions:
            with self.subTest(subscription=subscription), mock.patch.object(
                FABLE,
                "_run_json",
                return_value={
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiProvider": "firstParty",
                    "subscriptionType": subscription,
                },
            ) as run:
                self.assertEqual(
                    FABLE.check_claude_auth(executable),
                    {
                        "auth_method": "claude.ai",
                        "api_provider": "firstParty",
                    },
                )
                self.assertEqual(
                    run.call_args.args[0],
                    [str(executable), "auth", "status", "--json"],
                )
                self.assertFalse(run.call_args.kwargs["include_anthropic_api_key"])

        api_key_payload = {
            "loggedIn": True,
            "authMethod": "api_key",
            "apiProvider": "firstParty",
            "apiKeySource": "ANTHROPIC_API_KEY",
        }
        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            mock.patch.object(
                FABLE,
                "_run_json",
                side_effect=[{"loggedIn": False}, api_key_payload],
            ) as run,
        ):
            self.assertEqual(
                FABLE.check_claude_auth(executable, allow_api_keys=True),
                {"auth_method": "api_key", "api_provider": "firstParty"},
            )
            self.assertEqual(
                [call.kwargs["include_anthropic_api_key"] for call in run.call_args_list],
                [False, True],
            )

        managed_key_payload = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "apiKeySource": "/login managed key",
            "subscriptionType": None,
        }
        with mock.patch.object(
            FABLE, "_run_json", return_value=managed_key_payload
        ) as run:
            self.assertEqual(
                FABLE.check_claude_auth(executable, allow_api_keys=True),
                {"auth_method": "console_api_key", "api_provider": "firstParty"},
            )
            self.assertFalse(run.call_args.kwargs["include_anthropic_api_key"])

        invalid_payloads = (
            {
                "loggedIn": False,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": 1,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "console",
                "apiProvider": "firstParty",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "bedrock",
                "subscriptionType": "team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "Team",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": "enterprise",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "subscriptionType": [],
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
            },
        )
        secret = "TOP-SECRET-AUTH-METADATA"
        for payload in invalid_payloads:
            with self.subTest(payload=payload), mock.patch.object(
                FABLE, "_run_json", return_value={**payload, "account": secret}
            ):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    FABLE.check_claude_auth(executable)
                self.assertIn("Pro, Max, or Team", str(failure.exception))
                self.assertNotIn(secret, str(failure.exception))

    def test_opus_auth_rejects_api_and_managed_keys_without_forwarding_them(self) -> None:
        executable = Path("/fake/claude")
        api_auth_tuples = (
            {
                "loggedIn": True,
                "authMethod": "api_key",
                "apiProvider": "firstParty",
                "apiKeySource": "ANTHROPIC_API_KEY",
            },
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "apiKeySource": "/login managed key",
                "subscriptionType": None,
            },
        )
        for payload in api_auth_tuples:
            with (
                self.subTest(auth=payload["apiKeySource"]),
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
                mock.patch.object(FABLE, "_run_json", return_value=payload) as run,
            ):
                with self.assertRaisesRegex(
                    FABLE.AdvisorError, "Pro, Max, or Team"
                ) as failure:
                    FABLE.check_claude_auth(executable, allow_api_keys=False)
                run.assert_called_once()
                self.assertFalse(run.call_args.kwargs["include_anthropic_api_key"])
                self.assertNotIn("ANTHROPIC_API_KEY", str(failure.exception))
                self.assertNotIn("managed key", str(failure.exception))

    def test_api_key_auth_forwards_only_the_selected_key_to_the_sealed_cli(self) -> None:
        token = "test-anthropic-key"
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            if command[-1] == "--version":
                return self.version_result()
            if command[-3:] == ["auth", "status", "--json"]:
                if environment.get("ANTHROPIC_API_KEY") == token:
                    return self.completed(
                        command,
                        json.dumps(
                            {
                                "loggedIn": True,
                                "authMethod": "api_key",
                                "apiProvider": "firstParty",
                                "apiKeySource": "ANTHROPIC_API_KEY",
                            }
                        ),
                    )
                return self.completed(command, json.dumps({"loggedIn": False}))
            return self.model_result(
                "PLAN_APPROVED\nNo material gap.",
                structured_output={
                    "signal": "PLAN_APPROVED",
                    "body": "No material gap.",
                },
            )

        with (
            mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(self.home),
                    "ANTHROPIC_API_KEY": token,
                    "HTTP_PROXY": "http://hostile.invalid",
                    "CLAUDE_CONFIG_DIR": "/hostile/config",
                },
                clear=False,
            ),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            result = FABLE.review_plan("bounded packet")

        self.assertEqual(result["model"], FABLE.FABLE_MODEL)
        self.assertEqual(len(calls), 4)
        auth_environments = [calls[1][1]["env"], calls[2][1]["env"]]
        self.assertNotIn("ANTHROPIC_API_KEY", auth_environments[0])
        self.assertEqual(auth_environments[1].get("ANTHROPIC_API_KEY"), token)
        model_environment = calls[3][1]["env"]
        self.assertEqual(model_environment.get("ANTHROPIC_API_KEY"), token)
        for environment in (*auth_environments, model_environment):
            self.assertNotIn("HTTP_PROXY", environment)
            self.assertNotIn("CLAUDE_CONFIG_DIR", environment)

    def test_posix_identity_lookup_failure_stops_before_any_subprocess(self) -> None:
        executable = Path("/fake/claude")
        with (
            mock.patch.object(FABLE.os, "name", "posix"),
            mock.patch.object(
                FABLE,
                "_canonical_posix_identity",
                side_effect=FABLE.AdvisorError("canonical identity unavailable"),
            ),
            mock.patch.object(FABLE.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(
                FABLE.AdvisorError, "canonical identity unavailable"
            ):
                FABLE.check_claude_auth(executable)
        run.assert_not_called()

    def test_current_fable_version_reattest_rejects_bad_or_failed_cli_output(self) -> None:
        executable = Path("/fake/claude")
        failures = (
            (self.version_result("2.1.256 (Claude Code)"), "2.1.257 or newer"),
            (self.version_result("Claude Code 2.1.257"), "unparseable version"),
            (self.version_result("ignored", returncode=17), "exited with 17"),
        )
        for result, expected_error in failures:
            with self.subTest(expected_error=expected_error), mock.patch.object(
                FABLE.subprocess, "run", return_value=result
            ) as run:
                with self.assertRaisesRegex(FABLE.AdvisorError, expected_error):
                    FABLE.verify_fable_claude_version(executable, FABLE.FABLE_MODEL)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0], [str(executable), "--version"])

    def test_current_fable_status_reattests_version_before_auth(self) -> None:
        calls: list[list[str]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[-1] == "--version":
                return self.version_result()
            return self.auth_result()

        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)}),
            mock.patch.object(
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(FABLE.subprocess, "run", side_effect=fake_run),
        ):
            status = FABLE.status()

        self.assertTrue(status["available"])
        self.assertEqual(
            calls,
            [
                ["/fake/claude", "--version"],
                ["/fake/claude", "auth", "status", "--json"],
            ],
        )

    def test_fable_5_1_runtime_identity_is_exact_and_helper_free(self) -> None:
        exact_usage = {
            FABLE.FABLE_MODEL: {
                "outputTokens": 12,
                "canonicalModel": FABLE.FABLE_MODEL,
                "provider": "firstParty",
                "costBasis": "list",
            }
        }
        result, _ = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="PLAN_APPROVED\nNo material gap found.",
            model_usage=exact_usage,
        )
        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["model"], FABLE.FABLE_MODEL)
        self.assertEqual(result["used_models"], [FABLE.FABLE_MODEL])

        secret = "TOP-SECRET-MODEL-OUTPUT"
        rejected_scenarios = (
            (
                {FABLE.FABLE_MODEL: {"outputTokens": 12}},
                "canonical model and first-party provider",
            ),
            (
                {
                    FABLE.FABLE_MODEL: {
                        "outputTokens": 12,
                        "canonicalModel": "claude-other",
                        "provider": "firstParty",
                    }
                },
                "malformed modelUsage value",
            ),
            (
                {
                    FABLE.FABLE_MODEL: {
                        "outputTokens": 12,
                        "canonicalModel": FABLE.FABLE_MODEL,
                        "provider": "bedrock",
                    }
                },
                "malformed modelUsage value",
            ),
            (
                {
                    FABLE.FABLE_MODEL: {
                        "outputTokens": 12,
                        "canonicalModel": FABLE.FABLE_MODEL,
                        "provider": "firstParty",
                        "costBasis": "discounted",
                    }
                },
                "malformed modelUsage value",
            ),
            (
                {
                    **exact_usage,
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
                "outside the allowed Fable runtime policy",
            ),
            (
                {
                    FABLE.LEGACY_FABLE_MODEL: {
                        "outputTokens": 12,
                        "canonicalModel": FABLE.LEGACY_FABLE_MODEL,
                        "provider": "firstParty",
                    }
                },
                "pinned Claude Fable 5.1 primary",
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

    def test_legacy_fable_runtime_policy_retains_reviewed_primary_and_helper(
        self,
    ) -> None:
        self.write_state(
            schema=5,
            advisor=self.route(model=FABLE.LEGACY_FABLE_MODEL),
        )
        resolved_primary = "claude-opus-4-8"
        model_usage = {
            resolved_primary: {"outputTokens": 12},
            FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
        }
        result, calls = self.invoke_with_results(
            FABLE.review_plan,
            "legacy packet",
            model_response="PLAN_APPROVED\nLegacy policy retained.",
            model_usage=model_usage,
        )
        self.assertEqual(result["model"], FABLE.LEGACY_FABLE_MODEL)
        self.assertEqual(
            result["used_models"],
            sorted((resolved_primary, FABLE.FABLE_HELPER_MODEL)),
        )
        self.assertFalse(any(command[-1] == "--version" for command, _ in calls))

    def test_runtime_model_usage_values_fail_closed(self) -> None:
        malformed_values = (
            None,
            "12",
            12,
            1.5,
            [],
            {},
            {"outputTokens": -1},
            {"outputTokens": float("nan")},
            {"outputTokens": float("inf")},
            {"outputTokens": float("-inf")},
            {"outputTokens": True},
            {"": 12},
            {"outputTokens": "12"},
        )
        for usage_value in malformed_values:
            with self.subTest(usage_value=usage_value):
                with self.assertRaisesRegex(FABLE.AdvisorError, "[Rr]untime metadata"):
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response="PLAN_APPROVED\nNo material gap found.",
                        model_usage={FABLE.FABLE_MODEL: usage_value},
                    )

        for usage in (
            {7: {"outputTokens": 1}},
            {FABLE.FABLE_MODEL: {7: 1}},
            {"": {"outputTokens": 1}, FABLE.FABLE_MODEL: {"outputTokens": 1}},
        ):
            with self.subTest(non_json_key=usage):
                with self.assertRaisesRegex(FABLE.AdvisorError, "[Rr]untime metadata"):
                    FABLE._validate_runtime_models(usage)

        for usage in (
            {
                FABLE.FABLE_MODEL: {
                    "outputTokens": 0,
                    "canonicalModel": FABLE.FABLE_MODEL,
                    "provider": "firstParty",
                }
            },
            {
                FABLE.FABLE_MODEL: {
                    "outputTokens": 10**309,
                    "canonicalModel": FABLE.FABLE_MODEL,
                    "provider": "firstParty",
                }
            },
            {
                FABLE.FABLE_MODEL: {
                    "costUSD": 0.25,
                    "outputTokens": 12,
                    "canonicalModel": FABLE.FABLE_MODEL,
                    "provider": "firstParty",
                }
            },
        ):
            with self.subTest(valid_usage=usage):
                result, _ = self.invoke_with_results(
                    FABLE.review_plan,
                    "packet",
                    model_response="PLAN_APPROVED\nNo material gap found.",
                    model_usage=usage,
                )
                self.assertEqual(result["decision"], "PLAN_APPROVED")

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
        create_command = create_calls[2][0]
        review_command = review_calls[2][0]
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

        self.write_state(
            schema=2, advisor=self.route(model=FABLE.LEGACY_FABLE_MODEL)
        )
        self.assertEqual(FABLE.load_fable_route(self.home)["effort"], "high")
        self.write_state(
            schema=2, planner=self.route(model=FABLE.LEGACY_FABLE_MODEL)
        )
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home, seat="planner")

        self.write_state(
            schema=4, advisor=self.route(model=FABLE.LEGACY_FABLE_MODEL)
        )
        self.assertEqual(FABLE.load_fable_route(self.home)["effort"], "high")
        self.write_state(
            schema=4,
            advisor=self.route(model=FABLE.LEGACY_FABLE_MODEL),
            designer=self.route(model=FABLE.LEGACY_FABLE_MODEL),
        )
        with self.assertRaisesRegex(FABLE.AdvisorError, "state is invalid"):
            FABLE.load_fable_route(self.home)
        self.write_state(
            schema=5, advisor=self.route(model=FABLE.LEGACY_FABLE_MODEL)
        )
        self.assertEqual(
            FABLE.load_fable_route(self.home)["model"], FABLE.LEGACY_FABLE_MODEL
        )

    def test_opus_route_pins_primary_and_rejects_every_unverified_helper(self) -> None:
        self.write_state(schema=5, advisor=self.opus_route("xhigh"))
        result, calls = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="PLAN_APPROVED\nNo material gap.",
            model_usage={FABLE.OPUS_MODEL: {"outputTokens": 12}},
        )
        self.assertEqual(result["model"], FABLE.OPUS_MODEL)
        self.assertEqual(result["effort"], "xhigh")
        self.assertFalse(any(command[-1] == "--version" for command, _ in calls))
        review_command = calls[1][0]
        self.assertEqual(
            review_command[review_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(
            review_command[review_command.index("--effort") + 1], "xhigh"
        )

        with self.assertRaisesRegex(
            FABLE.AdvisorError, "outside the allowed Claude runtime policy"
        ):
            self.invoke_with_results(
                FABLE.review_plan,
                "packet",
                model_response="PLAN_APPROVED\nNo material gap.",
                model_usage={
                    FABLE.OPUS_MODEL: {"outputTokens": 12},
                    FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1},
                },
            )
        with self.assertRaisesRegex(
            FABLE.AdvisorError, "did not confirm the pinned Claude Opus 5"
        ):
            self.invoke_with_results(
                FABLE.review_plan,
                "packet",
                model_response="PLAN_APPROVED\nNo material gap.",
                model_usage={FABLE.FABLE_HELPER_MODEL: {"outputTokens": 1}},
            )

    def test_opus_planner_create_and_revise_pin_exact_route_and_primary_usage(
        self,
    ) -> None:
        self.write_state(schema=5, planner=self.opus_route("max"))
        created, create_calls = self.invoke_with_results(
            FABLE.create_plan,
            "bounded task packet",
            model_response="PLAN_DRAFT\n1. Verify the boundary.",
            model_usage={FABLE.OPUS_MODEL: {"outputTokens": 12}},
        )
        self.assertEqual(created["signal"], "PLAN_DRAFT")
        self.assertEqual(created["model"], FABLE.OPUS_MODEL)
        self.assertEqual(created["effort"], "max")
        self.assertEqual(created["used_models"], [FABLE.OPUS_MODEL])
        create_command = create_calls[1][0]
        self.assertEqual(
            create_command[create_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(create_command[create_command.index("--effort") + 1], "max")
        self.assertEqual(
            create_command[create_command.index("--system-prompt") + 1],
            FABLE.PLANNER_CREATE_SYSTEM_PROMPT,
        )

        revision = (
            "PLAN_REVISION\n\n"
            "## FINDINGS_LEDGER\n"
            "F-1 INCORPORATED: added the missing check.\n\n"
            "## REVISED_PLAN\n"
            "Source v1; revised v2. Verify the boundary."
        )
        revised, revise_calls = self.invoke_with_results(
            FABLE.revise_plan,
            "original task",
            "v1 canonical plan",
            "F-1 missing check",
            "F-1 pending",
            model_response=revision,
            model_usage={FABLE.OPUS_MODEL: {"outputTokens": 24}},
        )
        self.assertEqual(revised["signal"], "PLAN_REVISION")
        self.assertEqual(revised["revision"], revision)
        self.assertEqual(revised["model"], FABLE.OPUS_MODEL)
        self.assertEqual(revised["effort"], "max")
        self.assertEqual(revised["used_models"], [FABLE.OPUS_MODEL])
        revise_command, revise_kwargs = revise_calls[1]
        self.assertEqual(
            revise_command[revise_command.index("--model") + 1], FABLE.OPUS_MODEL
        )
        self.assertEqual(revise_command[revise_command.index("--effort") + 1], "max")
        self.assertEqual(
            revise_command[revise_command.index("--system-prompt") + 1],
            FABLE.PLANNER_REVISE_SYSTEM_PROMPT,
        )
        self.assertIn("# ORIGINAL_TASK\noriginal task", revise_kwargs["input"])
        self.assertIn(
            "# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION\nv1 canonical plan",
            revise_kwargs["input"],
        )

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
        prompt = calls[2][1]["input"]
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
            all_commands.append(calls[2][0])
        self.assertEqual(len(all_commands), 2)
        for command in all_commands:
            self.assertEqual(command.count("--no-session-persistence"), 1)
            self.assertNotIn("--resume", command)
            self.assertNotIn("--session-id", command)

    def test_review_uses_and_locally_enforces_the_exact_structured_schema(self) -> None:
        structured = {
            "signal": "PLAN_APPROVED",
            "body": "No material gap found.",
        }
        result, calls = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="This prose is not the decision contract.",
            structured_output=structured,
        )
        self.assertEqual(result["decision"], "PLAN_APPROVED")
        self.assertEqual(result["review"], "PLAN_APPROVED\nNo material gap found.")
        command = calls[2][0]
        self.assertEqual(command.count("--json-schema"), 1)
        self.assertEqual(
            json.loads(command[command.index("--json-schema") + 1]),
            {
                "type": "object",
                "properties": {
                    "signal": {
                        "type": "string",
                        "enum": ["PLAN_APPROVED", "PLAN_REVISE"],
                    },
                    "body": {"type": "string", "minLength": 1},
                },
                "required": ["signal", "body"],
                "additionalProperties": False,
            },
        )

        legacy, _ = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response=json.dumps(
                {
                    "signal": "PLAN_REVISE",
                    "body": "F-1: add the missing negative regression.",
                }
            ),
        )
        self.assertEqual(legacy["decision"], "PLAN_REVISE")
        self.assertEqual(
            legacy["review"],
            "PLAN_REVISE\nF-1: add the missing negative regression.",
        )

        malformed = (
            ("PLAN_APPROVED\nraw prose is not structured", DEFAULT_STRUCTURED_OUTPUT),
            (json.dumps({"signal": "PLAN_APPROVED"}), DEFAULT_STRUCTURED_OUTPUT),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "ok", "extra": 1}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_DRAFT", "body": "wrong signal"}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "   "}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": 7}),
                DEFAULT_STRUCTURED_OUTPUT,
            ),
            (
                json.dumps({"signal": "PLAN_APPROVED", "body": "one"}),
                {"signal": "PLAN_REVISE", "body": "two"},
            ),
        )
        secret = "TOP-SECRET-STRUCTURED-OUTPUT"
        for response, structured_output in malformed:
            with self.subTest(response=response, structured=structured_output):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    self.invoke_with_results(
                        FABLE.review_plan,
                        "packet",
                        model_response=response.replace("raw prose", secret),
                        structured_output=structured_output,
                    )
                self.assertNotIn(secret, str(failure.exception))

    def test_cli_output_container_accepts_one_result_and_rejects_ambiguity(
        self,
    ) -> None:
        self.write_state(planner=self.route())
        created, _ = self.invoke_with_results(
            FABLE.create_plan,
            "packet",
            model_response="PLAN_DRAFT\nDraft",
            as_events=True,
        )
        self.assertEqual(created["signal"], "PLAN_DRAFT")

        revision = (
            "PLAN_REVISION\n## FINDINGS_LEDGER\n"
            "F-1 INCORPORATED: fixed.\n## REVISED_PLAN\nv2"
        )
        revised, _ = self.invoke_with_results(
            FABLE.revise_plan,
            "task",
            "v1",
            "F-1",
            "history",
            model_response=revision,
            as_events=True,
        )
        self.assertEqual(revised["signal"], "PLAN_REVISION")

        self.write_state(advisor=self.route())
        reviewed, _ = self.invoke_with_results(
            FABLE.review_plan,
            "packet",
            model_response="ignored prose",
            structured_output={
                "signal": "PLAN_APPROVED",
                "body": "No material gap.",
            },
            as_events=True,
        )
        self.assertEqual(reviewed["decision"], "PLAN_APPROVED")

        result_event = {
            "type": "result",
            "subtype": "success",
            "result": json.dumps(
                {"signal": "PLAN_APPROVED", "body": "No material gap."}
            ),
            "modelUsage": self.current_model_usage(),
        }
        secret = "TOP-SECRET-AMBIGUOUS-EVENT"
        malformed_outers: tuple[object, ...] = (
            [],
            [{"type": "system", "subtype": "init"}],
            [result_event, result_event],
            [{"type": "system"}, secret, result_event],
            {**result_event, "type": "assistant"},
            {**result_event, "type": None},
            {
                "subtype": "error",
                "result": json.dumps(
                    {"signal": "PLAN_APPROVED", "body": secret}
                ),
                "modelUsage": self.current_model_usage(),
            },
            secret,
        )
        for outer in malformed_outers:
            with self.subTest(outer=outer):
                with self.assertRaises(FABLE.AdvisorError) as failure:
                    self.invoke_with_stdout(
                        FABLE.review_plan,
                        "packet",
                        stdout=json.dumps(outer),
                    )
                self.assertNotIn(secret, str(failure.exception))

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
                            self.version_result(),
                            self.auth_result(),
                            self.completed(["claude"], stdout),
                        ],
                    ),
                ):
                    with self.assertRaisesRegex(FABLE.AdvisorError, message):
                        FABLE.create_plan("packet")

        self.write_state(advisor=self.route())
        with self.assertRaisesRegex(FABLE.AdvisorError, "structured output"):
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
                FABLE.subprocess,
                "run",
                side_effect=[self.version_result(), self.auth_result(), failed],
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
                FABLE.subprocess,
                "run",
                side_effect=[self.version_result(), self.auth_result(), timeout],
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
                        FABLE, "resolve_claude", return_value=Path("/fake/claude")
                    ),
                    mock.patch.object(
                        FABLE,
                        "verify_fable_claude_version",
                        return_value="2.1.257",
                    ),
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
                FABLE, "resolve_claude", return_value=Path("/fake/claude")
            ),
            mock.patch.object(
                FABLE,
                "verify_fable_claude_version",
                return_value="2.1.257",
            ),
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
        error_payload = json.loads(extra["result"]["content"][0]["text"])
        self.assertIn("fresh native status", error_payload["recovery"])
        self.assertIn("fully quit and reopen Codex", error_payload["recovery"])
        self.assertIn("do not re-authenticate solely", error_payload["recovery"])

    def test_saved_xhigh_and_legacy_max_efforts_remain_valid(self) -> None:
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                self.write_state(advisor=self.route(effort))
                self.assertEqual(FABLE.load_fable_route(self.home)["effort"], effort)


if __name__ == "__main__":
    unittest.main()
