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
    / "kimi_designer_mcp.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("kimi_designer_mcp", SCRIPT)
assert SPEC and SPEC.loader
KIMI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(KIMI)


class KimiDesignerMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.write_state()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_state(self, *, route: dict[str, str] | None = None) -> None:
        designer = route or {
            "kind": "kimi_cli",
            "model": "kimi-code/k3",
            "effort": "max",
            "server": "kimi-designer-python3",
        }
        marker = KIMI.routing_state.MANAGED_MARKER
        payload = {
            "schema": 5,
            "policy_version": 5,
            "managed_by": "codex-orchestration",
            "config_file": str(self.home / "config.toml"),
            "executor": {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"},
            "planner": None,
            "advisor": None,
            "designer": designer,
            "managed": {
                "mode": f"{marker}\nmode",
                "usage": f"{marker}\nusage",
                "metadata": False,
                "namespace": "agents",
                "mcp": {"kimi-designer-python3": True},
            },
            "previous": {
                "mode": {"known": True, "present": False},
                "usage": {"known": True, "present": False},
                "metadata": {"known": True, "present": False},
                "namespace": {"known": True, "present": False},
                "mcp": {
                    "kimi-designer-python3": {"known": True, "present": False}
                },
            },
            "scalar_origin": None,
            "managed_feature": None,
        }
        (self.home / KIMI.STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def transcript(
        response: str = "DESIGN_HANDOFF\nUse one clear visual hierarchy.",
        *,
        model: str = "kimi-code/k3",
        update: str | None = None,
    ) -> str:
        messages: list[dict[str, object]] = [
            {
                "jsonrpc": "2.0",
                "id": 0,
                "result": {
                    "protocolVersion": 1,
                    "agentInfo": {"name": "Kimi Code CLI", "version": "0.27.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "sessionId": "session-test",
                    "configOptions": [
                        {"id": "model", "currentValue": model, "type": "select"}
                    ],
                },
            },
        ]
        if update is not None:
            messages.append(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-test",
                        "update": {"sessionUpdate": update},
                    },
                }
            )
        for chunk in (response[:12], response[12:]):
            messages.append(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-test",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": chunk},
                        },
                    },
                }
            )
        messages.append({"jsonrpc": "2.0", "id": 2, "result": {"stopReason": "end_turn"}})
        return "\n".join(json.dumps(message) for message in messages)

    def test_state_route_is_exact_and_schema_bound(self) -> None:
        self.assertEqual(KIMI.load_designer_route(self.home)["effort"], "max")
        self.write_state(
            route={
                "kind": "kimi_cli",
                "model": "kimi-code/k2",
                "effort": "max",
                "server": "kimi-designer-python3",
            }
        )
        with self.assertRaisesRegex(KIMI.KimiDesignerError, "state is invalid"):
            KIMI.load_designer_route(self.home)

        malformed_effort: dict[str, object] = {
            "kind": "kimi_cli",
            "model": "kimi-code/k3",
            "effort": ["max"],
            "server": "kimi-designer-python3",
        }
        self.write_state(route=malformed_effort)  # type: ignore[arg-type]
        with self.assertRaisesRegex(KIMI.KimiDesignerError, "state is invalid"):
            KIMI.load_designer_route(self.home)

    def test_prerequisites_require_oauth_k3_and_max_without_api_key(self) -> None:
        catalog = {
            "providers": {
                "managed:kimi-code": {
                    "type": "kimi",
                    "apiKey": "",
                    "oauth": {"storage": "file", "key": "oauth/kimi-code"},
                }
            },
            "models": {
                "kimi-code/k3": {
                    "provider": "managed:kimi-code",
                    "model": "k3",
                    "defaultEffort": "max",
                    "supportEfforts": ["low", "high", "max"],
                }
            },
        }
        outputs = ["0.27.0\n", "0.12.0\n", json.dumps(catalog)]
        with mock.patch.object(KIMI, "_resolve_command", side_effect=lambda name: Path(name)), mock.patch.object(
            KIMI, "_run_probe", side_effect=outputs
        ):
            ready = KIMI.check_prerequisites()
        self.assertEqual(ready["auth_method"], "kimi-code-oauth")
        self.assertEqual(ready["model"], "kimi-code/k3")

        catalog["providers"]["managed:kimi-code"]["apiKey"] = "secret"
        outputs = ["0.27.0\n", "0.12.0\n", json.dumps(catalog)]
        with mock.patch.object(KIMI, "_resolve_command", side_effect=lambda name: Path(name)), mock.patch.object(
            KIMI, "_run_probe", side_effect=outputs
        ), self.assertRaisesRegex(KIMI.KimiDesignerError, "audited contract"):
            KIMI.check_prerequisites()

        for malformed in ({"nested": "secret"}, ["secret"]):
            catalog["providers"]["managed:kimi-code"]["apiKey"] = malformed
            outputs = ["0.27.0\n", "0.12.0\n", json.dumps(catalog)]
            with mock.patch.object(
                KIMI, "_resolve_command", side_effect=lambda name: Path(name)
            ), mock.patch.object(
                KIMI, "_run_probe", side_effect=outputs
            ), self.assertRaisesRegex(KIMI.KimiDesignerError, "audited contract"):
                KIMI.check_prerequisites()

        catalog["providers"]["managed:kimi-code"]["apiKey"] = ""
        catalog["models"]["kimi-code/k3"]["supportEfforts"] = None
        outputs = ["0.27.0\n", "0.12.0\n", json.dumps(catalog)]
        with mock.patch.object(
            KIMI, "_resolve_command", side_effect=lambda name: Path(name)
        ), mock.patch.object(
            KIMI, "_run_probe", side_effect=outputs
        ), self.assertRaisesRegex(KIMI.KimiDesignerError, "audited contract"):
            KIMI.check_prerequisites()

    def test_transcript_requires_runtime_identity_and_rejects_tools(self) -> None:
        parsed = KIMI._parse_acpx_transcript(self.transcript())
        self.assertEqual(parsed["runtime_model"], "kimi-code/k3")
        self.assertTrue(parsed["response"].startswith("DESIGN_HANDOFF"))
        with self.assertRaisesRegex(KIMI.KimiDesignerError, "pinned Kimi K3"):
            KIMI._parse_acpx_transcript(self.transcript(model="kimi-code/k2"))
        with self.assertRaisesRegex(KIMI.KimiDesignerError, "call a tool"):
            KIMI._parse_acpx_transcript(self.transcript(update="tool_call"))
        forbidden_fs = self.transcript() + "\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 9, "method": "fs/read_text_file", "params": {}}
        )
        with self.assertRaisesRegex(KIMI.KimiDesignerError, "operation forbidden"):
            KIMI._parse_acpx_transcript(forbidden_fs)

    def test_call_is_stateless_pinned_and_deny_all(self) -> None:
        ready = {
            "acpx": "acpx",
            "model": "kimi-code/k3",
            "effort": "max",
            "auth_method": "kimi-code-oauth",
        }
        completed = subprocess.CompletedProcess(
            ["acpx"], 0, self.transcript(), "diagnostic output"
        )
        with mock.patch.object(KIMI, "load_designer_route", return_value={
            "model": "kimi-code/k3",
            "effort": "max",
            "server": "kimi-designer-python3",
        }), mock.patch.object(KIMI, "check_prerequisites", return_value=ready), mock.patch.object(
            KIMI.subprocess, "run", return_value=completed
        ) as run:
            result = KIMI.create_design_handoff("Design the approved settings screen.")
        command = run.call_args.args[0]
        self.assertIn("--deny-all", command)
        self.assertIn("--no-terminal", command)
        self.assertIn("--allowed-tools=", command)
        self.assertEqual(command[command.index("--model") + 1], "kimi-code/k3")
        self.assertEqual(command[-2:], ["--file", "-"])
        sent_prompt = run.call_args.kwargs["input"]
        self.assertTrue(sent_prompt.startswith(KIMI.DESIGNER_PROMPT))
        self.assertIn("Design the approved settings screen.", sent_prompt)
        self.assertEqual(result["runtime_model"], "kimi-code/k3")
        self.assertEqual(result["tool_policy"], "deny-all/no-terminal/disposable-cwd")

    def test_environment_scrubs_provider_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "KIMI_MODEL_API_KEY": "secret",
                "OPENROUTER_API_KEY": "secret",
                "SAFE_VALUE": "kept",
            },
            clear=True,
        ):
            env = KIMI.sanitized_environment()
        self.assertNotIn("KIMI_MODEL_API_KEY", env)
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertEqual(env["SAFE_VALUE"], "kept")
        self.assertEqual(env["KIMI_CODE_NO_AUTO_UPDATE"], "1")

    def test_mcp_surface_exposes_only_bounded_designer_and_status(self) -> None:
        tools = KIMI.tool_definitions()
        self.assertEqual([tool["name"] for tool in tools], ["create_design_handoff", "status"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        response = KIMI.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(response["result"]["tools"]), 2)


if __name__ == "__main__":
    unittest.main()
