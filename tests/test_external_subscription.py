from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/codex-orchestration/skills/codex-orchestration/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "external_subscription", SCRIPTS / "external_subscription.py"
)
assert SPEC and SPEC.loader
SUBSCRIPTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBSCRIPTION)


class ExternalSubscriptionTests(unittest.TestCase):
    def test_only_sealed_subscription_provider_model_pairs_are_allowed(self) -> None:
        provider, effort = SUBSCRIPTION.validate_route(
            "claude-fable", "claude-fable-5", "high", "create_plan"
        )
        self.assertEqual(
            provider["subscription_adapter"]["module"], "fable_advisor_mcp"
        )
        self.assertEqual(effort, "high")
        opus, opus_effort = SUBSCRIPTION.validate_route(
            "claude-opus", "claude-opus-5", "xhigh", "review_plan"
        )
        self.assertEqual(opus["name"], "Claude Opus 5")
        self.assertEqual(opus_effort, "xhigh")
        for values in (
            ("unknown", "claude-fable-5", "high", "create_plan"),
            ("claude-fable", "claude-other", "high", "create_plan"),
            ("claude-fable", "claude-opus-5", "high", "create_plan"),
            ("claude-opus", "claude-fable-5", "high", "create_plan"),
            ("claude-fable", "claude-fable-5", "extreme", "create_plan"),
            ("claude-fable", "claude-fable-5", "high", "general_prompt"),
        ):
            with self.subTest(values=values):
                with self.assertRaises(
                    (SUBSCRIPTION.SubscriptionAdapterError, ValueError)
                ):
                    SUBSCRIPTION.validate_route(*values)

    def test_status_reuses_first_party_auth_without_a_model_call(self) -> None:
        with mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "load_fable_route",
            return_value={"model": "claude-fable-5", "effort": "high"},
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "resolve_claude",
            return_value=Path("/trusted/claude"),
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "check_claude_auth",
            return_value={"auth_method": "claude.ai", "api_provider": "firstParty"},
        ):
            result = SUBSCRIPTION.status()
        self.assertFalse(result["model_call"])
        self.assertEqual(result["auth"], "claude.ai")
        self.assertEqual(result["runtime_identity"], "cli_metadata")

    def test_invoke_preserves_existing_no_tools_bridge_and_runtime_identity(self) -> None:
        expected = {
            "model": "claude-fable-5",
            "effort": "high",
            "used_models": ["claude-fable-5"],
            "signal": "PLAN_DRAFT",
        }
        with mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "load_fable_route",
            return_value={"model": "claude-fable-5", "effort": "high"},
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp, "create_plan", return_value=expected
        ) as create:
            result = SUBSCRIPTION.invoke(
                "create_plan", {"packet": "bounded planning packet"}
            )
        create.assert_called_once_with(packet="bounded planning packet")
        self.assertIs(result, expected)

    def test_argument_shape_and_runtime_metadata_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            SUBSCRIPTION.SubscriptionAdapterError, "arguments"
        ):
            SUBSCRIPTION.invoke("create_plan", {"prompt": "wrong key"})
        with mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "load_fable_route",
            return_value={"model": "claude-fable-5", "effort": "high"},
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "create_plan",
            return_value={
                "model": "claude-fable-5",
                "effort": "high",
                "used_models": [],
            },
        ):
            with self.assertRaisesRegex(
                SUBSCRIPTION.SubscriptionAdapterError, "metadata"
            ):
                SUBSCRIPTION.invoke("create_plan", {"packet": "bounded"})

    def test_opus_dispatch_checks_route_before_invocation(self) -> None:
        expected = {
            "model": "claude-opus-5",
            "effort": "xhigh",
            "used_models": ["claude-opus-5"],
            "decision": "PLAN_APPROVED",
        }
        with mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "load_fable_route",
            return_value={"model": "claude-opus-5", "effort": "xhigh"},
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "review_plan",
            return_value=expected,
        ) as review:
            result = SUBSCRIPTION.invoke(
                "review_plan",
                {"packet": "bounded"},
                provider_id="claude-opus",
                model="claude-opus-5",
                effort="xhigh",
            )
        self.assertIs(result, expected)
        review.assert_called_once_with(packet="bounded")

        with mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp,
            "load_fable_route",
            return_value={"model": "claude-fable-5", "effort": "high"},
        ), mock.patch.object(
            SUBSCRIPTION.fable_advisor_mcp, "review_plan"
        ) as review:
            with self.assertRaisesRegex(
                SUBSCRIPTION.SubscriptionAdapterError, "differs"
            ):
                SUBSCRIPTION.invoke(
                    "review_plan",
                    {"packet": "bounded"},
                    provider_id="claude-opus",
                    model="claude-opus-5",
                    effort="xhigh",
                )
            review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
