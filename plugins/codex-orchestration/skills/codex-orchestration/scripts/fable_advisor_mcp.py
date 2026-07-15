#!/usr/bin/env python3
"""Root-directed, no-tools MCP bridge from Codex to Claude Fable 5.

The managed policy reserves stateless Planner and Advisor operations for the
root; MCP requests do not carry caller identity, so the server cannot enforce
that caller boundary. Each model call reloads and authorizes its seat from
routing state, rechecks first-party Claude Code authentication, and uses a fresh
no-tools/no-persistence process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
from typing import Any, Literal

import routing_state


STATE_FILENAME = ".codex-orchestration-routing.json"
MANAGED_MARKER = routing_state.MANAGED_MARKER
FABLE_MODEL = routing_state.FABLE_MODEL
FABLE_SERVERS = routing_state.FABLE_SERVERS
SUPPORTED_EFFORTS = routing_state.FABLE_EFFORTS
# Claude Code currently reports this exact internal helper alongside Fable for
# some calls. Keep the runtime policy explicit and fail closed if that identity
# rotates or any other model appears.
FABLE_HELPER_MODEL = "claude-haiku-4-5-20251001"
ALLOWED_RUNTIME_MODELS = frozenset({FABLE_MODEL, FABLE_HELPER_MODEL})
CLAUDE_TIMEOUT_SECONDS = 600
AUTH_TIMEOUT_SECONDS = 20
# Applies to the combined user-controlled text sent by one model operation.
MAX_INPUT_CHARS = 200_000
SENSITIVE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}

FABLE_OUTCOME_CODES = frozenset(
    {
        "AUTH_UNAVAILABLE",
        "MODEL_UNAVAILABLE",
        "TRANSPORT_FAILURE",
        "INVALID_RESULT",
        "IDENTITY_MISMATCH",
        "STATE_INVALID",
        "UNKNOWN",
        "DELIVERABLE_VALID",
    }
)
_STARTED_ELIGIBLE_CODES = frozenset({"TRANSPORT_FAILURE", "INVALID_RESULT"})

ADVISOR_SYSTEM_PROMPT = """You are Claude Fable 5 acting only as a plan advisor to Codex's root orchestrator.
Review the supplied self-contained packet for material correctness, missing constraints, unsafe sequencing, ownership conflicts, and verification gaps. Do not edit files, call tools, spawn agents, contact the Planner or executors, or attempt implementation.

Your first non-empty line must be exactly PLAN_APPROVED or PLAN_REVISE.
Use PLAN_APPROVED only when no material gap is present. Use PLAN_REVISE when correction is needed. For PLAN_REVISE, assign every material finding a stable, unique finding ID and give a concrete correction. On later rounds, preserve IDs from the supplied cumulative ledger. Ignore style preferences. Report only to the root orchestrator."""

PLANNER_CREATE_SYSTEM_PROMPT = """You are Claude Fable 5 acting only as a plan author for Codex's root orchestrator.
Create a concrete implementation plan from the supplied self-contained packet. Include constraints, ownership, sequencing, acceptance criteria, security and compatibility boundaries, and behavioral plus regression verification. Do not edit files, call tools, spawn agents, contact the Advisor or executors, or attempt implementation.

Your first non-empty line must be exactly PLAN_DRAFT. Return the complete draft plan after that signal. Report only to the root orchestrator."""

PLANNER_REVISE_SYSTEM_PROMPT = """You are Claude Fable 5 acting only as a stateless plan reviser for Codex's root orchestrator.
Revise the supplied canonical current plan using the original task, its source plan version, the latest Advisor critique, and the compact cumulative history. Do not edit files, call tools, spawn agents, contact the Advisor or executors, or attempt implementation.

Your response must use exactly this top-level structure:
PLAN_REVISION

## FINDINGS_LEDGER
For every finding in the latest critique, include its stable Advisor finding ID exactly once and mark it INCORPORATED or REJECTED. Give a concrete reason for either disposition. Preserve relevant cumulative-history IDs.

## REVISED_PLAN
Provide the complete revised plan, clearly identifying its source plan version and revised version.

Both sections must be non-empty. Your first non-empty line must be exactly PLAN_REVISION. The root orchestrator, not you, validates finding coverage and plan-version semantics. Report only to the root orchestrator."""

# Backward-compatible public constant for existing importers.
SYSTEM_PROMPT = ADVISOR_SYSTEM_PROMPT

Seat = Literal["planner", "advisor"]


class AdvisorError(RuntimeError):
    """Fail-closed error for any Fable bridge operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "UNKNOWN",
        authenticated: bool = False,
        identity_matched: bool = False,
        mechanically_no_tools: bool = False,
        invocation_started: bool = False,
        deliverable_valid: bool = False,
        activation_id: str | None = None,
        candidate_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = classify_fable_outcome(
            code=code,
            authenticated=authenticated,
            identity_matched=identity_matched,
            mechanically_no_tools=mechanically_no_tools,
            invocation_started=invocation_started,
            deliverable_valid=deliverable_valid,
        )
        self.outcome.update(
            {
                "activation_id": activation_id,
                "candidate_index": candidate_index,
                "authenticated": authenticated,
                "identity_matched": identity_matched,
                "mechanically_no_tools": mechanically_no_tools,
                "invocation_started": invocation_started,
                "deliverable_valid": deliverable_valid,
            }
        )

    def with_activation(self, activation_id: str, candidate_index: int) -> AdvisorError:
        """Attach the bridge-derived activation provenance without echoing input."""

        self.outcome["activation_id"] = activation_id
        self.outcome["candidate_index"] = candidate_index
        return self


def candidate_activation_id(seat: Seat, candidate_index: int, route: dict[str, str]) -> str:
    """Return the redacted deterministic activation identity for one candidate."""

    try:
        return routing_state.candidate_activation_id(seat, candidate_index, route)
    except routing_state.RoutingStateError as exc:
        raise AdvisorError(
            "Invalid Fable candidate authorization.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        ) from exc


def classify_fable_outcome(
    *,
    code: str,
    authenticated: bool,
    identity_matched: bool,
    mechanically_no_tools: bool,
    invocation_started: bool,
    deliverable_valid: bool,
) -> dict[str, Any]:
    """Apply the exhaustive bridge/policy failover classification matrix."""

    valid_flags = all(
        type(value) is bool
        for value in (
            authenticated,
            identity_matched,
            mechanically_no_tools,
            invocation_started,
            deliverable_valid,
        )
    )
    if code not in FABLE_OUTCOME_CODES or not valid_flags:
        return {"code": code, "eligible": False, "state": "STATE_UNKNOWN"}
    if code == "DELIVERABLE_VALID":
        valid = (
            authenticated
            and identity_matched
            and mechanically_no_tools
            and invocation_started
            and deliverable_valid
        )
        return {
            "code": code,
            "eligible": False,
            "state": "DELIVERABLE_VALID" if valid else "STATE_UNKNOWN",
        }
    if deliverable_valid:
        return {"code": code, "eligible": False, "state": "STATE_UNKNOWN"}
    if code in {"IDENTITY_MISMATCH", "STATE_INVALID"}:
        return {"code": code, "eligible": False, "state": "STATE_UNKNOWN"}
    if code == "AUTH_UNAVAILABLE":
        eligible = (
            not invocation_started
            and not authenticated
            and identity_matched
            and mechanically_no_tools
        )
        return {
            "code": code,
            "eligible": eligible,
            "state": "ELIGIBLE_PRESTART" if eligible else "STATE_UNKNOWN",
        }
    if code == "MODEL_UNAVAILABLE":
        eligible = (
            not invocation_started
            and authenticated
            and identity_matched
            and mechanically_no_tools
        )
        return {
            "code": code,
            "eligible": eligible,
            "state": "ELIGIBLE_PRESTART" if eligible else "STATE_UNKNOWN",
        }
    elif code in _STARTED_ELIGIBLE_CODES:
        eligible = (
            invocation_started
            and authenticated
            and identity_matched
            and mechanically_no_tools
        )
        return {
            "code": code,
            "eligible": eligible,
            "state": "ELIGIBLE_STARTED" if eligible else "STATE_UNKNOWN",
        }
    return {"code": code, "eligible": False, "state": "STATE_UNKNOWN"}


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser() if value else Path.home() / ".codex"


def sanitized_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in SENSITIVE_ENV:
        env.pop(name, None)
    return env


def resolve_claude() -> Path:
    found = shutil.which("claude")
    if found:
        return Path(found).resolve()
    candidates = (
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AdvisorError(
        "Claude Code is not installed or `claude` is not on PATH.",
        code="AUTH_UNAVAILABLE",
        identity_matched=True,
        mechanically_no_tools=True,
    )


def _run_json(command: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            env=sanitized_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdvisorError(
            "Claude Code authentication check timed out.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        ) from exc
    except OSError as exc:
        raise AdvisorError(
            "Could not run Claude Code authentication check.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        ) from exc
    if result.returncode != 0:
        raise AdvisorError(
            f"Claude Code authentication check exited with {result.returncode}; output withheld.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError(
            "Claude Code returned malformed JSON.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        ) from exc
    if not isinstance(payload, dict):
        raise AdvisorError(
            "Claude Code returned an unexpected JSON value.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        )
    return payload


def check_claude_auth(claude: Path | None = None) -> dict[str, str]:
    executable = claude or resolve_claude()
    payload = _run_json([str(executable), "auth", "status"], timeout=AUTH_TIMEOUT_SECONDS)
    subscription = payload.get("subscriptionType")
    if not (
        payload.get("loggedIn") is True
        and payload.get("authMethod") == "claude.ai"
        and payload.get("apiProvider") == "firstParty"
        and subscription in {"pro", "max"}
    ):
        raise AdvisorError(
            "Claude Code must be logged in through a first-party Pro or Max account; "
            "run `claude auth login` and try again.",
            code="AUTH_UNAVAILABLE",
            identity_matched=True,
            mechanically_no_tools=True,
        )
    return {"auth_method": "claude.ai", "api_provider": "firstParty"}


def _read_routing_state(home: Path | None = None) -> dict[str, Any]:
    root = home or codex_home()
    path = root / STATE_FILENAME
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AdvisorError(
                "The saved routing state is not a regular file.",
                code="STATE_INVALID",
                mechanically_no_tools=True,
            )
        if info.st_nlink != 1:
            raise AdvisorError(
                "The saved routing state has multiple hard links.",
                code="STATE_INVALID",
                mechanically_no_tools=True,
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdvisorError(
            "Claude Fable 5 is not configured; run setup first.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError(
            "Could not read valid routing state.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        ) from exc
    try:
        state = routing_state.validate_routing_state(payload)
    except routing_state.RoutingStateError as exc:
        raise AdvisorError(
            "The saved routing state is invalid.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        ) from exc
    config_file = state["config_file"]
    try:
        belongs_to_home = (
            Path(config_file).expanduser().resolve()
            == (root / "config.toml").expanduser().resolve()
        )
    except (OSError, RuntimeError) as exc:
        raise AdvisorError(
            "The saved routing state belongs to another Codex home.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        ) from exc
    if not belongs_to_home:
        raise AdvisorError(
            "The saved routing state belongs to another Codex home.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        )
    return state


def _validate_seat(seat: str) -> Seat:
    if seat not in {"planner", "advisor"}:
        raise AdvisorError(
            "Fable seat must be `planner` or `advisor`.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        )
    return seat  # type: ignore[return-value]


def _validate_fable_route(route: Any, *, seat: Seat) -> dict[str, str]:
    if not isinstance(route, dict) or route.get("kind") != "fable":
        raise AdvisorError(
            f"Claude Fable 5 is not the configured {seat}.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        )
    return {
        "kind": "fable",
        "model": route["model"],
        "effort": route["effort"],
        "server": route["server"],
    }


def load_fable_route(
    home: Path | None = None,
    *,
    seat: str = "advisor",
    candidate_index: int = 0,
    activation_id: str | None = None,
) -> dict[str, str]:
    """Load and validate one explicitly authorized Fable seat.

    ``seat`` defaults to Advisor for compatibility with the original bridge.
    It is deliberately constrained and resolved from disk on every invocation.
    """

    selected = _validate_seat(seat)
    payload = _read_routing_state(home)
    if type(candidate_index) is not int or candidate_index < 0:
        raise AdvisorError(
            "Fable candidate index must be a non-negative integer.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        )
    backups = payload.get("backups", {})
    candidates = [payload.get(selected)]
    if isinstance(backups, dict):
        saved = backups.get(selected, [])
        if isinstance(saved, list):
            candidates.extend(saved)
    if candidate_index >= len(candidates):
        raise AdvisorError(
            "Fable candidate index is not configured for this seat.",
            code="STATE_INVALID",
            mechanically_no_tools=True,
        )
    route = _validate_fable_route(candidates[candidate_index], seat=selected)
    expected_activation = candidate_activation_id(selected, candidate_index, route)
    if (payload["schema"] >= 4 or activation_id is not None) and activation_id != expected_activation:
        raise AdvisorError(
            "Fable activation identity does not match the configured candidate.",
            code="IDENTITY_MISMATCH",
            mechanically_no_tools=True,
            candidate_index=candidate_index,
        )
    route["activation_id"] = expected_activation
    route["candidate_index"] = str(candidate_index)
    return route


def _validate_inputs(operation: str, **values: Any) -> dict[str, str]:
    checked: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise AdvisorError(f"`{name}` must be a non-empty string for {operation}.")
        checked[name] = value
    if sum(len(value) for value in checked.values()) > MAX_INPUT_CHARS:
        raise AdvisorError(
            f"{operation} input exceeds the {MAX_INPUT_CHARS}-character combined limit."
        )
    return checked


def _first_non_empty_line(response: str) -> str:
    return next((line.strip() for line in response.splitlines() if line.strip()), "")


def _validate_runtime_models(usage: Any) -> list[str]:
    raw_models = list(usage) if isinstance(usage, dict) else []
    if not all(isinstance(model, str) for model in raw_models):
        raise AdvisorError(
            "Runtime metadata reported a model outside the allowed Fable runtime policy.",
            code="IDENTITY_MISMATCH",
            authenticated=True,
            mechanically_no_tools=True,
            invocation_started=True,
        )
    used_models = sorted(raw_models)
    if FABLE_MODEL not in used_models:
        raise AdvisorError(
            "Runtime metadata did not confirm the pinned Claude Fable 5 primary model.",
            code="IDENTITY_MISMATCH",
            authenticated=True,
            mechanically_no_tools=True,
            invocation_started=True,
        )
    if not set(used_models).issubset(ALLOWED_RUNTIME_MODELS):
        raise AdvisorError(
            "Runtime metadata reported a model outside the allowed Fable runtime policy.",
            code="IDENTITY_MISMATCH",
            authenticated=True,
            mechanically_no_tools=True,
            invocation_started=True,
        )
    return used_models


def _invoke_fable(
    *,
    operation: str,
    seat: Seat,
    prompt: str,
    system_prompt: str,
    allowed_signals: set[str],
    activation_id: str | None = None,
    candidate_index: int = 0,
) -> tuple[str, str, dict[str, str], dict[str, str], list[str]]:
    """Run one stateless, seat-authorized, no-tools Fable operation."""

    route = load_fable_route(
        seat=seat,
        candidate_index=candidate_index,
        activation_id=activation_id,
    )
    try:
        claude = resolve_claude()
        auth = check_claude_auth(claude)
    except AdvisorError as exc:
        raise exc.with_activation(route["activation_id"], candidate_index)
    command = [
        str(claude),
        "-p",
        "--model",
        route["model"],
        "--effort",
        route["effort"],
        "--safe-mode",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--prompt-suggestions",
        "false",
        "--output-format",
        "json",
        "--system-prompt",
        system_prompt,
    ]
    try:
        result = subprocess.run(
            command,
            input=prompt,
            env=sanitized_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdvisorError(
            f"Claude Fable 5 {operation} timed out.",
            code="TRANSPORT_FAILURE",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        ) from exc
    except OSError as exc:
        raise AdvisorError(
            f"Could not start Claude Fable 5 {operation}.",
            code="MODEL_UNAVAILABLE",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        ) from exc
    if result.returncode != 0:
        raise AdvisorError(
            f"Claude Fable 5 {operation} exited with {result.returncode}; output withheld.",
            code="TRANSPORT_FAILURE",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError(
            f"Claude Fable 5 {operation} returned malformed JSON.",
            code="INVALID_RESULT",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
        raise AdvisorError(
            f"Claude Fable 5 {operation} returned an unexpected response.",
            code="INVALID_RESULT",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        )
    # Authorize the complete runtime identity set before interpreting or
    # returning any model-authored plan/review content.
    try:
        used_models = _validate_runtime_models(payload.get("modelUsage"))
    except AdvisorError as exc:
        raise exc.with_activation(route["activation_id"], candidate_index)
    response = payload["result"].strip()
    signal = _first_non_empty_line(response)
    if signal not in allowed_signals:
        if operation == "plan review":
            raise AdvisorError(
                "Claude Fable 5 omitted the required plan decision.",
                code="INVALID_RESULT",
                authenticated=True,
                identity_matched=True,
                mechanically_no_tools=True,
                invocation_started=True,
                activation_id=route["activation_id"],
                candidate_index=candidate_index,
            )
        expected = " or ".join(sorted(allowed_signals))
        raise AdvisorError(
            f"Claude Fable 5 {operation} omitted the required {expected} signal.",
            code="INVALID_RESULT",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        )
    return signal, response, route, auth, used_models


def _base_result(
    *, route: dict[str, str], auth: dict[str, str], used_models: list[str]
) -> dict[str, Any]:
    return {
        # ``model`` is the route's pinned primary identity; ``used_models``
        # preserves every runtime-reported model, including an allowed helper.
        "model": FABLE_MODEL,
        "effort": route["effort"],
        "auth_method": auth["auth_method"],
        "used_models": used_models,
        "outcome": {
            **classify_fable_outcome(
                code="DELIVERABLE_VALID",
                authenticated=True,
                identity_matched=True,
                mechanically_no_tools=True,
                invocation_started=True,
                deliverable_valid=True,
            ),
            "activation_id": route.get("activation_id"),
            "candidate_index": int(route.get("candidate_index", "0")),
            "authenticated": True,
            "identity_matched": True,
            "mechanically_no_tools": True,
            "invocation_started": True,
            "deliverable_valid": bool(used_models),
        },
    }


def create_plan(
    packet: str,
    *,
    activation_id: str | None = None,
    candidate_index: int = 0,
) -> dict[str, Any]:
    values = _validate_inputs("plan creation", packet=packet)
    signal, response, route, auth, used_models = _invoke_fable(
        operation="plan creation",
        seat="planner",
        prompt=values["packet"],
        system_prompt=PLANNER_CREATE_SYSTEM_PROMPT,
        allowed_signals={"PLAN_DRAFT"},
        activation_id=activation_id,
        candidate_index=candidate_index,
    )
    return {
        "signal": signal,
        "plan": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def _validate_revision_structure(response: str) -> None:
    lines = response.splitlines()
    ledger_positions = [
        i for i, line in enumerate(lines) if line.strip() == "## FINDINGS_LEDGER"
    ]
    plan_positions = [
        i for i, line in enumerate(lines) if line.strip() == "## REVISED_PLAN"
    ]
    if len(ledger_positions) != 1 or len(plan_positions) != 1:
        raise AdvisorError(
            "Claude Fable 5 plan revision must contain exactly one FINDINGS_LEDGER "
            "and one REVISED_PLAN section."
        )
    ledger_index = ledger_positions[0]
    plan_index = plan_positions[0]
    if ledger_index >= plan_index:
        raise AdvisorError(
            "Claude Fable 5 plan revision sections are in the wrong order."
        )
    ledger = "\n".join(lines[ledger_index + 1 : plan_index]).strip()
    revised_plan = "\n".join(lines[plan_index + 1 :]).strip()
    if not ledger or not revised_plan:
        raise AdvisorError(
            "Claude Fable 5 plan revision has an empty FINDINGS_LEDGER or REVISED_PLAN section."
        )


def revise_plan(
    task: str,
    current_plan: str,
    critique: str,
    history: str,
    *,
    activation_id: str | None = None,
    candidate_index: int = 0,
) -> dict[str, Any]:
    values = _validate_inputs(
        "plan revision",
        task=task,
        current_plan=current_plan,
        critique=critique,
        history=history,
    )
    prompt = "\n\n".join(
        (
            "# ORIGINAL_TASK\n" + values["task"],
            "# CANONICAL_CURRENT_PLAN_WITH_SOURCE_VERSION\n" + values["current_plan"],
            "# LATEST_ADVISOR_CRITIQUE_WITH_STABLE_FINDING_IDS\n" + values["critique"],
            "# COMPACT_CUMULATIVE_FINDINGS_HISTORY\n" + values["history"],
        )
    )
    signal, response, route, auth, used_models = _invoke_fable(
        operation="plan revision",
        seat="planner",
        prompt=prompt,
        system_prompt=PLANNER_REVISE_SYSTEM_PROMPT,
        allowed_signals={"PLAN_REVISION"},
        activation_id=activation_id,
        candidate_index=candidate_index,
    )
    try:
        _validate_revision_structure(response)
    except AdvisorError as exc:
        raise AdvisorError(
            str(exc),
            code="INVALID_RESULT",
            authenticated=True,
            identity_matched=True,
            mechanically_no_tools=True,
            invocation_started=True,
            activation_id=route["activation_id"],
            candidate_index=candidate_index,
        ) from exc
    return {
        "signal": signal,
        "revision": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def review_plan(
    packet: str,
    *,
    activation_id: str | None = None,
    candidate_index: int = 0,
) -> dict[str, Any]:
    values = _validate_inputs("plan review", packet=packet)
    signal, response, route, auth, used_models = _invoke_fable(
        operation="plan review",
        seat="advisor",
        prompt=values["packet"],
        system_prompt=ADVISOR_SYSTEM_PROMPT,
        allowed_signals={"PLAN_APPROVED", "PLAN_REVISE"},
        activation_id=activation_id,
        candidate_index=candidate_index,
    )
    return {
        "decision": signal,
        "review": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def _configured_fable_seats() -> dict[str, dict[str, str]]:
    payload = _read_routing_state()
    routes: dict[str, dict[str, str]] = {}
    for seat in ("planner", "advisor"):
        value = payload.get(seat)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise AdvisorError(f"The saved {seat} route is invalid.")
        candidates = [value]
        backups = payload.get("backups", {})
        if isinstance(backups, dict) and isinstance(backups.get(seat), list):
            candidates.extend(backups[seat])
        for candidate_index, candidate in enumerate(candidates):
            if isinstance(candidate, dict) and candidate.get("kind") == "fable":
                activation_id = candidate_activation_id(
                    _validate_seat(seat),
                    candidate_index,
                    candidate,
                )
                routes[seat] = load_fable_route(
                    seat=seat,
                    candidate_index=candidate_index,
                    activation_id=activation_id,
                )
                break
    if not routes:
        raise AdvisorError("Claude Fable 5 is not configured for Planner or Advisor.")
    return routes


def status() -> dict[str, Any]:
    routes = _configured_fable_seats()
    auth = check_claude_auth()
    seats = {
        seat: {
            "model": route["model"],
            "effort": route["effort"],
            "candidate_index": route["candidate_index"],
            "activation_id": route["activation_id"],
        }
        for seat, route in routes.items()
    }
    result: dict[str, Any] = {
        "available": True,
        "configured_seats": list(seats),
        "seats": seats,
        **auth,
    }
    # Preserve the unambiguous legacy Advisor status fields.
    if "advisor" in seats:
        result.update(seats["advisor"])
    return result


def tool_definitions() -> list[dict[str, Any]]:
    annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    string_property = {"type": "string", "maxLength": MAX_INPUT_CHARS}
    return [
        {
            "name": "create_plan",
            "title": "Create a plan with Claude Fable 5",
            "description": "Create one stateless plan draft with the configured Fable Planner.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": {**string_property, "description": "Complete planning packet."},
                    "activation_id": {"type": "string", "maxLength": 512},
                    "candidate_index": {"type": "integer", "minimum": 0},
                },
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "revise_plan",
            "title": "Revise a plan with Claude Fable 5",
            "description": "Create one stateless revision with a findings ledger and complete revised plan.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {**string_property, "description": "Original task."},
                    "current_plan": {**string_property, "description": "Canonical current plan with source version."},
                    "critique": {**string_property, "description": "Latest Advisor critique with stable finding IDs."},
                    "history": {**string_property, "description": "Compact cumulative findings history."},
                    "activation_id": {"type": "string", "maxLength": 512},
                    "candidate_index": {"type": "integer", "minimum": 0},
                },
                "required": ["task", "current_plan", "critique", "history"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "review_plan",
            "title": "Review a plan with Claude Fable 5",
            "description": "Review one self-contained packet with the configured Fable Advisor.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": {**string_property, "description": "Complete context, plan, risks, slices, and checks."},
                    "activation_id": {"type": "string", "maxLength": 512},
                    "candidate_index": {"type": "integer", "minimum": 0},
                },
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "status",
            "title": "Check Claude Fable 5 Planner and Advisor status",
            "description": "Check configured Fable seats and first-party login without a model call.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": annotations,
        },
    ]


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": is_error,
    }


def _tool_arguments(arguments: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AdvisorError("Tool arguments must be an object.")
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise AdvisorError(f"Unexpected tool argument(s): {', '.join(unexpected)}.")
    return arguments


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "codex-orchestration-fable-advisor", "version": "2.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        try:
            if name == "create_plan":
                args = _tool_arguments(arguments, {"packet", "activation_id", "candidate_index"})
                result = _tool_result(
                    create_plan(
                        args.get("packet"),
                        activation_id=args.get("activation_id"),
                        candidate_index=args.get("candidate_index", 0),
                    )
                )
            elif name == "revise_plan":
                args = _tool_arguments(arguments, {"task", "current_plan", "critique", "history", "activation_id", "candidate_index"})
                result = _tool_result(
                    revise_plan(
                        args.get("task"),
                        args.get("current_plan"),
                        args.get("critique"),
                        args.get("history"),
                        activation_id=args.get("activation_id"),
                        candidate_index=args.get("candidate_index", 0),
                    )
                )
            elif name == "review_plan":
                args = _tool_arguments(arguments, {"packet", "activation_id", "candidate_index"})
                result = _tool_result(
                    review_plan(
                        args.get("packet"),
                        activation_id=args.get("activation_id"),
                        candidate_index=args.get("candidate_index", 0),
                    )
                )
            elif name == "status":
                _tool_arguments(arguments, set())
                result = _tool_result(status())
            else:
                raise AdvisorError(f"Unknown tool: {name!r}.")
        except AdvisorError as exc:
            result = _tool_result(
                {
                    "available": False,
                    "error": str(exc),
                    "outcome": exc.outcome,
                },
                is_error=True,
            )
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_request(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
