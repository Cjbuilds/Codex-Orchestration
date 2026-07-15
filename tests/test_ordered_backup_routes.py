from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/codex-orchestration/skills/codex-orchestration/scripts"
sys.path.insert(0, str(SCRIPTS))

import configure_native_routing as native  # noqa: E402
import fable_advisor_mcp as fable  # noqa: E402


class BackupSpecTests(unittest.TestCase):
    def test_parse_backup_specs(self) -> None:
        self.assertEqual(
            native.parse_backup_spec("model:gpt-5.6-sol@xhigh", "executor"),
            {"kind": "model", "model": "gpt-5.6-sol", "effort": "xhigh"},
        )
        self.assertEqual(
            native.parse_backup_spec("agent:secondary_worker", "planner"),
            {"kind": "agent", "agent": "secondary_worker"},
        )
        self.assertEqual(
            native.parse_backup_spec("fable:high", "advisor"),
            {"kind": "fable", "model": native.FABLE_MODEL, "effort": "high"},
        )

    def test_parse_backup_specs_reject_bad_grammar_and_fable_executor(self) -> None:
        for seat, value in (
            ("executor", "fable:high"),
            ("executor", "model:gpt@high@extra"),
            ("planner", "model:@high"),
            ("planner", "agent:Bad-Name"),
            ("planner", "fable:bogus"),
            ("planner", "agent:foo "),
        ):
            with self.subTest(seat=seat, value=value), self.assertRaises(native.ConfigurationError):
                native.parse_backup_spec(value, seat)

    def test_validate_backup_cap_and_duplicate_identity(self) -> None:
        primary = {"kind": "model", "model": "gpt-5.6-sol", "effort": "high"}
        backups = [
            {"kind": "model", "model": "backup-one", "effort": "high"},
            {"kind": "model", "model": "backup-two", "effort": "high"},
        ]
        native.validate_route_chains(primary, [], primary, backups, None, [], None)
        with self.assertRaises(native.ConfigurationError):
            native.validate_route_chains(primary, [*backups, {"kind": "model", "model": "third", "effort": "high"}], None, [], None, [], None)
        with self.assertRaises(native.ConfigurationError):
            native.validate_route_chains(
                primary,
                [{"kind": "model", "model": "gpt-5.6-sol", "effort": "low"}],
                None,
                [],
                None,
                [],
            )

    def test_custom_agent_identity_resolution_catches_alias_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            agents = home / "agents"
            agents.mkdir()
            for name in ("planner_alias", "advisor_alias"):
                (agents / f"{name}.toml").write_text(
                    f'name = "{name}"\ndescription = "alias"\nmodel = "gpt-5.6-sol"\nmodel_provider = "openai"\ndeveloper_instructions = "work"\n',
                    encoding="utf-8",
                )
            identities = native.agent_route_identities(
                home,
                [
                    {"kind": "agent", "agent": "planner_alias"},
                    {"kind": "agent", "agent": "advisor_alias"},
                ],
            )
            with self.assertRaises(native.ConfigurationError):
                native.validate_route_chains(
                    {"kind": "model", "model": "executor", "effort": "high"},
                    [],
                    {"kind": "agent", "agent": "planner_alias"},
                    [],
                    {"kind": "agent", "agent": "advisor_alias"},
                    [],
                    identities,
                )

    def test_unknown_root_provider_overlaps_matching_custom_agent_model(self) -> None:
        with self.assertRaises(native.ConfigurationError):
            native.validate_route_chains(
                {"kind": "model", "model": "executor", "effort": "high"},
                [],
                {"kind": "model", "model": "gpt-5.6-sol", "effort": "high"},
                [],
                {"kind": "agent", "agent": "advisor_alias"},
                [],
                {"advisor_alias": ("openai", "gpt-5.6-sol")},
            )


class FableCandidateTests(unittest.TestCase):
    def test_candidate_activation_id_and_exhaustive_classification(self) -> None:
        route = {"kind": "fable", "model": "claude-fable-5", "effort": "high", "server": "fable-advisor-python3"}
        activation = fable.candidate_activation_id("planner", 1, route)
        self.assertEqual(activation, "planner:1:anthropic-claude-code/claude-fable-5@high")
        outcome = fable.classify_fable_outcome(
            code="TRANSPORT_FAILURE",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            deliverable_valid=False,
        )
        self.assertEqual(outcome["eligible"], True)
        model_unavailable = fable.classify_fable_outcome(
            code="MODEL_UNAVAILABLE",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=False,
            deliverable_valid=False,
        )
        self.assertEqual(model_unavailable["state"], "ELIGIBLE_PRESTART")
        impossible = fable.classify_fable_outcome(
            code="DELIVERABLE_VALID",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            deliverable_valid=False,
        )
        self.assertEqual(impossible["state"], "STATE_UNKNOWN")
        frozen = fable.classify_fable_outcome(
            code="UNKNOWN",
            authenticated=False,
            identity_matched=False,
            mechanically_no_tools=False,
            invocation_started=False,
            deliverable_valid=False,
        )
        self.assertEqual(frozen["state"], "STATE_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
