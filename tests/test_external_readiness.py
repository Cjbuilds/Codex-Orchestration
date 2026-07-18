from __future__ import annotations

from pathlib import Path
import sys
import unittest


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

import external_readiness as READINESS  # noqa: E402


class ExternalReadinessTests(unittest.TestCase):
    def test_nominal_native_route_reaches_honest_route_acceptance(self) -> None:
        states = [
            READINESS.Readiness.UNCONFIGURED,
            READINESS.Readiness.PROVIDER_DECLARED,
            READINESS.Readiness.AUTH_REQUIRED,
            READINESS.Readiness.AUTH_READY,
            READINESS.Readiness.CAPABILITY_VERIFIED,
            READINESS.Readiness.ROLE_STAGED,
            READINESS.Readiness.RESTART_REQUIRED,
            READINESS.Readiness.READY,
            READINESS.Readiness.ROUTE_ACCEPTED,
        ]
        current = states[0]
        for target in states[1:]:
            current = READINESS.transition(current, target)
        self.assertEqual(current, READINESS.Readiness.ROUTE_ACCEPTED)

    def test_route_acceptance_is_not_runtime_confirmation(self) -> None:
        self.assertEqual(
            READINESS.runtime_identity_state(
                route_accepted=True, evidence_source=None
            ),
            READINESS.Readiness.ROUTE_ACCEPTED,
        )
        with self.assertRaisesRegex(READINESS.ReadinessError, "not mechanical"):
            READINESS.runtime_identity_state(
                route_accepted=True, evidence_source="model_self_report"
            )
        self.assertEqual(
            READINESS.runtime_identity_state(
                route_accepted=True, evidence_source="subscription_cli_runtime"
            ),
            READINESS.Readiness.USED_CONFIRMED,
        )

    def test_every_unlisted_transition_fails_closed(self) -> None:
        states = list(READINESS.Readiness)
        for source in states:
            allowed = READINESS.legal_targets(source)
            for target in states:
                if target in allowed:
                    self.assertEqual(READINESS.transition(source, target), target)
                else:
                    with self.subTest(source=source, target=target):
                        with self.assertRaises(READINESS.ReadinessError):
                            READINESS.transition(source, target)

    def test_blocking_states_cannot_skip_recovery_contracts(self) -> None:
        self.assertEqual(
            READINESS.legal_targets(READINESS.Readiness.CLI_CHANGED),
            frozenset(
                {
                    READINESS.Readiness.AUTH_READY,
                    READINESS.Readiness.UNSUPPORTED,
                }
            ),
        )
        self.assertNotIn(
            READINESS.Readiness.READY,
            READINESS.legal_targets(READINESS.Readiness.CONFIG_DRIFT),
        )
        self.assertFalse(
            READINESS.legal_targets(READINESS.Readiness.UNSUPPORTED)
        )

    def test_explicit_reconfiguration_and_disconnect_edges_are_legal(self) -> None:
        expected = {
            READINESS.Readiness.READY: {
                READINESS.Readiness.ROLE_STAGED,
                READINESS.Readiness.AUTH_REQUIRED,
                READINESS.Readiness.CAPABILITY_VERIFIED,
            },
            READINESS.Readiness.RESTART_REQUIRED: {
                READINESS.Readiness.READY,
                READINESS.Readiness.AUTH_REQUIRED,
                READINESS.Readiness.CAPABILITY_VERIFIED,
            },
        }
        for source, targets in expected.items():
            with self.subTest(source=source):
                self.assertTrue(targets <= READINESS.legal_targets(source))

    def test_unknown_and_non_string_states_fail_closed(self) -> None:
        for value in (None, True, 1, "ready", "FUTURE"):
            with self.subTest(value=value):
                with self.assertRaises(READINESS.ReadinessError):
                    READINESS.parse_readiness(value)


if __name__ == "__main__":
    unittest.main()
