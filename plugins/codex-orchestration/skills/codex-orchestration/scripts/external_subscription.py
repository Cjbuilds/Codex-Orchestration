#!/usr/bin/env python3
"""Sealed dispatch for audited first-party subscription model adapters."""

from __future__ import annotations

from typing import Any, Callable

import external_providers
import fable_advisor_mcp


FABLE_PROVIDER = "claude-fable"
FABLE_MODEL = "claude-fable-5"
OPERATION_SEATS = {
    "create_plan": "planner",
    "revise_plan": "planner",
    "review_plan": "advisor",
}


class SubscriptionAdapterError(RuntimeError):
    """A subscription route is unavailable or outside its sealed contract."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise SubscriptionAdapterError(detail)


def validate_route(
    provider_id: str,
    model: str,
    effort: str,
    operation: str,
) -> tuple[dict[str, Any], str]:
    """Resolve only manifest-sealed provider/model/effort/operation tuples."""

    _require(provider_id == FABLE_PROVIDER, "subscription provider is not audited")
    provider = external_providers.load_provider(provider_id)
    _require(provider["lane"] == "subscription", "provider is not a subscription adapter")
    _require(model == FABLE_MODEL, "subscription model is not sealed")
    selected_effort = external_providers.resolve_effort(provider, model, effort)
    adapter = provider["subscription_adapter"]
    _require(
        operation in adapter["allowed_operations"],
        "subscription operation is not allowed",
    )
    _require(
        OPERATION_SEATS.get(operation) in adapter["allowed_seats"],
        "subscription seat is not allowed",
    )
    return provider, selected_effort


def status(
    *,
    provider_id: str = FABLE_PROVIDER,
    model: str = FABLE_MODEL,
    effort: str = "high",
    seat: str = "planner",
) -> dict[str, Any]:
    """Check route and first-party login without making a model call."""

    _require(seat in {"planner", "advisor"}, "subscription seat is unsupported")
    operation = "create_plan" if seat == "planner" else "review_plan"
    provider, selected_effort = validate_route(
        provider_id, model, effort, operation
    )
    route = fable_advisor_mcp.load_fable_route(seat=seat)
    _require(route["model"] == model, "configured Fable route model drifted")
    _require(
        route["effort"] == selected_effort,
        "configured Fable effort differs from the requested effort",
    )
    executable = fable_advisor_mcp.resolve_claude()
    auth = fable_advisor_mcp.check_claude_auth(executable)
    return {
        "provider": provider_id,
        "model": model,
        "effort": selected_effort,
        "seat": seat,
        "auth": auth["auth_method"],
        "runtime_identity": provider["runtime_identity"],
        "model_call": False,
    }


def invoke(
    operation: str,
    arguments: dict[str, str],
    *,
    provider_id: str = FABLE_PROVIDER,
    model: str = FABLE_MODEL,
    effort: str = "high",
) -> dict[str, Any]:
    """Dispatch one operation through the existing no-tools Fable bridge."""

    _, selected_effort = validate_route(provider_id, model, effort, operation)
    expected_keys = {
        "create_plan": {"packet"},
        "revise_plan": {"task", "current_plan", "critique", "history"},
        "review_plan": {"packet"},
    }
    _require(
        type(arguments) is dict and set(arguments) == expected_keys[operation],
        "subscription arguments are invalid",
    )
    _require(
        all(type(value) is str and bool(value.strip()) for value in arguments.values()),
        "subscription arguments must be non-empty strings",
    )
    dispatch: dict[str, Callable[..., dict[str, Any]]] = {
        "create_plan": fable_advisor_mcp.create_plan,
        "revise_plan": fable_advisor_mcp.revise_plan,
        "review_plan": fable_advisor_mcp.review_plan,
    }
    result = dispatch[operation](**arguments)
    _require(
        result.get("model") == model,
        "subscription runtime model was not confirmed",
    )
    _require(
        result.get("effort") == selected_effort,
        "subscription runtime effort drifted",
    )
    _require(
        model in result.get("used_models", []),
        "subscription runtime metadata omitted the primary model",
    )
    return result
