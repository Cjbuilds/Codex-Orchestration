#!/usr/bin/env python3
"""Read-only MCP bridge from Codex to Claude Fable 5 through Claude Code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


STATE_FILENAME = ".codex-orchestration-routing.json"
FABLE_MODEL = "claude-fable-5"
SUPPORTED_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
CLAUDE_TIMEOUT_SECONDS = 600
AUTH_TIMEOUT_SECONDS = 20
SENSITIVE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
}
SYSTEM_PROMPT = """You are Claude Fable 5 acting only as a plan advisor to Codex's root orchestrator.
Review the supplied self-contained packet for material correctness, missing constraints, unsafe sequencing, ownership conflicts, and verification gaps. Do not edit files, call tools, spawn agents, contact executors, or attempt implementation.

Your first non-empty line must be exactly PLAN_APPROVED or PLAN_REVISE.
Use PLAN_APPROVED only when no material gap is present. Use PLAN_REVISE when correction is needed, followed by a concise prioritized list in which every gap has a concrete correction. Ignore style preferences. Report only to the root orchestrator."""


class AdvisorError(RuntimeError):
    pass


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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdvisorError(f"Could not run Claude Code: {exc}") from exc
    if result.returncode != 0:
        raise AdvisorError(f"Claude Code exited with {result.returncode}.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError("Claude Code returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise AdvisorError("Claude Code returned an unexpected JSON value.")
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
            "run `claude auth login` and try again."
        )
    return {
        "auth_method": "claude.ai",
        "api_provider": "firstParty",
    }


def load_fable_route(home: Path | None = None) -> dict[str, str]:
    path = (home or codex_home()) / STATE_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdvisorError("Claude Fable 5 is not configured; run setup first.") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError(f"Could not read the routing state: {exc}") from exc
    route = payload.get("advisor") if isinstance(payload, dict) else None
    if not isinstance(route, dict) or route.get("kind") != "fable":
        raise AdvisorError("Claude Fable 5 is not the configured advisor.")
    model = route.get("model")
    effort = route.get("effort")
    if model != FABLE_MODEL or effort not in SUPPORTED_EFFORTS:
        raise AdvisorError("The saved Claude Fable 5 route is invalid.")
    return {"model": model, "effort": effort}


def review_plan(packet: str) -> dict[str, Any]:
    if not isinstance(packet, str) or not packet.strip():
        raise AdvisorError("`packet` must be a non-empty self-contained review packet.")
    route = load_fable_route()
    claude = resolve_claude()
    auth = check_claude_auth(claude)
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
        SYSTEM_PROMPT,
    ]
    try:
        result = subprocess.run(
            command,
            input=packet,
            env=sanitized_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdvisorError(f"Claude Fable 5 review failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise AdvisorError(f"Claude Fable 5 exited with {result.returncode}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdvisorError("Claude Fable 5 returned malformed JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
        raise AdvisorError("Claude Fable 5 returned an unexpected response.")
    review = payload["result"].strip()
    first = next((line.strip() for line in review.splitlines() if line.strip()), "")
    if first not in {"PLAN_APPROVED", "PLAN_REVISE"}:
        raise AdvisorError("Claude Fable 5 omitted the required plan decision.")
    usage = payload.get("modelUsage")
    used_models = sorted(usage) if isinstance(usage, dict) else []
    if FABLE_MODEL not in used_models:
        raise AdvisorError("Runtime metadata did not confirm Claude Fable 5.")
    return {
        "decision": first,
        "review": review,
        "model": FABLE_MODEL,
        "effort": route["effort"],
        "auth_method": auth["auth_method"],
        "used_models": used_models,
    }


def tool_definitions() -> list[dict[str, Any]]:
    annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    return [
        {
            "name": "review_plan",
            "title": "Review a plan with Claude Fable 5",
            "description": (
                "Send one self-contained, read-only plan-review packet to the configured "
                "Claude Fable 5 advisor."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": {
                        "type": "string",
                        "description": "Complete context, plan, risks, slices, and checks.",
                    }
                },
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "status",
            "title": "Check Claude Fable 5 advisor status",
            "description": "Check the saved route and Claude Code login without a model call.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
    ]


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": is_error,
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "codex-orchestration-fable-advisor", "version": "1.0.0"},
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
            if name == "review_plan":
                packet = arguments.get("packet") if isinstance(arguments, dict) else None
                result = _tool_result(review_plan(packet))
            elif name == "status":
                route = load_fable_route()
                auth = check_claude_auth()
                result = _tool_result({"available": True, **route, **auth})
            else:
                raise AdvisorError(f"Unknown tool: {name!r}.")
        except AdvisorError as exc:
            result = _tool_result({"available": False, "error": str(exc)}, is_error=True)
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
