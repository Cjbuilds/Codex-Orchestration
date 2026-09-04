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


def fable_route(
    server: str = "fable-advisor-python3", *, model: str = STATE.FABLE_MODEL
) -> dict[str, str]:
    return {
        "kind": "fable",
        "model": model,
        "effort": "high",
        "server": server,
    }


def legacy_fable_route(server: str = "fable-advisor-python3") -> dict[str, str]:
    return fable_route(server, model=STATE.LEGACY_FABLE_MODEL)


def opus_route(server: str = "fable-advisor-python3") -> dict[str, str]:
    return {
        "kind": "claude_subscription",
        "model": STATE.OPUS_MODEL,
        "effort": "xhigh",
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
        state["advisor"] = legacy_fable_route()
    if schema >= 3:
        state["planner"] = (
            fable_route() if schema >= 6 else legacy_fable_route()
        )
    if schema >= 4:
        state["designer"] = {
            "kind": "model",
            "model": "gpt-designer",
            "effort": "high",
        }
    if schema >= 7:
        state["validator"] = None
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
    def test_genuine_schemas_one_through_seven_are_accepted(self) -> None:
        for schema in (1, 2, 3, 4, 5, 6, 7):
            with self.subTest(schema=schema):
                state = genuine_state(schema)
                self.assertIs(STATE.validate_routing_state(state), state)

    def test_schema_seven_accepts_exact_direct_validator_route(self) -> None:
        state = genuine_state(7)
        state["validator"] = {
            "kind": "model",
            "model": "gpt-6-astra",
            "effort": "xhigh",
        }
        self.assertIs(STATE.validate_routing_state(state), state)

    def test_schema_seven_validator_route_is_strict_and_direct_only(self) -> None:
        mutations = {
            "missing validator": lambda state: state.pop("validator"),
            "boolean validator": lambda state: state.update(validator=False),
            "agent route": lambda state: state.update(
                validator={"kind": "agent", "agent": "validator_agent"}
            ),
            "Fable bridge": lambda state: state.update(validator=fable_route()),
            "Opus bridge": lambda state: state.update(validator=opus_route()),
            "missing kind": lambda state: state.update(
                validator={"model": "gpt-6-astra", "effort": "xhigh"}
            ),
            "missing model": lambda state: state.update(
                validator={"kind": "model", "effort": "xhigh"}
            ),
            "missing effort": lambda state: state.update(
                validator={"kind": "model", "model": "gpt-6-astra"}
            ),
            "provider field": lambda state: state["validator"].update(
                provider="openai"
            ),
            "boolean field": lambda state: state["validator"].update(enabled=True),
            "read-only field": lambda state: state["validator"].update(
                readonly=True
            ),
            "round field": lambda state: state["validator"].update(max_rounds=3),
            "non-string kind": lambda state: state["validator"].update(kind=7),
            "non-string model": lambda state: state["validator"].update(model=7),
            "non-string effort": lambda state: state["validator"].update(effort=7),
            "empty model": lambda state: state["validator"].update(model=""),
            "empty effort": lambda state: state["validator"].update(effort=""),
            "reserved Fable model": lambda state: state["validator"].update(
                model=STATE.FABLE_MODEL
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                invalid = genuine_state(7)
                invalid["validator"] = {
                    "kind": "model",
                    "model": "gpt-6-astra",
                    "effort": "xhigh",
                }
                mutate(invalid)
                with self.assertRaises(STATE.RoutingStateError):
                    STATE.validate_routing_state(invalid)

    def test_advisor_and_validator_may_share_a_direct_model_route(self) -> None:
        state = genuine_state(7)
        shared_route = {
            "kind": "model",
            "model": "gpt-6-astra",
            "effort": "xhigh",
        }
        state["advisor"] = deepcopy(shared_route)
        state["validator"] = deepcopy(shared_route)
        self.assertIs(STATE.validate_routing_state(state), state)

        state["planner"] = deepcopy(shared_route)
        with self.assertRaisesRegex(
            STATE.RoutingStateError, "Planner and Advisor routes are not independent"
        ):
            STATE.validate_routing_state(state)

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
            *( (f"schema {value!r}", schema(value)) for value in (True, 1.0, "4", None, 0, 8) ),
            *( (f"policy {value!r}", policy(value)) for value in (True, 4.0, "4", None, 0, 7, 6, 3) ),
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

    def test_schema_five_opus_route_is_sealed_and_exclusive(self) -> None:
        state = genuine_state(5)
        state["planner"] = opus_route()
        self.assertIs(STATE.validate_routing_state(state), state)

        mutations = {
            "Fable cross encoded": lambda value: value["planner"].update(
                model=STATE.LEGACY_FABLE_MODEL
            ),
            "wrong effort": lambda value: value["planner"].update(effort="ultra"),
            "wrong server": lambda value: value["planner"].update(server="future"),
            "extra key": lambda value: value["planner"].update(future=True),
            "executor Opus": lambda value: value.update(executor=opus_route()),
            "designer Opus": lambda value: value.update(designer=opus_route()),
            "mixed subscription seats": lambda value: value.update(
                advisor=legacy_fable_route()
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                invalid = deepcopy(state)
                mutate(invalid)
                with self.assertRaises(STATE.RoutingStateError):
                    STATE.validate_routing_state(invalid)

        legacy = genuine_state(4)
        legacy["planner"] = opus_route()
        with self.assertRaises(STATE.RoutingStateError):
            STATE.validate_routing_state(legacy)

    def test_reserved_claude_models_cannot_use_generic_model_routes(self) -> None:
        seats_by_schema = {
            1: ("executor", "advisor"),
            2: ("executor", "advisor"),
            3: ("executor", "planner", "advisor"),
            4: ("executor", "planner", "advisor", "designer"),
            5: ("executor", "planner", "advisor", "designer"),
            6: ("executor", "planner", "advisor", "designer"),
            7: ("executor", "planner", "advisor", "designer", "validator"),
        }
        for schema, seats in seats_by_schema.items():
            for seat in seats:
                for model in (
                    STATE.LEGACY_FABLE_MODEL,
                    STATE.FABLE_MODEL,
                    STATE.OPUS_MODEL,
                ):
                    with self.subTest(schema=schema, seat=seat, model=model):
                        invalid = genuine_state(schema)
                        invalid[seat] = {
                            "kind": "model",
                            "model": model,
                            "effort": "high",
                        }
                        if not any(
                            isinstance(invalid.get(candidate), dict)
                            and invalid[candidate].get("kind")
                            in {"fable", "claude_subscription"}
                            for candidate in ("planner", "advisor")
                        ):
                            for server in invalid["managed"].get("mcp", {}):
                                invalid["managed"]["mcp"][server] = False
                        with self.assertRaises(STATE.RoutingStateError):
                            STATE.validate_routing_state(invalid)

        for sealed, generic in (
            (legacy_fable_route(), STATE.OPUS_MODEL),
            (opus_route(), STATE.LEGACY_FABLE_MODEL),
        ):
            for sealed_seat, generic_seat in (
                ("planner", "advisor"),
                ("advisor", "planner"),
            ):
                with self.subTest(
                    sealed_model=sealed["model"],
                    sealed_seat=sealed_seat,
                    generic_model=generic,
                    generic_seat=generic_seat,
                ):
                    invalid = genuine_state(5)
                    invalid[sealed_seat] = deepcopy(sealed)
                    invalid[generic_seat] = {
                        "kind": "model",
                        "model": generic,
                        "effort": "high",
                    }
                    with self.assertRaises(STATE.RoutingStateError):
                        STATE.validate_routing_state(invalid)

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
        for schema in (1, 2, 3, 4, 5, 6):
            legacy = genuine_state(schema)
            legacy["validator"] = None
            scenarios.append((f"schema {schema} validator", legacy))

        for label, state in scenarios:
            with self.subTest(label=label), self.assertRaises(STATE.RoutingStateError):
                STATE.validate_routing_state(state)

    def test_schema_six_and_seven_pin_fable_5_1_while_older_schemas_keep_legacy_fable(
        self,
    ) -> None:
        for schema in (1, 2, 3, 4, 5):
            if schema == 1:
                continue
            with self.subTest(schema=schema, model="current Fable"):
                legacy = genuine_state(schema)
                legacy["advisor" if schema == 2 else "planner"] = fable_route()
                with self.assertRaises(STATE.RoutingStateError):
                    STATE.validate_routing_state(legacy)

        for schema in (6, 7):
            with self.subTest(schema=schema, model="legacy Fable"):
                current = genuine_state(schema)
                current["planner"] = legacy_fable_route()
                with self.assertRaises(STATE.RoutingStateError):
                    STATE.validate_routing_state(current)


if __name__ == "__main__":
    unittest.main()
