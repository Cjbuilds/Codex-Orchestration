from __future__ import annotations

from copy import deepcopy
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "qwen_advisor_mcp_under_test", SCRIPTS / "qwen_advisor_mcp.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QWEN = load_module()


def snapshot(value: object = None, *, present: bool = False) -> dict[str, object]:
    result: dict[str, object] = {"known": True, "present": present}
    if present:
        result["value"] = value
    return result


def routing_state(home: Path, *, region: str = "global") -> dict[str, object]:
    return {
        "schema": 6,
        "policy_version": 6,
        "managed_by": "codex-orchestration",
        "config_file": str(home / "config.toml"),
        "executor": {
            "kind": "model",
            "model": "gpt-5.6-luna",
            "effort": "xhigh",
        },
        "planner": {
            "kind": "model",
            "model": "gpt-5.6-sol",
            "effort": "high",
        },
        "advisor": {
            "kind": "qwen_cli",
            "model": QWEN.QWEN_MODEL,
            "effort": "native",
            "region": region,
            "server": "qwen-advisor-python3",
        },
        "designer": None,
        "managed": {
            "mode": f"{QWEN.routing_state.MANAGED_MARKER}\nmode",
            "usage": f"{QWEN.routing_state.MANAGED_MARKER}\nusage",
            "metadata": False,
            "namespace": "agents",
            "mcp": {"qwen-advisor-python3": True},
        },
        "previous": {
            "mode": snapshot(),
            "usage": snapshot(),
            "metadata": snapshot(),
            "namespace": snapshot(),
            "mcp": {"qwen-advisor-python3": snapshot()},
        },
        "scalar_origin": None,
        "managed_feature": None,
    }


def qwen_response(
    decision: str = "PLAN_APPROVED",
    *,
    review: str = "No material gap found.",
    model: str | None = None,
) -> dict[str, object]:
    selected = model or QWEN.QWEN_MODEL
    content = json.dumps({"decision": decision, "review": review})
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": selected,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            },
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
        },
    }


class QwenAdvisorTests(unittest.TestCase):
    def write_state(self, home: Path, *, region: str = "global") -> None:
        (home / QWEN.STATE_FILENAME).write_text(
            json.dumps(routing_state(home, region=region)), encoding="utf-8"
        )

    def test_load_route_requires_exact_qwen_advisor_and_current_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.write_state(home)
            self.assertEqual(
                QWEN.load_advisor_route(home),
                {
                    "model": QWEN.QWEN_MODEL,
                    "effort": "native",
                    "region": "global",
                },
            )
            state = routing_state(home)
            state["advisor"] = None
            state["managed"]["mcp"]["qwen-advisor-python3"] = False
            (home / QWEN.STATE_FILENAME).write_text(
                json.dumps(state), encoding="utf-8"
            )
            with self.assertRaisesRegex(QWEN.QwenAdvisorError, "not the configured"):
                QWEN.load_advisor_route(home)

    def test_structured_output_authorizes_model_decision_and_zero_tools(self) -> None:
        decision, review, used = QWEN._validate_output(
            json.dumps(
                qwen_response(
                    "PLAN_REVISE", review="QWEN-1: Add a negative test."
                )
            )
        )
        self.assertEqual(decision, "PLAN_REVISE")
        self.assertIn("QWEN-1", review)
        self.assertEqual(used, [QWEN.QWEN_MODEL])

    def test_structured_output_negative_matrix_fails_closed(self) -> None:
        baseline = qwen_response()
        cases = []
        wrong_model = deepcopy(baseline)
        wrong_model["model"] = "qwen3.7-max"
        cases.append(("wrong model", wrong_model))
        missing_choices = deepcopy(baseline)
        missing_choices.pop("choices")
        cases.append(("missing choices", missing_choices))
        multiple_choices = deepcopy(baseline)
        multiple_choices["choices"].append(deepcopy(multiple_choices["choices"][0]))
        cases.append(("multiple choices", multiple_choices))
        boolean_index = deepcopy(baseline)
        boolean_index["choices"][0]["index"] = False
        cases.append(("boolean index", boolean_index))
        failed = deepcopy(baseline)
        failed["choices"][0]["finish_reason"] = "length"
        cases.append(("failed result", failed))
        wrong_role = deepcopy(baseline)
        wrong_role["choices"][0]["message"]["role"] = "tool"
        cases.append(("wrong role", wrong_role))
        tool_calls = deepcopy(baseline)
        tool_calls["choices"][0]["message"]["tool_calls"] = [{"name": "read"}]
        cases.append(("tool calls", tool_calls))
        function_call = deepcopy(baseline)
        function_call["choices"][0]["message"]["function_call"] = {"name": "read"}
        cases.append(("function call", function_call))
        malformed_content = deepcopy(baseline)
        malformed_content["choices"][0]["message"]["content"] = []
        cases.append(("malformed content", malformed_content))
        missing_usage = deepcopy(baseline)
        missing_usage.pop("usage")
        cases.append(("missing usage", missing_usage))
        boolean_usage = deepcopy(baseline)
        boolean_usage["usage"]["completion_tokens"] = False
        cases.append(("boolean usage", boolean_usage))
        negative_usage = deepcopy(baseline)
        negative_usage["usage"]["prompt_tokens"] = -1
        cases.append(("negative usage", negative_usage))
        inconsistent_usage = deepcopy(baseline)
        inconsistent_usage["usage"]["total_tokens"] = 99
        cases.append(("inconsistent usage", inconsistent_usage))
        missing_signal = qwen_response("Looks good")
        cases.append(("missing signal", missing_signal))
        malformed_review_envelope = deepcopy(baseline)
        malformed_review_envelope["choices"][0]["message"]["content"] = "not-json"
        cases.append(("malformed review envelope", malformed_review_envelope))
        missing_review = deepcopy(baseline)
        missing_review["choices"][0]["message"]["content"] = json.dumps(
            {"decision": "PLAN_APPROVED"}
        )
        cases.append(("missing review", missing_review))
        extra_review_field = deepcopy(baseline)
        extra_review_field["choices"][0]["message"]["content"] = json.dumps(
            {"decision": "PLAN_APPROVED", "review": "Good.", "extra": True}
        )
        cases.append(("extra review field", extra_review_field))
        empty_review = deepcopy(baseline)
        empty_review["choices"][0]["message"]["content"] = json.dumps(
            {"decision": "PLAN_APPROVED", "review": "  "}
        )
        cases.append(("empty review", empty_review))
        oversized_review = deepcopy(baseline)
        oversized_review["choices"][0]["message"]["content"] = json.dumps(
            {
                "decision": "PLAN_APPROVED",
                "review": "x" * (QWEN.MAX_REVIEW_CHARS + 1),
            }
        )
        cases.append(("oversized review", oversized_review))
        cases.append(("malformed json", None))

        for label, response in cases:
            with self.subTest(label=label), self.assertRaises(QWEN.QwenAdvisorError):
                QWEN._validate_output(
                    "{" if response is None else json.dumps(response)
                )

    def test_review_uses_sealed_api_contract_and_never_returns_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.write_state(home)
            captured: dict[str, object] = {}

            def fake_post(endpoint, payload, credential):
                captured.update(
                    {
                        "endpoint": endpoint,
                        "payload": deepcopy(payload),
                        "credential": credential,
                    }
                )
                return json.dumps(qwen_response())

            ready = {
                "protocol": QWEN.QWEN_PROTOCOL,
                "region": "global",
                "endpoint": QWEN.REGIONS["global"]["endpoint"],
                "credential_provider": "qwen-token-plan-global",
                "helper": str(home / "helper.py"),
            }
            with (
                mock.patch.object(QWEN, "codex_home", return_value=home),
                mock.patch.object(QWEN, "check_prerequisites", return_value=ready),
                mock.patch.object(
                    QWEN.external_credentials,
                    "read_credential",
                    return_value="sk-sp-test-secret",
                ),
                mock.patch.object(QWEN, "_post_json", side_effect=fake_post),
            ):
                result = QWEN.review_plan("complete packet")

            self.assertEqual(captured["endpoint"], ready["endpoint"])
            self.assertEqual(captured["credential"], "sk-sp-test-secret")
            payload = captured["payload"]
            self.assertEqual(
                set(payload), {"model", "messages", "response_format", "stream"}
            )
            self.assertEqual(payload["model"], QWEN.QWEN_MODEL)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertIs(payload["stream"], False)
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["messages"][0]["role"], "system")
            self.assertIn("exactly one JSON object", payload["messages"][0]["content"])
            self.assertEqual(
                payload["messages"][1],
                {
                    "role": "user",
                    "content": (
                        "BEGIN_UNTRUSTED_PLAN_PACKET\n"
                        "complete packet\n"
                        "END_UNTRUSTED_PLAN_PACKET\n"
                    ),
                },
            )
            self.assertNotIn("sk-sp-test-secret", json.dumps(result))
            self.assertEqual(result["used_models"], [QWEN.QWEN_MODEL])
            self.assertEqual(result["protocol"], QWEN.QWEN_PROTOCOL)

    def test_structured_output_rejects_oversized_response(self) -> None:
        with self.assertRaisesRegex(QWEN.QwenAdvisorError, "oversized"):
            QWEN._validate_output("x" * (QWEN.MAX_OUTPUT_CHARS + 1))

    def test_post_json_uses_direct_sealed_http_contract(self) -> None:
        class Headers:
            def get_content_type(self) -> str:
                return "application/json"

        class Response:
            status = 200
            headers = Headers()

            def __init__(self, url: str) -> None:
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def geturl(self) -> str:
                return self.url

            def read(self, size: int) -> bytes:
                self.read_size = size
                return json.dumps(qwen_response()).encode("utf-8")

        endpoint = QWEN.REGIONS["global"]["endpoint"]
        expected_url = endpoint + "/chat/completions"
        captured: dict[str, object] = {}
        response = Response(expected_url)

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        opener = mock.Mock()
        opener.open.side_effect = fake_open
        with mock.patch.object(
            QWEN.urllib.request, "build_opener", return_value=opener
        ) as build_opener:
            body = QWEN._post_json(
                endpoint,
                {"model": QWEN.QWEN_MODEL, "stream": False},
                "sk-sp-test-secret",
            )

        self.assertEqual(json.loads(body), qwen_response())
        self.assertEqual(captured["timeout"], QWEN.QWEN_TIMEOUT_SECONDS)
        self.assertEqual(response.read_size, QWEN.MAX_OUTPUT_CHARS + 1)
        request = captured["request"]
        self.assertEqual(request.full_url, expected_url)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"model": QWEN.QWEN_MODEL, "stream": False},
        )
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer sk-sp-test-secret")
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertEqual(headers["x-dashscope-session-cache"], "disable")
        handlers = build_opener.call_args.args
        self.assertIsInstance(handlers[0], QWEN.urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], QWEN._NoRedirectHandler)

    def test_post_json_hides_http_error_body(self) -> None:
        endpoint = QWEN.REGIONS["global"]["endpoint"]
        error = QWEN.urllib.error.HTTPError(
            endpoint + "/chat/completions",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b"sensitive provider response"),
        )
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(QWEN.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(
                QWEN.QwenAdvisorError, "HTTP 401; output withheld"
            ) as raised:
                QWEN._post_json(endpoint, {"model": QWEN.QWEN_MODEL}, "sk-sp-test")
        self.assertNotIn("sensitive provider response", str(raised.exception))

    def test_review_rejects_non_plan_credential_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.write_state(home)
            ready = {
                "protocol": QWEN.QWEN_PROTOCOL,
                "region": "global",
                "endpoint": QWEN.REGIONS["global"]["endpoint"],
                "credential_provider": "qwen-token-plan-global",
                "helper": str(home / "helper.py"),
            }
            with (
                mock.patch.object(QWEN, "codex_home", return_value=home),
                mock.patch.object(QWEN, "check_prerequisites", return_value=ready),
                mock.patch.object(
                    QWEN.external_credentials,
                    "read_credential",
                    return_value="ordinary-api-key",
                ),
                mock.patch.object(QWEN, "_post_json") as post,
            ):
                with self.assertRaisesRegex(QWEN.QwenAdvisorError, "invalid type"):
                    QWEN.review_plan("packet")
            post.assert_not_called()

    def test_status_is_non_billable_and_reports_no_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.write_state(home, region="china")
            with (
                mock.patch.object(QWEN, "codex_home", return_value=home),
                mock.patch.object(
                    QWEN,
                    "check_prerequisites",
                    return_value={"protocol": QWEN.QWEN_PROTOCOL},
                ) as check,
                mock.patch.object(QWEN, "_post_json") as post,
            ):
                result = QWEN.status()
            check.assert_called_once_with("china")
            post.assert_not_called()
            self.assertFalse(result["model_call_made"])
            self.assertEqual(result["model"], QWEN.QWEN_MODEL)

    def test_mcp_surface_exposes_only_review_and_status(self) -> None:
        definitions = QWEN.tool_definitions()
        self.assertEqual([item["name"] for item in definitions], ["review_plan", "status"])
        for item in definitions:
            self.assertTrue(item["annotations"]["readOnlyHint"])
            self.assertFalse(item["annotations"]["destructiveHint"])
            self.assertFalse(item["inputSchema"]["additionalProperties"])

    def test_tool_call_rejects_unknown_arguments_without_echoing_values(self) -> None:
        response = QWEN.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "status",
                    "arguments": {"credential": "secret-value"},
                },
            }
        )
        rendered = json.dumps(response)
        self.assertIn("Unexpected tool argument", rendered)
        self.assertNotIn("secret-value", rendered)


if __name__ == "__main__":
    unittest.main()
