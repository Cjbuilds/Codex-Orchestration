from __future__ import annotations

from copy import deepcopy
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

import routing_state as STATE  # noqa: E402


def snapshot(value: object = None, *, present: bool = False) -> dict[str, object]:
    saved: dict[str, object] = {"known": True, "present": present}
    if present:
        saved["value"] = value
    return saved


def fable_route(server: str = "fable-advisor-python3") -> dict[str, str]:
    return {
        "kind": "fable",
        "model": STATE.FABLE_MODEL,
        "effort": "high",
        "server": server,
    }


def genuine_state(schema: int) -> dict[str, object]:
    managed: dict[str, object] = {
        "mode": f"{STATE.MANAGED_MARKER}\nmode body",
        "usage": f"{STATE.MANAGED_MARKER}\nusage body",
        "metadata": False,
        "namespace": STATE.ROUTING_TOOL_NAMESPACE,
    }
    previous: dict[str, object] = {
        "mode": snapshot(),
        "usage": snapshot("prior usage", present=True),
        "metadata": snapshot(True, present=True),
        "namespace": {"known": False, "present": False},
    }
    state: dict[str, object] = {
        "schema": schema,
        "policy_version": schema,
        "managed_by": "codex-orchestration",
        "config_file": "/tmp/codex/config.toml",
        "executor": {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"},
        "advisor": {"kind": "agent", "agent": "independent_advisor"},
        "managed": managed,
        "previous": previous,
        "scalar_origin": None,
        "managed_feature": None,
    }
    if schema == 2:
        state["advisor"] = fable_route()
    if schema >= 3:
        state["planner"] = fable_route()
    if schema == 4:
        state["designer"] = {
            "kind": "model",
            "model": "gpt-designer",
            "effort": "high",
        }
    if schema >= 2:
        managed["mcp"] = {
            "fable-advisor-python3": True,
            "fable-advisor-python": False,
        }
        previous["mcp"] = {
            "fable-advisor-python3": snapshot(),
            "fable-advisor-python": snapshot(False, present=True),
        }
    return state


class RoutingStateTests(unittest.TestCase):
    def test_genuine_schemas_one_through_four_are_accepted(self) -> None:
        for schema in (1, 2, 3, 4):
            with self.subTest(schema=schema):
                state = genuine_state(schema)
                self.assertIs(STATE.validate_routing_state(state), state)

    def test_scalar_conversion_and_retained_disabled_mcp_are_accepted(self) -> None:
        state = genuine_state(3)
        state["planner"] = {"kind": "model", "model": "gpt-planner", "effort": "high"}
        managed = state["managed"]
        managed["mcp"] = {server: False for server in managed["mcp"]}
        state["scalar_origin"] = True
        state["managed_feature"] = {
            "enabled": True,
            "hide_spawn_agent_metadata": False,
            "tool_namespace": STATE.ROUTING_TOOL_NAMESPACE,
            "multi_agent_mode_hint_text": managed["mode"],
            "usage_hint_text": managed["usage"],
        }
        self.assertIs(STATE.validate_routing_state(state), state)

    def test_full_negative_invariant_matrix_fails_closed(self) -> None:
        baseline = genuine_state(4)

        def schema(value: object):
            return lambda state: state.__setitem__("schema", value)

        def policy(value: object):
            return lambda state: state.__setitem__("policy_version", value)

        mutations = [
            *( (f"schema {value!r}", schema(value)) for value in (True, 1.0, "4", None, 0, 5) ),
            *( (f"policy {value!r}", policy(value)) for value in (True, 4.0, "4", None, 0, 5, 3) ),
            ("missing top key", lambda state: state.pop("managed_by")),
            ("extra top key", lambda state: state.__setitem__("future", True)),
            ("wrong owner", lambda state: state.__setitem__("managed_by", "other")),
            ("empty config path", lambda state: state.__setitem__("config_file", "")),
            ("missing managed key", lambda state: state["managed"].pop("metadata")),
            ("extra managed key", lambda state: state["managed"].update(future=True)),
            ("missing previous key", lambda state: state["previous"].pop("mode")),
            ("extra previous key", lambda state: state["previous"].update(future=True)),
            ("unmarked mode", lambda state: state["managed"].update(mode="mode")),
            ("marker prefix", lambda state: state["managed"].update(mode=f"{STATE.MANAGED_MARKER} forged\nbody")),
            ("marker only", lambda state: state["managed"].update(mode=STATE.MANAGED_MARKER)),
            ("empty marker body", lambda state: state["managed"].update(mode=f"{STATE.MANAGED_MARKER}\n  ")),
            ("wrong metadata", lambda state: state["managed"].update(metadata=0)),
            ("wrong namespace", lambda state: state["managed"].update(namespace="other")),
            ("snapshot non-object", lambda state: state["previous"].update(mode=None)),
            ("snapshot known non-bool", lambda state: state["previous"].update(mode={"known": 1, "present": False})),
            ("snapshot present non-bool", lambda state: state["previous"].update(mode={"known": True, "present": 0})),
            ("snapshot unknown present", lambda state: state["previous"].update(mode={"known": False, "present": True, "value": "x"})),
            ("snapshot absent extra", lambda state: state["previous"].update(mode={"known": True, "present": False, "value": "x"})),
            ("snapshot present missing value", lambda state: state["previous"].update(mode={"known": True, "present": True})),
            ("snapshot wrong value type", lambda state: state["previous"].update(metadata={"known": True, "present": True, "value": 1})),
            ("executor null", lambda state: state.__setitem__("executor", None)),
            ("executor Fable", lambda state: state.__setitem__("executor", fable_route())),
            ("designer Fable", lambda state: state.__setitem__("designer", fable_route())),
            ("designer agent", lambda state: state.__setitem__("designer", {"kind": "agent", "agent": "designer_agent"})),
            ("model route missing effort", lambda state: state["executor"].pop("effort")),
            ("model route extra key", lambda state: state["executor"].update(future=True)),
            ("model route bad model", lambda state: state["executor"].update(model="bad model")),
            ("model route bad effort", lambda state: state["executor"].update(effort="bad effort")),
            ("agent route bad name", lambda state: state.__setitem__("executor", {"kind": "agent", "agent": "Bad-Agent"})),
            ("Fable wrong model", lambda state: state["planner"].update(model="claude-other")),
            ("Fable wrong effort", lambda state: state["planner"].update(effort="ultra")),
            ("Fable wrong server", lambda state: state["planner"].update(server="future-server")),
            ("Fable extra route key", lambda state: state["planner"].update(future=True)),
            ("same direct model", lambda state: state.update(planner={"kind": "model", "model": "same", "effort": "high"}, advisor={"kind": "model", "model": "same", "effort": "low"})),
            ("same agent", lambda state: state.update(planner={"kind": "agent", "agent": "same_agent"}, advisor={"kind": "agent", "agent": "same_agent"})),
            ("two Fable seats", lambda state: state.__setitem__("advisor", fable_route())),
            ("managed MCP missing pair", lambda state: state["previous"].pop("mcp")),
            ("previous MCP missing pair", lambda state: state["managed"].pop("mcp")),
            ("empty MCP", lambda state: (state["managed"].update(mcp={}), state["previous"].update(mcp={}))),
            ("unsupported MCP key", lambda state: (state["managed"]["mcp"].update(future=False), state["previous"]["mcp"].update(future=snapshot()))),
            ("unpaired MCP key", lambda state: state["previous"]["mcp"].pop("fable-advisor-python")),
            ("MCP value integer", lambda state: state["managed"]["mcp"].update({"fable-advisor-python": 0})),
            ("MCP snapshot wrong type", lambda state: state["previous"]["mcp"].update({"fable-advisor-python": snapshot(0, present=True)})),
            ("selected launcher disabled", lambda state: state["managed"]["mcp"].update({"fable-advisor-python3": False})),
            ("two launchers enabled", lambda state: state["managed"]["mcp"].update({"fable-advisor-python": True})),
            ("launcher without Fable", lambda state: state.__setitem__("planner", {"kind": "model", "model": "planner", "effort": "high"})),
            ("scalar origin integer", lambda state: state.__setitem__("scalar_origin", 1)),
            ("null scalar forged table", lambda state: state.__setitem__("managed_feature", {})),
            ("boolean scalar missing table", lambda state: state.update(scalar_origin=False, managed_feature=None)),
            ("scalar enabled integer", lambda state: state.update(scalar_origin=True, managed_feature={"enabled": 1, "hide_spawn_agent_metadata": False, "tool_namespace": "agents", "multi_agent_mode_hint_text": state["managed"]["mode"], "usage_hint_text": state["managed"]["usage"]})),
            ("scalar enabled float", lambda state: state.update(scalar_origin=True, managed_feature={"enabled": 1.0, "hide_spawn_agent_metadata": False, "tool_namespace": "agents", "multi_agent_mode_hint_text": state["managed"]["mode"], "usage_hint_text": state["managed"]["usage"]})),
            ("scalar metadata integer", lambda state: state.update(scalar_origin=True, managed_feature={"enabled": True, "hide_spawn_agent_metadata": 0, "tool_namespace": "agents", "multi_agent_mode_hint_text": state["managed"]["mode"], "usage_hint_text": state["managed"]["usage"]})),
            ("scalar table extra key", lambda state: state.update(scalar_origin=True, managed_feature={"enabled": True, "hide_spawn_agent_metadata": False, "tool_namespace": "agents", "multi_agent_mode_hint_text": state["managed"]["mode"], "usage_hint_text": state["managed"]["usage"], "future": True})),
            ("legacy future Planner field", lambda state: (state.update(schema=2, policy_version=2), state.__setitem__("planner", None))),
            ("legacy future Designer field", lambda state: (state.update(schema=3, policy_version=3), state.__setitem__("designer", None))),
            ("future nested snapshot field", lambda state: state["previous"]["mode"].update(future=True)),
        ]

        for label, mutate in mutations:
            with self.subTest(label=label):
                state = deepcopy(baseline)
                mutate(state)
                with self.assertRaises(STATE.RoutingStateError):
                    STATE.validate_routing_state(state)

    def test_legacy_schemas_reject_future_surfaces(self) -> None:
        scenarios = []
        schema_one = genuine_state(1)
        schema_one["planner"] = None
        scenarios.append(("schema 1 planner", schema_one))
        schema_one = genuine_state(1)
        schema_one["advisor"] = fable_route()
        scenarios.append(("schema 1 Fable", schema_one))
        schema_one = genuine_state(1)
        schema_one["managed"]["mcp"] = {"fable-advisor-python3": False}
        schema_one["previous"]["mcp"] = {"fable-advisor-python3": snapshot()}
        scenarios.append(("schema 1 MCP", schema_one))
        schema_two = genuine_state(2)
        schema_two["planner"] = None
        scenarios.append(("schema 2 planner", schema_two))
        for schema in (1, 2, 3):
            legacy = genuine_state(schema)
            legacy["designer"] = None
            scenarios.append((f"schema {schema} designer", legacy))

        for label, state in scenarios:
            with self.subTest(label=label), self.assertRaises(STATE.RoutingStateError):
                STATE.validate_routing_state(state)


if __name__ == "__main__":
    unittest.main()
