#!/usr/bin/env python3
"""Root-directed, no-tools MCP bridge to sealed Claude subscription models.

The managed policy reserves stateless Planner and Advisor operations for the
root; MCP requests do not carry caller identity, so the server cannot enforce
that caller boundary. Each model call reloads and authorizes its seat from
routing state, rechecks first-party Claude Code authentication, and uses a fresh
no-tools/no-persistence process.
"""

from __future__ import annotations

import json
import math
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
OPUS_MODEL = routing_state.OPUS_MODEL
FABLE_SERVERS = routing_state.FABLE_SERVERS
SUPPORTED_EFFORTS = routing_state.FABLE_EFFORTS
# Claude Code currently reports this exact internal helper alongside Fable for
# some calls. Keep the runtime policy explicit and fail closed if that identity
# rotates or any other model appears.
FABLE_HELPER_MODEL = "claude-haiku-4-5-20251001"
FABLE_RESOLVED_PRIMARY_MODEL = "claude-opus-4-8"
REVIEWED_PRIMARY_MODELS_BY_ROUTE = {
    FABLE_MODEL: frozenset({FABLE_MODEL, FABLE_RESOLVED_PRIMARY_MODEL}),
    # The resolved Fable identity is not an alias for the separately sealed
    # Opus route. Opus remains primary-only until independently re-qualified.
    OPUS_MODEL: frozenset({OPUS_MODEL}),
}
ALLOWED_RUNTIME_MODELS = frozenset(
    {*REVIEWED_PRIMARY_MODELS_BY_ROUTE[FABLE_MODEL], FABLE_HELPER_MODEL}
)
ALLOWED_RUNTIME_MODELS_BY_PRIMARY = {
    FABLE_MODEL: ALLOWED_RUNTIME_MODELS,
    # No Opus helper identity has been independently verified. Fail closed if
    # Claude Code reports anything beyond the sealed primary.
    OPUS_MODEL: frozenset({OPUS_MODEL}),
}
CLAUDE_TIMEOUT_SECONDS = 600
AUTH_TIMEOUT_SECONDS = 20
# Applies to the combined user-controlled text sent by one model operation.
MAX_INPUT_CHARS = 200_000
PLAN_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {
            "type": "string",
            "enum": ["PLAN_APPROVED", "PLAN_REVISE"],
        },
        "body": {"type": "string", "minLength": 1},
    },
    "required": ["signal", "body"],
    "additionalProperties": False,
}
SENSITIVE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_AWS_WORKSPACE_ID",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_BETAS",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME",
    "ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
    "CLAUDE_CODE_SKIP_MANTLE_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}
STALE_BRIDGE_RECOVERY = (
    "If Codex Orchestration changed after this task started, run fresh native status. "
    "When status reports first-party login ready, fully quit and reopen Codex and "
    "start a new task; do not re-authenticate solely for this loaded-bridge failure."
)

ADVISOR_SYSTEM_PROMPT = """You are the configured Claude model acting only as a plan advisor to Codex's root orchestrator.
Review the supplied self-contained packet for material correctness, missing constraints, unsafe sequencing, ownership conflicts, and verification gaps. Do not edit files, call tools, spawn agents, contact the Planner or executors, or attempt implementation.

Return the required structured fields `signal` and `body`. Use signal PLAN_APPROVED only when no material gap is present. Use PLAN_REVISE when correction is needed. The body must be non-empty. For PLAN_REVISE, assign every material finding a stable, unique finding ID and give a concrete correction. On later rounds, preserve IDs from the supplied cumulative ledger. Ignore style preferences. Report only to the root orchestrator."""

PLANNER_CREATE_SYSTEM_PROMPT = """You are the configured Claude model acting only as a plan author for Codex's root orchestrator.
Create a concrete implementation plan from the supplied self-contained packet. Include constraints, ownership, sequencing, acceptance criteria, security and compatibility boundaries, and behavioral plus regression verification. Do not edit files, call tools, spawn agents, contact the Advisor or executors, or attempt implementation.

Your first non-empty line must be exactly PLAN_DRAFT. Return the complete draft plan after that signal. Report only to the root orchestrator."""

PLANNER_REVISE_SYSTEM_PROMPT = """You are the configured Claude model acting only as a stateless plan reviser for Codex's root orchestrator.
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
    """Fail-closed error for any bundled Claude bridge operation."""


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser() if value else Path.home() / ".codex"


def _canonical_posix_identity() -> str:
    try:
        import pwd

        name = pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError, AttributeError) as exc:
        raise AdvisorError(
            "Could not determine the canonical POSIX login identity for Claude Code."
        ) from exc
    if not isinstance(name, str) or not name.strip():
        raise AdvisorError(
            "Could not determine the canonical POSIX login identity for Claude Code."
        )
    return name


def sanitized_environment() -> dict[str, str]:
    common_names = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
    if os.name == "nt":
        canonical_names = (
            *common_names,
            "SystemRoot",
            "ComSpec",
            "PATHEXT",
            "TEMP",
            "TMP",
            "USERPROFILE",
        )
        inherited = {name.casefold(): value for name, value in os.environ.items()}
        env = {
            canonical: inherited[canonical.casefold()]
            for canonical in canonical_names
            if canonical.casefold() in inherited
        }
    else:
        env = {
            name: os.environ[name]
            for name in (*common_names, "HOME", "TMPDIR")
            if name in os.environ
        }
        identity = _canonical_posix_identity()
        env["USER"] = identity
        env["LOGNAME"] = identity
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
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
    raise AdvisorError("Claude Code is not installed or `claude` is not on PATH.")


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
        raise AdvisorError("Claude Code authentication check timed out.") from exc
    except OSError as exc:
        raise AdvisorError("Could not run Claude Code authentication check.") from exc
    if result.returncode != 0:
        raise AdvisorError(
            f"Claude Code authentication check exited with {result.returncode}; output withheld."
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError("Claude Code returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise AdvisorError("Claude Code returned an unexpected JSON value.")
    return payload


def check_claude_auth(claude: Path | None = None) -> dict[str, str]:
    executable = claude or resolve_claude()
    payload = _run_json(
        [str(executable), "auth", "status", "--json"],
        timeout=AUTH_TIMEOUT_SECONDS,
    )
    subscription = payload.get("subscriptionType")
    if not (
        payload.get("loggedIn") is True
        and payload.get("authMethod") == "claude.ai"
        and payload.get("apiProvider") == "firstParty"
        and isinstance(subscription, str)
        and subscription in {"pro", "max", "team"}
    ):
        raise AdvisorError(
            "Claude Code must be logged in through a first-party Pro, Max, or Team "
            "account; run `claude auth login` and try again."
        )
    return {"auth_method": "claude.ai", "api_provider": "firstParty"}


def _read_routing_state(home: Path | None = None) -> dict[str, Any]:
    root = home or codex_home()
    path = root / STATE_FILENAME
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AdvisorError("The saved routing state is not a regular file.")
        if info.st_nlink != 1:
            raise AdvisorError("The saved routing state has multiple hard links.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdvisorError(
            "A bundled Claude planning model is not configured; run setup first."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("Could not read valid routing state.") from exc
    try:
        state = routing_state.validate_routing_state(payload)
    except routing_state.RoutingStateError as exc:
        raise AdvisorError("The saved routing state is invalid.") from exc
    config_file = state["config_file"]
    try:
        belongs_to_home = (
            Path(config_file).expanduser().resolve()
            == (root / "config.toml").expanduser().resolve()
        )
    except (OSError, RuntimeError) as exc:
        raise AdvisorError("The saved routing state belongs to another Codex home.") from exc
    if not belongs_to_home:
        raise AdvisorError("The saved routing state belongs to another Codex home.")
    return state


def _validate_seat(seat: str) -> Seat:
    if seat not in {"planner", "advisor"}:
        raise AdvisorError("Claude planning seat must be `planner` or `advisor`.")
    return seat  # type: ignore[return-value]


def _validate_fable_route(route: Any, *, seat: Seat) -> dict[str, str]:
    if not isinstance(route, dict) or route.get("kind") not in {
        "fable",
        "claude_subscription",
    }:
        raise AdvisorError(
            f"A bundled Claude model is not the configured {seat}."
        )
    return {"model": route["model"], "effort": route["effort"]}


def load_fable_route(
    home: Path | None = None, *, seat: str = "advisor"
) -> dict[str, str]:
    """Load and validate one explicitly authorized bundled Claude seat.

    ``seat`` defaults to Advisor for compatibility with the original bridge.
    It is deliberately constrained and resolved from disk on every invocation.
    """

    selected = _validate_seat(seat)
    payload = _read_routing_state(home)
    return _validate_fable_route(payload.get(selected), seat=selected)


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


def _validate_runtime_models(
    usage: Any, primary_model: str = FABLE_MODEL
) -> list[str]:
    allowed_models = ALLOWED_RUNTIME_MODELS_BY_PRIMARY.get(primary_model)
    reviewed_primaries = REVIEWED_PRIMARY_MODELS_BY_ROUTE.get(primary_model)
    if allowed_models is None or reviewed_primaries is None:
        raise AdvisorError("The configured Claude primary model is not sealed.")
    policy_label = "Fable" if primary_model == FABLE_MODEL else "Claude"
    primary_label = (
        "Claude Fable 5" if primary_model == FABLE_MODEL else "Claude Opus 5"
    )
    if not isinstance(usage, dict):
        raise AdvisorError("Runtime metadata has a malformed modelUsage mapping.")
    raw_models = list(usage)
    if not all(isinstance(model, str) and bool(model.strip()) for model in raw_models):
        raise AdvisorError(
            f"Runtime metadata reported a model outside the allowed {policy_label} "
            "runtime policy."
        )
    for model_usage in usage.values():
        if not isinstance(model_usage, dict) or not model_usage:
            raise AdvisorError("Runtime metadata has a malformed modelUsage value.")
        for field, value in model_usage.items():
            is_nonnegative_finite_number = (
                type(value) is int
                and value >= 0
                or type(value) is float
                and math.isfinite(value)
                and value >= 0
            )
            if (
                not isinstance(field, str)
                or not field.strip()
                or not is_nonnegative_finite_number
            ):
                raise AdvisorError(
                    "Runtime metadata has a malformed modelUsage value."
                )
    used_models = sorted(raw_models)
    if not set(used_models).intersection(reviewed_primaries):
        raise AdvisorError(
            f"Runtime metadata did not confirm the pinned {primary_label} primary "
            "model or a reviewed resolved identity."
        )
    if not set(used_models).issubset(allowed_models):
        raise AdvisorError(
            f"Runtime metadata reported a model outside the allowed {policy_label} "
            "runtime policy."
        )
    return used_models


def _normalize_model_payload(
    payload: Any, *, display_name: str, operation: str
) -> dict[str, Any]:
    """Accept one legacy result object or one unambiguous result event."""

    message = f"{display_name} {operation} returned an unexpected response."
    if isinstance(payload, dict):
        if "type" in payload and payload.get("type") != "result":
            raise AdvisorError(message)
        if "subtype" in payload and payload.get("subtype") != "success":
            raise AdvisorError(message)
        return payload
    if not isinstance(payload, list) or not payload:
        raise AdvisorError(message)
    if not all(
        isinstance(event, dict)
        and isinstance(event.get("type"), str)
        and bool(event["type"])
        for event in payload
    ):
        raise AdvisorError(message)
    result_events = [event for event in payload if event.get("type") == "result"]
    if len(result_events) != 1:
        raise AdvisorError(message)
    selected = result_events[0]
    if selected.get("subtype") not in (None, "success"):
        raise AdvisorError(message)
    content_fields = {"result", "modelUsage", "structured_output"}
    if any(
        event is not selected and content_fields.intersection(event)
        for event in payload
    ):
        raise AdvisorError(message)
    return selected


def _validate_review_output(value: Any, *, display_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(PLAN_REVIEW_SCHEMA["required"]):
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    signal = value.get("signal")
    body = value.get("body")
    if (
        not isinstance(signal, str)
        or signal not in {"PLAN_APPROVED", "PLAN_REVISE"}
        or not isinstance(body, str)
        or not body.strip()
    ):
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    return {"signal": signal, "body": body.strip()}


def _review_response(payload: dict[str, Any], *, display_name: str) -> tuple[str, str]:
    structured_present = "structured_output" in payload
    result_present = "result" in payload
    structured = (
        _validate_review_output(
            payload.get("structured_output"),
            display_name=display_name,
        )
        if structured_present
        else None
    )
    legacy: dict[str, str] | None = None
    if result_present:
        raw_result = payload.get("result")
        if not isinstance(raw_result, str):
            raise AdvisorError(
                f"{display_name} plan review returned invalid structured output."
            )
        try:
            decoded_result = json.loads(raw_result)
        except json.JSONDecodeError:
            if structured is None:
                raise AdvisorError(
                    f"{display_name} plan review returned invalid structured output."
                )
        else:
            legacy = _validate_review_output(
                decoded_result,
                display_name=display_name,
            )
    if structured is None and legacy is None:
        raise AdvisorError(
            f"{display_name} plan review returned invalid structured output."
        )
    if structured is not None and legacy is not None and structured != legacy:
        raise AdvisorError(
            f"{display_name} plan review returned conflicting structured output."
        )
    selected = structured or legacy
    assert selected is not None
    return selected["signal"], f"{selected['signal']}\n{selected['body']}"


def _invoke_fable(
    *,
    operation: str,
    seat: Seat,
    prompt: str,
    system_prompt: str,
    allowed_signals: set[str],
) -> tuple[str, str, dict[str, str], dict[str, str], list[str]]:
    """Run one stateless, seat-authorized, no-tools Claude operation."""

    route = load_fable_route(seat=seat)
    display_name = (
        "Claude Fable 5" if route["model"] == FABLE_MODEL else "Claude Opus 5"
    )
    claude = resolve_claude()
    auth = check_claude_auth(claude)
    command = [
        str(claude),
        "--print",
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
    if operation == "plan review":
        command.extend(
            (
                "--json-schema",
                json.dumps(
                    PLAN_REVIEW_SCHEMA,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
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
        raise AdvisorError(f"{display_name} {operation} timed out.") from exc
    except OSError as exc:
        raise AdvisorError(f"Could not start {display_name} {operation}.") from exc
    if result.returncode != 0:
        raise AdvisorError(
            f"{display_name} {operation} exited with {result.returncode}; output withheld."
        )
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError(f"{display_name} {operation} returned malformed JSON.") from exc
    payload = _normalize_model_payload(
        decoded,
        display_name=display_name,
        operation=operation,
    )
    # Authorize the complete runtime identity set before interpreting or
    # returning any model-authored plan/review content.
    used_models = _validate_runtime_models(payload.get("modelUsage"), route["model"])
    if operation == "plan review":
        signal, response = _review_response(payload, display_name=display_name)
    else:
        if "structured_output" in payload or not isinstance(
            payload.get("result"), str
        ):
            raise AdvisorError(
                f"{display_name} {operation} returned an unexpected response."
            )
        response = payload["result"].strip()
        signal = _first_non_empty_line(response)
    if signal not in allowed_signals:
        if operation == "plan review":
            raise AdvisorError(
                f"{display_name} returned an invalid structured plan decision."
            )
        expected = " or ".join(sorted(allowed_signals))
        raise AdvisorError(
            f"{display_name} {operation} omitted the required {expected} signal."
        )
    return signal, response, route, auth, used_models


def _base_result(
    *, route: dict[str, str], auth: dict[str, str], used_models: list[str]
) -> dict[str, Any]:
    return {
        # ``model`` is the route's pinned primary identity; ``used_models``
        # preserves every runtime-reported model, including an allowed helper.
        "model": route["model"],
        "effort": route["effort"],
        "auth_method": auth["auth_method"],
        "used_models": used_models,
    }


def create_plan(packet: str) -> dict[str, Any]:
    values = _validate_inputs("plan creation", packet=packet)
    signal, response, route, auth, used_models = _invoke_fable(
        operation="plan creation",
        seat="planner",
        prompt=values["packet"],
        system_prompt=PLANNER_CREATE_SYSTEM_PROMPT,
        allowed_signals={"PLAN_DRAFT"},
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
            "Claude plan revision must contain exactly one FINDINGS_LEDGER "
            "and one REVISED_PLAN section."
        )
    ledger_index = ledger_positions[0]
    plan_index = plan_positions[0]
    if ledger_index >= plan_index:
        raise AdvisorError(
            "Claude plan revision sections are in the wrong order."
        )
    ledger = "\n".join(lines[ledger_index + 1 : plan_index]).strip()
    revised_plan = "\n".join(lines[plan_index + 1 :]).strip()
    if not ledger or not revised_plan:
        raise AdvisorError(
            "Claude plan revision has an empty FINDINGS_LEDGER or REVISED_PLAN section."
        )


def revise_plan(
    task: str, current_plan: str, critique: str, history: str
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
    )
    _validate_revision_structure(response)
    return {
        "signal": signal,
        "revision": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def review_plan(packet: str) -> dict[str, Any]:
    values = _validate_inputs("plan review", packet=packet)
    signal, response, route, auth, used_models = _invoke_fable(
        operation="plan review",
        seat="advisor",
        prompt=values["packet"],
        system_prompt=ADVISOR_SYSTEM_PROMPT,
        allowed_signals={"PLAN_APPROVED", "PLAN_REVISE"},
    )
    return {
        "decision": signal,
        "review": response,
        **_base_result(route=route, auth=auth, used_models=used_models),
    }


def _configured_fable_seats() -> dict[str, dict[str, str]]:
    """Return configured bundled Claude seats (legacy public name)."""

    payload = _read_routing_state()
    routes: dict[str, dict[str, str]] = {}
    for seat in ("planner", "advisor"):
        value = payload.get(seat)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise AdvisorError(f"The saved {seat} route is invalid.")
        if value.get("kind") not in {"fable", "claude_subscription"}:
            continue
        routes[seat] = _validate_fable_route(value, seat=_validate_seat(seat))
    if not routes:
        raise AdvisorError(
            "No bundled Claude model is configured for Planner or Advisor."
        )
    return routes


def status() -> dict[str, Any]:
    routes = _configured_fable_seats()
    auth = check_claude_auth()
    seats = {
        seat: {"model": route["model"], "effort": route["effort"]}
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
            "title": "Create a plan with the configured Claude model",
            "description": "Create one stateless plan draft with the configured Claude Planner.",
            "inputSchema": {
                "type": "object",
                "properties": {"packet": {**string_property, "description": "Complete planning packet."}},
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "revise_plan",
            "title": "Revise a plan with the configured Claude model",
            "description": "Create one stateless revision with a findings ledger and complete revised plan.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {**string_property, "description": "Original task."},
                    "current_plan": {**string_property, "description": "Canonical current plan with source version."},
                    "critique": {**string_property, "description": "Latest Advisor critique with stable finding IDs."},
                    "history": {**string_property, "description": "Compact cumulative findings history."},
                },
                "required": ["task", "current_plan", "critique", "history"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "review_plan",
            "title": "Review a plan with the configured Claude model",
            "description": "Review one self-contained packet with the configured Claude Advisor.",
            "inputSchema": {
                "type": "object",
                "properties": {"packet": {**string_property, "description": "Complete context, plan, risks, slices, and checks."}},
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "status",
            "title": "Check bundled Claude Planner and Advisor status",
            "description": "Check configured Claude seats and first-party login without a model call.",
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
            # Preserve the historical launcher identity for loaded-plugin
            # compatibility; the tool metadata describes either sealed model.
            "serverInfo": {
                "name": "codex-orchestration-fable-advisor",
                "version": "2.0.0",
            },
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
                args = _tool_arguments(arguments, {"packet"})
                result = _tool_result(create_plan(args.get("packet")))
            elif name == "revise_plan":
                args = _tool_arguments(arguments, {"task", "current_plan", "critique", "history"})
                result = _tool_result(
                    revise_plan(
                        args.get("task"),
                        args.get("current_plan"),
                        args.get("critique"),
                        args.get("history"),
                    )
                )
            elif name == "review_plan":
                args = _tool_arguments(arguments, {"packet"})
                result = _tool_result(review_plan(args.get("packet")))
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
                    "recovery": STALE_BRIDGE_RECOVERY,
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
