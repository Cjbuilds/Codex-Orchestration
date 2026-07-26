from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "codex-orchestration" / "skills" / "codex-orchestration" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import external_providers  # noqa: E402
import routing_state  # noqa: E402

import configure_native_routing  # noqa: E402


def _state(*, planner: dict[str, str] | None, designer: dict[str, str] | None) -> dict[str, object]:
    server = "fable-advisor-python3"
    managed = {
        "mode": f"{routing_state.MANAGED_MARKER}\nmode",
        "usage": f"{routing_state.MANAGED_MARKER}\nusage",
        "metadata": False,
        "namespace": "agents",
        "mcp": {server: True} if planner or designer else {server: False},
    }
    previous = {
        "mode": {"known": True, "present": False},
        "usage": {"known": True, "present": False},
        "metadata": {"known": True, "present": False},
        "namespace": {"known": True, "present": False},
        "mcp": {server: {"known": True, "present": False}},
    }
    return {
        "schema": 6,
        "policy_version": 6,
        "managed_by": "codex-orchestration",
        "config_file": "/tmp/codex/config.toml",
        "executor": {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"},
        "planner": planner,
        "advisor": None,
        "designer": designer,
        "managed": managed,
        "previous": previous,
        "scalar_origin": None,
        "managed_feature": None,
    }


class OpusDesignerContractTests(unittest.TestCase):
    def opus(self, effort: str, *, kind: str = "claude_subscription") -> dict[str, str]:
        return {
            "kind": kind,
            "model": routing_state.OPUS_MODEL,
            "effort": effort,
            "server": "fable-advisor-python3",
        }

    def test_schema_six_accepts_independent_opus_planner_and_designer_efforts(self) -> None:
        value = _state(planner=self.opus("xhigh"), designer=self.opus("max"))
        self.assertIs(routing_state.validate_routing_state(value), value)

    def test_designer_rejects_fable_and_mixed_subscription_identity(self) -> None:
        fable = self.opus("high", kind="fable") | {"model": routing_state.FABLE_MODEL}
        with self.assertRaises(routing_state.RoutingStateError):
            routing_state.validate_routing_state(_state(planner=None, designer=fable))
        mixed = _state(planner=self.opus("high"), designer=self.opus("max"))
        mixed["designer"]["server"] = "fable-advisor-python"
        with self.assertRaises(routing_state.RoutingStateError):
            routing_state.validate_routing_state(mixed)

    def test_schema_five_cannot_smuggle_designer_opus(self) -> None:
        legacy = _state(planner=None, designer=self.opus("high"))
        legacy["schema"] = 5
        legacy["policy_version"] = 5
        with self.assertRaises(routing_state.RoutingStateError):
            routing_state.validate_routing_state(legacy)

    def test_opus_manifest_maps_designer_create_design(self) -> None:
        provider = external_providers.load_provider("claude-opus")
        adapter = provider["subscription_adapter"]
        self.assertIn("designer", adapter["allowed_seats"])
        self.assertIn("create_design", adapter["allowed_operations"])

    def test_mcp_surface_contains_read_only_design_tool_and_signal_prompt(self) -> None:
        script = SCRIPTS / "fable_advisor_mcp.py"
        spec = importlib.util.spec_from_file_location("opus_designer_mcp", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = [item["name"] for item in module.tool_definitions()]
        self.assertIn("create_design", names)
        self.assertTrue(module.DESIGNER_SYSTEM_PROMPT.splitlines()[-1].endswith(
            "Report only to the root orchestrator."
        ))

    def test_transition_allows_opus_designer_addition_but_blocks_removal(self) -> None:
        existing = _state(planner=self.opus("high"), designer=None)
        configure_native_routing._guard_subscription_transition(
            existing,
            self.opus("high"),
            None,
            self.opus("max"),
        )
        with self.assertRaises(configure_native_routing.ConfigurationError):
            configure_native_routing._guard_subscription_transition(
                existing,
                None,
                None,
                self.opus("max"),
            )


if __name__ == "__main__":
    unittest.main()
