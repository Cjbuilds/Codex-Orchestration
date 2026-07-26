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

    def test_fable_invoke_accepts_either_reviewed_primary_runtime_identity(
        self,
    ) -> None:
        for primary in ("claude-fable-5", "claude-opus-4-8"):
            expected = {
                "model": "claude-fable-5",
                "effort": "high",
                "used_models": [
                    primary,
                    SUBSCRIPTION.fable_advisor_mcp.FABLE_HELPER_MODEL,
                ],
                "signal": "PLAN_DRAFT",
            }
            with self.subTest(primary=primary), mock.patch.object(
                SUBSCRIPTION.fable_advisor_mcp,
                "load_fable_route",
                return_value={"model": "claude-fable-5", "effort": "high"},
            ), mock.patch.object(
                SUBSCRIPTION.fable_advisor_mcp,
                "create_plan",
                return_value=expected,
            ):
                self.assertIs(
                    SUBSCRIPTION.invoke(
                        "create_plan", {"packet": "bounded planning packet"}
                    ),
                    expected,
                )

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

    def test_opus_planner_create_and_revise_dispatch_exact_public_operations(
        self,
    ) -> None:
        route = {"model": "claude-opus-5", "effort": "max"}
        created = {
            "model": "claude-opus-5",
            "effort": "max",
            "used_models": ["claude-opus-5"],
            "signal": "PLAN_DRAFT",
            "plan": "PLAN_DRAFT\nDraft",
        }
        revised = {
            "model": "claude-opus-5",
            "effort": "max",
            "used_models": ["claude-opus-5"],
            "signal": "PLAN_REVISION",
            "revision": (
                "PLAN_REVISION\n\n## FINDINGS_LEDGER\n"
                "F-1 INCORPORATED\n\n## REVISED_PLAN\nv2 plan"
            ),
        }
        revision_arguments = {
            "task": "original task",
            "current_plan": "v1 plan",
            "critique": "F-1 missing check",
            "history": "F-1 pending",
        }
        with (
            mock.patch.object(
                SUBSCRIPTION.fable_advisor_mcp,
                "load_fable_route",
                return_value=route,
            ),
            mock.patch.object(
                SUBSCRIPTION.fable_advisor_mcp,
                "create_plan",
                return_value=created,
            ) as create,
            mock.patch.object(
                SUBSCRIPTION.fable_advisor_mcp,
                "revise_plan",
                return_value=revised,
            ) as revise,
        ):
            create_result = SUBSCRIPTION.invoke(
                "create_plan",
                {"packet": "bounded planning packet"},
                provider_id="claude-opus",
                model="claude-opus-5",
                effort="max",
            )
            revise_result = SUBSCRIPTION.invoke(
                "revise_plan",
                revision_arguments,
                provider_id="claude-opus",
                model="claude-opus-5",
                effort="max",
            )

        self.assertIs(create_result, created)
        self.assertIs(revise_result, revised)
        create.assert_called_once_with(packet="bounded planning packet")
        revise.assert_called_once_with(**revision_arguments)


if __name__ == "__main__":
    unittest.main()
