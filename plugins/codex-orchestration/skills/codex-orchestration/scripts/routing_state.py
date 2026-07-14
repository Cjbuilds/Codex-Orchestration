#!/usr/bin/env python3
"""Fail-closed validation for persisted Codex-Orchestration routing state.

This module deliberately depends only on the Python standard library so every
packaged entry point can import the same contract validator.
"""

from __future__ import annotations

import re
from typing import Any


MANAGED_MARKER = "[codex-orchestration managed-policy v1]"
ROUTING_TOOL_NAMESPACE = "agents"
FABLE_MODEL = "claude-fable-5"
FABLE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
FABLE_SERVERS = frozenset(
    {
        "fable-advisor-python3",
        "fable-advisor-python",
        "fable-advisor-py",
    }
)

_SCHEMA_POLICY_PAIRS = {1: 1, 2: 2, 3: 3}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,199}$")
_AGENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_BASE_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "policy_version",
        "managed_by",
        "config_file",
        "executor",
        "advisor",
        "managed",
        "previous",
        "scalar_origin",
        "managed_feature",
    }
)
_BASE_MANAGED_KEYS = frozenset({"mode", "usage", "metadata", "namespace"})
_BASE_PREVIOUS_KEYS = frozenset({"mode", "usage", "metadata", "namespace"})


class RoutingStateError(ValueError):
    """The persisted value is not one exact supported routing-state contract."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise RoutingStateError(detail)


def _has_marker_first_line(value: Any) -> bool:
    if type(value) is not str:
        return False
    first_line, separator, body = value.partition("\n")
    return first_line == MANAGED_MARKER and separator == "\n" and bool(body.strip())


def _validate_snapshot(value: Any, expected_type: type) -> None:
    _require(type(value) is dict, "restore snapshot must be an object")
    known = value.get("known")
    present = value.get("present")
    _require(type(known) is bool, "snapshot known must be boolean")
    _require(type(present) is bool, "snapshot present must be boolean")

    if not known:
        _require(
            not present and set(value) == {"known", "present"},
            "unknown snapshot must be exactly absent",
        )
    elif not present:
        _require(
            set(value) == {"known", "present"},
            "absent snapshot has unexpected fields",
        )
    else:
        _require(
            set(value) == {"known", "present", "value"},
            "present snapshot has the wrong shape",
        )
        _require(
            type(value["value"]) is expected_type,
            "present snapshot has the wrong value type",
        )


def _validate_route(route: Any, *, seat: str, schema: int) -> str:
    _require(type(route) is dict, f"{seat} route must be an object")
    kind = route.get("kind")
    _require(type(kind) is str, f"{seat} route kind must be a string")

    if kind == "model":
        _require(
            set(route) == {"kind", "model", "effort"},
            f"{seat} model route has the wrong shape",
        )
        _require(
            type(route["model"]) is str and _MODEL_RE.fullmatch(route["model"]) is not None,
            f"{seat} model route has an invalid model",
        )
        _require(
            type(route["effort"]) is str
            and _EFFORT_RE.fullmatch(route["effort"]) is not None,
            f"{seat} model route has an invalid effort",
        )
    elif kind == "agent":
        _require(
            set(route) == {"kind", "agent"},
            f"{seat} agent route has the wrong shape",
        )
        _require(
            type(route["agent"]) is str and _AGENT_RE.fullmatch(route["agent"]) is not None,
            f"{seat} agent route has an invalid name",
        )
    elif kind == "fable":
        _require(
            seat in {"planner", "advisor"} and schema >= 2,
            f"{seat} cannot use Fable in schema {schema}",
        )
        _require(
            set(route) == {"kind", "model", "effort", "server"},
            f"{seat} Fable route has the wrong shape",
        )
        _require(route["model"] == FABLE_MODEL, "Fable model is not pinned")
        _require(
            type(route["effort"]) is str and route["effort"] in FABLE_EFFORTS,
            "Fable effort is unsupported",
        )
        _require(
            type(route["server"]) is str and route["server"] in FABLE_SERVERS,
            "Fable server is unsupported",
        )
    else:
        raise RoutingStateError(f"{seat} route kind is unsupported")
    return kind


def _validate_route_separation(planner: Any, advisor: Any) -> None:
    if planner is None or advisor is None:
        return
    planner_kind = planner["kind"]
    advisor_kind = advisor["kind"]
    same_route = (
        planner_kind == advisor_kind == "model"
        and planner["model"] == advisor["model"]
    ) or (
        planner_kind == advisor_kind == "agent"
        and planner["agent"] == advisor["agent"]
    ) or planner_kind == advisor_kind == "fable"
    _require(not same_route, "Planner and Advisor routes are not independent")


def _validate_scalar_conversion(state: dict[str, Any], managed: dict[str, Any]) -> None:
    scalar_origin = state["scalar_origin"]
    managed_feature = state["managed_feature"]
    if scalar_origin is None:
        _require(managed_feature is None, "null scalar origin requires null managed feature")
        return

    _require(type(scalar_origin) is bool, "scalar origin must be null or boolean")
    _require(type(managed_feature) is dict, "scalar conversion must save a table")
    _require(
        set(managed_feature)
        == {
            "enabled",
            "hide_spawn_agent_metadata",
            "tool_namespace",
            "multi_agent_mode_hint_text",
            "usage_hint_text",
        },
        "managed scalar conversion table has the wrong shape",
    )
    _require(
        type(managed_feature["enabled"]) is bool
        and managed_feature["enabled"] is scalar_origin,
        "managed scalar conversion enabled value is forged",
    )
    _require(
        type(managed_feature["hide_spawn_agent_metadata"]) is bool
        and managed_feature["hide_spawn_agent_metadata"] is False,
        "managed scalar conversion metadata value is forged",
    )
    _require(
        type(managed_feature["tool_namespace"]) is str
        and managed_feature["tool_namespace"] == ROUTING_TOOL_NAMESPACE,
        "managed scalar conversion namespace is forged",
    )
    _require(
        type(managed_feature["multi_agent_mode_hint_text"]) is str
        and managed_feature["multi_agent_mode_hint_text"] == managed["mode"],
        "managed scalar conversion mode is forged",
    )
    _require(
        type(managed_feature["usage_hint_text"]) is str
        and managed_feature["usage_hint_text"] == managed["usage"],
        "managed scalar conversion usage is forged",
    )


def validate_routing_state(value: Any) -> dict[str, Any]:
    """Validate and return one exact, complete persisted schema 1, 2, or 3.

    Unknown keys and future extensions are rejected intentionally. Callers must
    perform their own secure file read and any caller-specific path/seat checks.
    """

    _require(type(value) is dict, "routing state must be an object")
    schema = value.get("schema")
    policy_version = value.get("policy_version")
    _require(
        type(schema) is int and schema in _SCHEMA_POLICY_PAIRS,
        "schema must be an exact supported integer",
    )
    _require(
        type(policy_version) is int
        and policy_version == _SCHEMA_POLICY_PAIRS[schema],
        "policy version does not match schema",
    )

    expected_top = set(_BASE_TOP_LEVEL_KEYS)
    if schema == 3:
        expected_top.add("planner")
    _require(set(value) == expected_top, "top-level state shape is unsupported")
    _require(value["managed_by"] == "codex-orchestration", "state owner is invalid")
    _require(
        type(value["config_file"]) is str
        and bool(value["config_file"])
        and "\x00" not in value["config_file"],
        "config path is invalid",
    )

    _validate_route(value["executor"], seat="executor", schema=schema)
    planner = value.get("planner")
    advisor = value["advisor"]
    if planner is not None:
        _validate_route(planner, seat="planner", schema=schema)
    if advisor is not None:
        _validate_route(advisor, seat="advisor", schema=schema)
    _validate_route_separation(planner, advisor)

    managed = value["managed"]
    previous = value["previous"]
    _require(type(managed) is dict, "managed state must be an object")
    _require(type(previous) is dict, "previous state must be an object")
    managed_has_mcp = "mcp" in managed
    previous_has_mcp = "mcp" in previous
    _require(managed_has_mcp == previous_has_mcp, "MCP state and restore data must pair")
    _require(not managed_has_mcp or schema >= 2, "schema 1 cannot contain MCP state")

    expected_managed = set(_BASE_MANAGED_KEYS)
    expected_previous = set(_BASE_PREVIOUS_KEYS)
    if managed_has_mcp:
        expected_managed.add("mcp")
        expected_previous.add("mcp")
    _require(set(managed) == expected_managed, "managed state has the wrong shape")
    _require(set(previous) == expected_previous, "restore state has the wrong shape")
    _require(_has_marker_first_line(managed["mode"]), "managed mode marker is invalid")
    _require(_has_marker_first_line(managed["usage"]), "managed usage marker is invalid")
    _require(managed["metadata"] is False, "managed metadata must be false")
    _require(
        managed["namespace"] == ROUTING_TOOL_NAMESPACE,
        "managed namespace is invalid",
    )

    for key, expected_type in (
        ("mode", str),
        ("usage", str),
        ("metadata", bool),
        ("namespace", str),
    ):
        _validate_snapshot(previous[key], expected_type)

    fable_routes = [
        route
        for route in (planner, advisor)
        if type(route) is dict and route.get("kind") == "fable"
    ]
    _require(len(fable_routes) <= 1, "more than one Fable seat is configured")
    if managed_has_mcp:
        managed_mcp = managed["mcp"]
        previous_mcp = previous["mcp"]
        _require(type(managed_mcp) is dict and bool(managed_mcp), "MCP state is empty")
        _require(type(previous_mcp) is dict, "MCP restore state must be an object")
        _require(
            set(managed_mcp) == set(previous_mcp)
            and set(managed_mcp).issubset(FABLE_SERVERS),
            "MCP state has unsupported or unpaired servers",
        )
        _require(
            all(type(enabled) is bool for enabled in managed_mcp.values()),
            "MCP enabled values must be booleans",
        )
        for saved in previous_mcp.values():
            _validate_snapshot(saved, bool)
        true_servers = [server for server, enabled in managed_mcp.items() if enabled]
    else:
        true_servers = []

    if fable_routes:
        selected_server = fable_routes[0]["server"]
        _require(
            true_servers == [selected_server],
            "MCP state must enable exactly the selected Fable launcher",
        )
    else:
        _require(not true_servers, "MCP state enables a launcher without a Fable seat")

    _validate_scalar_conversion(value, managed)
    return value
