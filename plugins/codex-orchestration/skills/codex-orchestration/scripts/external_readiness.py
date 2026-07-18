#!/usr/bin/env python3
"""Fail-closed readiness states for Codex-Orchestration external routes."""

from __future__ import annotations

from enum import Enum
from typing import Final


class ReadinessError(ValueError):
    """A readiness transition or runtime identity claim is unsupported."""


class Readiness(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    PROVIDER_DECLARED = "PROVIDER_DECLARED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTH_READY = "AUTH_READY"
    CAPABILITY_VERIFIED = "CAPABILITY_VERIFIED"
    ROLE_STAGED = "ROLE_STAGED"
    RESTART_REQUIRED = "RESTART_REQUIRED"
    READY = "READY"
    ROUTE_ACCEPTED = "ROUTE_ACCEPTED"
    USED_CONFIRMED = "USED_CONFIRMED"
    CLI_CHANGED = "CLI_CHANGED"
    CONFIG_DRIFT = "CONFIG_DRIFT"
    ROLE_COLLISION = "ROLE_COLLISION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


BLOCKING_STATES: Final[frozenset[Readiness]] = frozenset(
    {
        Readiness.CLI_CHANGED,
        Readiness.CONFIG_DRIFT,
        Readiness.ROLE_COLLISION,
        Readiness.RECOVERY_REQUIRED,
        Readiness.UNSUPPORTED,
    }
)

_FORWARD: Final[dict[Readiness, frozenset[Readiness]]] = {
    Readiness.UNCONFIGURED: frozenset(
        {Readiness.PROVIDER_DECLARED, Readiness.UNSUPPORTED}
    ),
    Readiness.PROVIDER_DECLARED: frozenset(
        {
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.AUTH_READY,
            Readiness.CONFIG_DRIFT,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.AUTH_REQUIRED: frozenset(
        {Readiness.AUTH_READY, Readiness.CONFIG_DRIFT, Readiness.UNSUPPORTED}
    ),
    Readiness.LOGIN_REQUIRED: frozenset(
        {
            Readiness.AUTH_READY,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.AUTH_READY: frozenset(
        {
            Readiness.CAPABILITY_VERIFIED,
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.CAPABILITY_VERIFIED: frozenset(
        {
            Readiness.ROLE_STAGED,
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.ROLE_COLLISION,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.ROLE_STAGED: frozenset(
        {
            Readiness.RESTART_REQUIRED,
            Readiness.AUTH_REQUIRED,
            Readiness.CAPABILITY_VERIFIED,
            Readiness.CONFIG_DRIFT,
            Readiness.ROLE_COLLISION,
            Readiness.RECOVERY_REQUIRED,
        }
    ),
    Readiness.RESTART_REQUIRED: frozenset(
        {
            Readiness.READY,
            Readiness.AUTH_REQUIRED,
            Readiness.CAPABILITY_VERIFIED,
            Readiness.CONFIG_DRIFT,
            Readiness.ROLE_COLLISION,
            Readiness.RECOVERY_REQUIRED,
        }
    ),
    Readiness.READY: frozenset(
        {
            Readiness.ROLE_STAGED,
            Readiness.ROUTE_ACCEPTED,
            Readiness.CAPABILITY_VERIFIED,
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.ROLE_COLLISION,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.ROUTE_ACCEPTED: frozenset(
        {
            Readiness.USED_CONFIRMED,
            Readiness.ROLE_STAGED,
            Readiness.CAPABILITY_VERIFIED,
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.USED_CONFIRMED: frozenset(
        {
            Readiness.ROLE_STAGED,
            Readiness.CAPABILITY_VERIFIED,
            Readiness.AUTH_REQUIRED,
            Readiness.LOGIN_REQUIRED,
            Readiness.CLI_CHANGED,
            Readiness.CONFIG_DRIFT,
            Readiness.UNSUPPORTED,
        }
    ),
    Readiness.CLI_CHANGED: frozenset(
        {Readiness.AUTH_READY, Readiness.UNSUPPORTED}
    ),
    Readiness.CONFIG_DRIFT: frozenset(
        {Readiness.RECOVERY_REQUIRED, Readiness.UNSUPPORTED}
    ),
    Readiness.ROLE_COLLISION: frozenset(
        {Readiness.RESTART_REQUIRED, Readiness.UNSUPPORTED}
    ),
    Readiness.RECOVERY_REQUIRED: frozenset(
        {Readiness.PROVIDER_DECLARED, Readiness.UNCONFIGURED, Readiness.UNSUPPORTED}
    ),
    Readiness.UNSUPPORTED: frozenset(),
}

MECHANICAL_IDENTITY_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "app_server_event",
        "provider_response_metadata",
        "rollout_metadata",
        "subscription_cli_runtime",
    }
)


def parse_readiness(value: object) -> Readiness:
    if type(value) is not str:
        raise ReadinessError("readiness state must be a string")
    try:
        return Readiness(value)
    except ValueError as exc:
        raise ReadinessError(f"unsupported readiness state: {value!r}") from exc


def transition(current: Readiness | str, target: Readiness | str) -> Readiness:
    """Validate one explicit transition and return its target."""

    source = current if isinstance(current, Readiness) else parse_readiness(current)
    destination = target if isinstance(target, Readiness) else parse_readiness(target)
    if destination not in _FORWARD[source]:
        raise ReadinessError(
            f"illegal external-route transition: {source.value} -> {destination.value}"
        )
    return destination


def runtime_identity_state(
    *, route_accepted: bool, evidence_source: str | None
) -> Readiness:
    """Return the strongest honest runtime claim available to the plugin."""

    if not route_accepted:
        raise ReadinessError("runtime identity cannot be claimed before route acceptance")
    if evidence_source is None:
        return Readiness.ROUTE_ACCEPTED
    if evidence_source not in MECHANICAL_IDENTITY_SOURCES:
        raise ReadinessError("runtime identity evidence is not mechanical")
    return Readiness.USED_CONFIRMED


def legal_targets(state: Readiness | str) -> frozenset[Readiness]:
    source = state if isinstance(state, Readiness) else parse_readiness(state)
    return _FORWARD[source]
