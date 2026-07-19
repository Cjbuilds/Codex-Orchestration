#!/usr/bin/env python3
"""Root-directed, subscription-backed Kimi K3 Designer MCP bridge.

Each call uses the installed Kimi Code CLI through ACP and the user's existing
first-party OAuth session.  The model runs in a fresh empty directory through
acpx with terminal capability disabled and every permission denied.  Any tool
attempt, unexpected model, malformed ACP transcript, or catalog drift fails
closed before model-authored content is returned to Codex.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

import routing_state


STATE_FILENAME = ".codex-orchestration-routing.json"
KIMI_MODEL = "kimi-code/k3"
KIMI_PROVIDER = "managed:kimi-code"
KIMI_EFFECTIVE_MODEL = "k3"
KIMI_EFFORT = "max"
MIN_KIMI_VERSION = (0, 27, 0)
MIN_ACPX_VERSION = (0, 12, 0)
CALL_TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 20
MAX_INPUT_CHARS = 200_000
FORBIDDEN_METHOD_PREFIXES = ("fs/", "terminal/")
FORBIDDEN_METHODS = {"session/request_permission"}
FORBIDDEN_UPDATES = {"tool_call", "tool_call_update"}
SENSITIVE_ENV_EXACT = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}

DESIGNER_PROMPT = """You are Kimi K3 acting only as a stateless Designer for Codex's root orchestrator.
Turn the supplied approved requirements into a bounded visual, UX, interaction, information-architecture, or design-system handoff. Do not call tools, inspect files, execute commands, spawn agents, contact other roles, revise the canonical implementation plan, edit implementation code, or release implementation work.

Your first non-empty line must be exactly DESIGN_HANDOFF. Return the complete handoff after that signal, including concrete decisions, states, accessibility, responsive behavior when relevant, constraints, and acceptance checks. Report only to the root orchestrator.

# APPROVED_REQUIREMENTS_AND_DESIGN_PACKET
"""


class KimiDesignerError(RuntimeError):
    """Fail-closed error for the Kimi subscription bridge."""


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser() if value else Path.home() / ".codex"


def sanitized_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in tuple(env):
        upper = name.upper()
        if upper in SENSITIVE_ENV_EXACT or upper.startswith("KIMI_MODEL_"):
            env.pop(name, None)
    env["KIMI_CODE_NO_AUTO_UPDATE"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _resolve_command(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise KimiDesignerError(f"Required command `{name}` is not installed or on PATH.")
    return Path(found).resolve()


def _version_tuple(value: str, *, label: str) -> tuple[int, int, int]:
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", value.strip())
    if match is None:
        raise KimiDesignerError(f"Could not parse the installed {label} version.")
    return tuple(map(int, match.groups()))


def _run_probe(command: list[str], *, label: str) -> str:
    try:
        result = subprocess.run(
            command,
            env=sanitized_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KimiDesignerError(f"Could not inspect {label}.") from exc
    if result.returncode != 0:
        raise KimiDesignerError(f"{label} inspection failed; output withheld.")
    return result.stdout


def check_prerequisites() -> dict[str, str]:
    kimi = _resolve_command("kimi")
    acpx = _resolve_command("acpx")
    kimi_version_text = _run_probe([str(kimi), "--version"], label="Kimi Code CLI")
    acpx_version_text = _run_probe([str(acpx), "--version"], label="acpx")
    kimi_version = _version_tuple(kimi_version_text, label="Kimi Code CLI")
    acpx_version = _version_tuple(acpx_version_text, label="acpx")
    if kimi_version < MIN_KIMI_VERSION:
        raise KimiDesignerError("Kimi Code CLI 0.27.0 or newer is required.")
    if acpx_version < MIN_ACPX_VERSION:
        raise KimiDesignerError("acpx 0.12.0 or newer is required.")

    raw_catalog = _run_probe(
        [str(kimi), "provider", "list", "--json"], label="Kimi provider catalog"
    )
    try:
        catalog = json.loads(raw_catalog)
    except json.JSONDecodeError as exc:
        raise KimiDesignerError("Kimi provider catalog returned malformed JSON.") from exc
    providers = catalog.get("providers") if isinstance(catalog, dict) else None
    models = catalog.get("models") if isinstance(catalog, dict) else None
    provider = providers.get(KIMI_PROVIDER) if isinstance(providers, dict) else None
    model = models.get(KIMI_MODEL) if isinstance(models, dict) else None
    if not isinstance(provider, dict) or not isinstance(model, dict):
        raise KimiDesignerError("The managed Kimi Code subscription and K3 model are unavailable.")
    oauth = provider.get("oauth")
    api_key = provider.get("apiKey")
    support_efforts = model.get("supportEfforts")
    if (
        provider.get("type") != "kimi"
        or not (api_key is None or api_key == "")
        or not isinstance(oauth, dict)
        or oauth.get("key") != "oauth/kimi-code"
        or model.get("provider") != KIMI_PROVIDER
        or model.get("model") != KIMI_EFFECTIVE_MODEL
        or model.get("defaultEffort") != KIMI_EFFORT
        or not isinstance(support_efforts, list)
        or not all(isinstance(item, str) for item in support_efforts)
        or KIMI_EFFORT not in support_efforts
    ):
        raise KimiDesignerError("The Kimi K3 subscription route does not match the audited contract.")
    return {
        "kimi": str(kimi),
        "kimi_version": ".".join(map(str, kimi_version)),
        "acpx": str(acpx),
        "acpx_version": ".".join(map(str, acpx_version)),
        "model": KIMI_MODEL,
        "effort": KIMI_EFFORT,
        "auth_method": "kimi-code-oauth",
    }


def _read_routing_state(home: Path | None = None) -> dict[str, Any]:
    root = home or codex_home()
    path = root / STATE_FILENAME
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise KimiDesignerError("The saved routing state is not a private regular file.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KimiDesignerError("Kimi K3 is not configured as Designer; run setup first.") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KimiDesignerError("Could not read valid routing state.") from exc
    try:
        state = routing_state.validate_routing_state(payload)
    except routing_state.RoutingStateError as exc:
        raise KimiDesignerError("The saved routing state is invalid.") from exc
    if Path(state["config_file"]).expanduser().resolve() != (root / "config.toml").resolve():
        raise KimiDesignerError("The saved routing state belongs to another Codex home.")
    return state


def load_designer_route(home: Path | None = None) -> dict[str, str]:
    route = _read_routing_state(home).get("designer")
    if not isinstance(route, dict) or route.get("kind") != "kimi_cli":
        raise KimiDesignerError("Kimi K3 is not the configured Designer.")
    if route.get("model") != KIMI_MODEL or route.get("effort") != KIMI_EFFORT:
        raise KimiDesignerError("The saved Kimi Designer route is not pinned to K3 Max.")
    return {"model": route["model"], "effort": route["effort"], "server": route["server"]}


def _parse_acpx_transcript(stdout: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KimiDesignerError("Kimi ACP returned malformed JSON.") from exc
        if not isinstance(message, dict):
            raise KimiDesignerError("Kimi ACP returned an unexpected transcript value.")
        messages.append(message)
        if "error" in message:
            raise KimiDesignerError("Kimi ACP reported a protocol error; details withheld.")
        method = message.get("method")
        if isinstance(method, str) and (
            method in FORBIDDEN_METHODS or method.startswith(FORBIDDEN_METHOD_PREFIXES)
        ):
            raise KimiDesignerError("Kimi attempted an operation forbidden to the Designer bridge.")
        if method == "session/update":
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            update_type = update.get("sessionUpdate") if isinstance(update, dict) else None
            if update_type in FORBIDDEN_UPDATES:
                raise KimiDesignerError("Kimi attempted to call a tool in the Designer bridge.")

    initialize = next(
        (item.get("result") for item in messages if item.get("id") == 0 and "result" in item),
        None,
    )
    session = next(
        (item.get("result") for item in messages if item.get("id") == 1 and "result" in item),
        None,
    )
    completed = next(
        (item.get("result") for item in messages if item.get("id") == 2 and "result" in item),
        None,
    )
    if not isinstance(initialize, dict) or not isinstance(session, dict) or not isinstance(completed, dict):
        raise KimiDesignerError("Kimi ACP transcript is incomplete.")
    agent_info = initialize.get("agentInfo")
    if not isinstance(agent_info, dict) or agent_info.get("name") != "Kimi Code CLI":
        raise KimiDesignerError("ACP runtime identity did not confirm Kimi Code CLI.")
    model_options = session.get("configOptions")
    current_model = None
    if isinstance(model_options, list):
        for option in model_options:
            if isinstance(option, dict) and option.get("id") == "model":
                current_model = option.get("currentValue")
                break
    if current_model != KIMI_MODEL:
        raise KimiDesignerError("ACP runtime metadata did not confirm the pinned Kimi K3 model.")
    if completed.get("stopReason") != "end_turn":
        raise KimiDesignerError("Kimi Designer did not complete normally.")

    chunks: list[str] = []
    for item in messages:
        if item.get("method") != "session/update":
            continue
        params = item.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict) or update.get("sessionUpdate") != "agent_message_chunk":
            continue
        content = update.get("content")
        if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str):
            chunks.append(content["text"])
    response = "".join(chunks).strip()
    if not response:
        raise KimiDesignerError("Kimi Designer returned no text response.")
    return {
        "response": response,
        "runtime_model": current_model,
        "agent_version": str(agent_info.get("version", "")),
    }


def create_design_handoff(packet: str) -> dict[str, Any]:
    if not isinstance(packet, str) or not packet.strip():
        raise KimiDesignerError("`packet` must be a non-empty string.")
    if len(packet) > MAX_INPUT_CHARS:
        raise KimiDesignerError(f"Design packet exceeds the {MAX_INPUT_CHARS}-character limit.")
    route = load_designer_route()
    ready = check_prerequisites()
    prompt = DESIGNER_PROMPT + packet
    with tempfile.TemporaryDirectory(prefix="codex-orchestration-kimi-designer-") as workspace:
        command = [
            ready["acpx"],
            "--deny-all",
            "--non-interactive-permissions",
            "fail",
            "--no-terminal",
            "--allowed-tools=",
            "--auth-policy",
            "skip",
            "--model",
            KIMI_MODEL,
            "--format",
            "json",
            "--json-strict",
            "--max-turns",
            "1",
            "--timeout",
            str(CALL_TIMEOUT_SECONDS),
            "--cwd",
            workspace,
            "kimi",
            "exec",
            "--file",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                input=prompt,
                env=sanitized_environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=CALL_TIMEOUT_SECONDS + 30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise KimiDesignerError("Kimi Designer timed out.") from exc
        except OSError as exc:
            raise KimiDesignerError("Could not start the Kimi Designer bridge.") from exc
    if result.returncode != 0:
        raise KimiDesignerError(
            f"Kimi Designer exited with {result.returncode}; output withheld."
        )
    parsed = _parse_acpx_transcript(result.stdout)
    response = parsed["response"]
    first_line = next((line.strip() for line in response.splitlines() if line.strip()), "")
    if first_line != "DESIGN_HANDOFF":
        raise KimiDesignerError("Kimi Designer omitted the required DESIGN_HANDOFF signal.")
    return {
        "signal": "DESIGN_HANDOFF",
        "design": response,
        "model": route["model"],
        "effort": route["effort"],
        "runtime_model": parsed["runtime_model"],
        "agent": "Kimi Code CLI",
        "agent_version": parsed["agent_version"],
        "auth_method": ready["auth_method"],
        "transport": "acpx/acp",
        "tool_policy": "deny-all/no-terminal/disposable-cwd",
    }


def status() -> dict[str, Any]:
    route = load_designer_route()
    ready = check_prerequisites()
    return {
        "available": True,
        "configured_seat": "designer",
        "model": route["model"],
        "effort": route["effort"],
        "auth_method": ready["auth_method"],
        "kimi_version": ready["kimi_version"],
        "acpx_version": ready["acpx_version"],
        "model_call_made": False,
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
            "name": "create_design_handoff",
            "title": "Create a design handoff with Kimi K3",
            "description": "Create one stateless, no-tools design handoff through the configured Kimi subscription Designer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "packet": {
                        "type": "string",
                        "maxLength": MAX_INPUT_CHARS,
                        "description": "Approved requirements, constraints, and required handoff format.",
                    }
                },
                "required": ["packet"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "status",
            "title": "Check Kimi K3 subscription Designer status",
            "description": "Check the configured subscription route without making a model call.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
            "serverInfo": {"name": "codex-orchestration-kimi-designer", "version": "1.0.0"},
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
            if not isinstance(arguments, dict):
                raise KimiDesignerError("Tool arguments must be an object.")
            if name == "create_design_handoff":
                if set(arguments) != {"packet"}:
                    raise KimiDesignerError("Designer tool requires exactly `packet`.")
                result = _tool_result(create_design_handoff(arguments.get("packet")))
            elif name == "status":
                if arguments:
                    raise KimiDesignerError("Status accepts no arguments.")
                result = _tool_result(status())
            else:
                raise KimiDesignerError(f"Unknown tool: {name!r}.")
        except KimiDesignerError as exc:
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
