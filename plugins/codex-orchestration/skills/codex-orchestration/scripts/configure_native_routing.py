#!/usr/bin/env python3
"""Preview, apply, inspect, or disable Codex-Orchestration's native routing policy.

The script deliberately uses Codex App Server's config/read and config/batchWrite
RPCs instead of rewriting config.toml itself. Codex therefore owns TOML parsing,
validation, optimistic concurrency, comment preservation, atomic persistence, and
readback verification.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from routing_state import (
    FABLE_EFFORTS,
    FABLE_MODEL,
    MANAGED_MARKER,
    ROUTING_TOOL_NAMESPACE,
    RoutingStateError,
    validate_routing_state,
)

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("Python 3.11 or newer is required (missing tomllib).") from exc


POLICY_VERSION = 3
STATE_SCHEMA = 3
STATE_FILENAME = ".codex-orchestration-routing.json"
PROBE_VALUE = "CODEX_ORCHESTRATION_CAPABILITY_PROBE"
PLUGIN_ID = "codex-orchestration@codex-orchestration"
FABLE_DEFAULT_EFFORT = "high"
FABLE_EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")
FABLE_EFFORT_ALIASES = {"ultra": "max"}
FABLE_SERVERS = {
    "fable-advisor-python3": ("python3", []),
    "fable-advisor-python": ("python", []),
    "fable-advisor-py": ("py", ["-3.11"]),
}
RPC_TIMEOUT_SECONDS = 20
PROBE_TIMEOUT_SECONDS = 15
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,199}$")
AGENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PERSONAL_MANAGED_ROLE_RE = re.compile(
    r"^codex_orchestration_(?:executor|advisor|planner)_[0-9a-f]{12}$"
)
CUSTOM_AGENT_MANAGED_MARKER = (
    "# Managed by codex-orchestration. Standalone custom agent v2."
)
MISSING = object()


class ConfigurationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Manage a persistent Codex multi-agent routing policy. The model "
            "selected for each task remains the root orchestrator."
        )
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--disable", action="store_true")
    parser.add_argument(
        "--require-effective",
        action="store_true",
        help=(
            "With --status, return 1 unless the policy is installed, effective, "
            "client-compatible, complete, and free of unavailable or orphaned roles."
        ),
    )

    executor = parser.add_mutually_exclusive_group()
    executor.add_argument("--executor-model", help="Exact model ID for direct routing.")
    executor.add_argument(
        "--executor-agent",
        help="Loaded custom-agent name for durable or cross-provider routing.",
    )
    parser.add_argument(
        "--executor-effort",
        default="auto",
        help="Exact supported effort, or auto (resolved to the catalog default).",
    )

    planner = parser.add_mutually_exclusive_group()
    planner.add_argument("--planner-model", help="Optional exact planner model ID.")
    planner.add_argument("--planner-agent", help="Optional loaded planner agent name.")
    planner.add_argument(
        "--planner-fable",
        action="store_true",
        help="Use the bundled Claude Fable 5 planner through Claude Code.",
    )
    parser.add_argument(
        "--planner-effort",
        default="auto",
        help="Exact supported planner effort, or auto.",
    )

    advisor = parser.add_mutually_exclusive_group()
    advisor.add_argument("--advisor-model", help="Optional exact advisor model ID.")
    advisor.add_argument("--advisor-agent", help="Optional loaded advisor agent name.")
    advisor.add_argument(
        "--advisor-fable",
        action="store_true",
        help="Use the bundled Claude Fable 5 advisor through Claude Code.",
    )
    parser.add_argument(
        "--advisor-effort",
        default="auto",
        help="Exact supported advisor effort, or auto.",
    )

    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument(
        "--compat-bin",
        action="append",
        default=[],
        help="Additional Codex binary sharing this user config; repeat as needed.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        help="Override CODEX_HOME (primarily for isolated validation).",
    )
    parser.add_argument(
        "--replace-existing-policy",
        action="store_true",
        help="Replace user-authored v2 hint text and remember it for disable.",
    )
    parser.add_argument(
        "--allow-incompatible-client",
        action="store_true",
        help="Proceed even though another detected Codex binary rejects this policy.",
    )
    parser.add_argument(
        "--confirm-unlisted-models",
        action="store_true",
        help="Use exact model IDs confirmed by the active host when model/list is unavailable.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply after preview.")
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.require_effective and not args.status:
        raise ConfigurationError("--require-effective requires --status.")
    if args.status and args.apply:
        raise ConfigurationError("--status cannot be combined with --apply.")
    if args.status and any(
        (
            args.executor_model,
            args.executor_agent,
            args.planner_model,
            args.planner_agent,
            args.planner_fable,
            args.advisor_model,
            args.advisor_agent,
            args.advisor_fable,
        )
    ):
        raise ConfigurationError("--status does not accept seat settings.")
    if args.disable and any(
        (
            args.executor_model,
            args.executor_agent,
            args.planner_model,
            args.planner_agent,
            args.planner_fable,
            args.advisor_model,
            args.advisor_agent,
            args.advisor_fable,
        )
    ):
        raise ConfigurationError("--disable does not accept seat settings.")
    if not args.status and not args.disable and not (
        args.executor_model or args.executor_agent
    ):
        raise ConfigurationError(
            "Setup requires --executor-model or --executor-agent. Advisor omission means none."
        )
    if args.executor_agent and args.executor_effort != "auto":
        raise ConfigurationError(
            "A custom executor agent owns its effort; omit --executor-effort."
        )
    if args.planner_agent and args.planner_effort != "auto":
        raise ConfigurationError(
            "A custom planner agent owns its effort; omit --planner-effort."
        )
    if args.advisor_agent and args.advisor_effort != "auto":
        raise ConfigurationError(
            "A custom advisor agent owns its effort; omit --advisor-effort."
        )
    if args.planner_fable:
        normalize_fable_effort(args.planner_effort)
    if args.advisor_fable:
        normalize_fable_effort(args.advisor_effort)
    if args.planner_fable and args.advisor_fable:
        raise ConfigurationError(
            "Planner and Advisor routes must be distinct; both cannot use Claude Fable 5."
        )
    for label, value, pattern in (
        ("executor model", args.executor_model, MODEL_RE),
        ("planner model", args.planner_model, MODEL_RE),
        ("advisor model", args.advisor_model, MODEL_RE),
        ("executor agent", args.executor_agent, AGENT_RE),
        ("planner agent", args.planner_agent, AGENT_RE),
        ("advisor agent", args.advisor_agent, AGENT_RE),
    ):
        if value is not None and not pattern.fullmatch(value):
            raise ConfigurationError(f"Invalid {label}: {value!r}.")
    for label, value in (
        ("executor effort", args.executor_effort),
        ("planner effort", args.planner_effort),
        ("advisor effort", args.advisor_effort),
    ):
        if value != "auto" and not EFFORT_RE.fullmatch(value):
            raise ConfigurationError(f"Invalid {label}: {value!r}.")


def normalize_fable_effort(value: str) -> str:
    """Return the Claude CLI effort for a user-facing Fable effort label."""

    requested = FABLE_DEFAULT_EFFORT if value == "auto" else value
    effective = FABLE_EFFORT_ALIASES.get(requested, requested)
    if effective not in FABLE_EFFORTS:
        supported = ", ".join((*FABLE_EFFORT_CHOICES, *FABLE_EFFORT_ALIASES))
        raise ConfigurationError(
            f"Claude Fable 5 effort must be one of: {supported}."
        )
    return effective


def resolve_binary(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or os.sep in value:
        if not candidate.is_file():
            raise ConfigurationError(f"Codex binary does not exist: {candidate}")
        return candidate.resolve()
    found = shutil.which(value)
    if not found:
        raise ConfigurationError(f"Codex binary is not on PATH: {value}")
    return Path(found).resolve()


def binary_version(binary: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError(f"Could not run {binary}: {exc}") from exc
    output = result.stdout.strip()
    return output or f"exit {result.returncode}"


def supports_native_policy(binary: Path) -> tuple[bool, str]:
    """Capability-detect the structured field without reading the user's config."""

    with tempfile.TemporaryDirectory(prefix="codex-orchestration-probe-") as home:
        env = os.environ.copy()
        env["CODEX_HOME"] = home
        try:
            result = subprocess.run(
                [
                    str(binary),
                    "-c",
                    "features.multi_agent_v2.hide_spawn_agent_metadata=false",
                    "-c",
                    (
                        "features.multi_agent_v2.tool_namespace="
                        f'"{ROUTING_TOOL_NAMESPACE}"'
                    ),
                    "-c",
                    (
                        "features.multi_agent_v2.multi_agent_mode_hint_text="
                        f'"{PROBE_VALUE}"'
                    ),
                    "-c",
                    (
                        "features.multi_agent_v2.usage_hint_text="
                        f'"{PROBE_VALUE}"'
                    ),
                    "features",
                    "list",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    if result.returncode == 0:
        return True, "supported"
    detail = " ".join(result.stdout.strip().split())
    return False, (detail[:240] or f"exit {result.returncode}")


def discover_compatibility_binaries(
    target: Path, explicit: list[str]
) -> list[Path]:
    candidates: list[Path] = [target]
    for value in explicit:
        candidates.append(resolve_binary(value))
    path_codex = shutil.which("codex")
    if path_codex:
        candidates.append(Path(path_codex).resolve())
    desktop = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if desktop.is_file():
        candidates.append(desktop.resolve())
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        real = candidate.resolve()
        if real not in seen:
            seen.add(real)
            unique.append(real)
    return unique


class AppServer:
    def __init__(self, binary: Path, codex_home: Path | None) -> None:
        env = os.environ.copy()
        if codex_home is not None:
            resolved_home = codex_home.expanduser().absolute()
            resolved_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(resolved_home)
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            self._process = subprocess.Popen(
                [str(binary), "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self._stderr.close()
            raise ConfigurationError(f"Could not start Codex App Server: {exc}") from exc
        if self._process.stdin is None or self._process.stdout is None:
            self.close()
            raise ConfigurationError("Codex App Server did not expose stdio.")
        self._stdin = self._process.stdin
        self._stdout = self._process.stdout
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._pending: dict[int, dict[str, Any]] = {}
        self._next_id = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            response = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codex_orchestration_installer",
                        "title": "Codex Orchestration Installer",
                        "version": "0.5.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.codex_home = Path(response["codexHome"])
            self.config_path = self.codex_home / "config.toml"
            self.notify("initialized")
        except BaseException:
            self.close()
            raise

    def _read_loop(self) -> None:
        try:
            for line in self._stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._messages.put(
                        ConfigurationError(f"Invalid App Server JSON: {exc}")
                    )
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
            self._messages.put(EOFError("Codex App Server closed stdout."))
        except BaseException as exc:  # pragma: no cover - defensive reader boundary
            self._messages.put(exc)

    def _send(self, message: dict[str, Any]) -> None:
        try:
            self._stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ConfigurationError(
                f"Codex App Server closed its input: {exc}. {self.stderr_excerpt()}"
            ) from exc

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + RPC_TIMEOUT_SECONDS
        while True:
            if request_id in self._pending:
                message = self._pending.pop(request_id)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConfigurationError(
                        f"Timed out waiting for App Server method {method}. "
                        f"{self.stderr_excerpt()}"
                    )
                try:
                    item = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise ConfigurationError(
                        f"Timed out waiting for App Server method {method}."
                    ) from exc
                if isinstance(item, BaseException):
                    raise ConfigurationError(
                        f"App Server stopped during {method}: {item}. "
                        f"{self.stderr_excerpt()}"
                    )
                message = item
                message_id = message.get("id")
                if not isinstance(message_id, int):
                    continue
                if message_id != request_id:
                    self._pending[message_id] = message
                    continue
            if "error" in message:
                error = message.get("error") or {}
                detail = error.get("message", "unknown App Server error")
                data = error.get("data")
                if isinstance(data, dict) and data.get("config_write_error_code"):
                    detail = f"{detail} ({data['config_write_error_code']})"
                raise ConfigurationError(f"{method} failed: {detail}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ConfigurationError(f"{method} returned an invalid result.")
            return result

    def notify(self, method: str) -> None:
        self._send({"method": method})

    def stderr_excerpt(self) -> str:
        # Seeking a file descriptor while the child is still writing can move
        # the shared file offset. The process status is enough during a timeout;
        # collect stderr only after the child has stopped.
        if self._process.poll() is None:
            return ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            value = " ".join(self._stderr.read().strip().split())
            self._stderr.seek(0, os.SEEK_END)
        except OSError:
            return ""
        return value[-1000:]

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        stderr = getattr(self, "_stderr", None)
        if stderr is not None:
            stderr.close()

    def __enter__(self) -> "AppServer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _user_layer(read_result: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    layers = read_result.get("layers")
    if not isinstance(layers, list):
        raise ConfigurationError("config/read did not include configuration layers.")
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        name = layer.get("name")
        if (
            isinstance(name, dict)
            and name.get("type") == "user"
            and name.get("profile") is None
        ):
            config = layer.get("config")
            if not isinstance(config, dict):
                config = {}
            version = layer.get("version")
            return config, version if isinstance(version, str) else None
    return {}, None


def nested_get(config: dict[str, Any], *segments: str) -> Any:
    current: Any = config
    for segment in segments:
        if not isinstance(current, dict) or segment not in current:
            return MISSING
        current = current[segment]
    return current


def snapshot(value: Any, *, known: bool = True) -> dict[str, Any]:
    if not known:
        return {"known": False, "present": False}
    if value is MISSING:
        return {"known": True, "present": False}
    return {"known": True, "present": True, "value": value}


def snapshot_edit(key_path: str, saved: dict[str, Any]) -> dict[str, Any] | None:
    if not saved.get("known"):
        return None
    return {
        "keyPath": key_path,
        "value": saved.get("value") if saved.get("present") else None,
        "mergeStrategy": "replace",
    }


def fable_key_path(server: str) -> str:
    return (
        f"plugins.{json.dumps(PLUGIN_ID)}.mcp_servers."
        f"{json.dumps(server)}.enabled"
    )


def validate_planning_routes(
    planner: dict[str, Any] | None,
    advisor: dict[str, Any] | None,
) -> None:
    """Reject routes that cannot provide independent planning and review seats."""

    if planner is None or advisor is None:
        return
    planner_kind = planner.get("kind")
    advisor_kind = advisor.get("kind")
    identical = (
        planner_kind == advisor_kind == "model"
        and planner.get("model") == advisor.get("model")
    ) or (
        planner_kind == advisor_kind == "agent"
        and planner.get("agent") == advisor.get("agent")
    ) or planner_kind == advisor_kind == "fable"
    if identical:
        raise ConfigurationError(
            "Planner and Advisor routes must be distinct (different direct model IDs, "
            "different custom-agent names, and at most one Claude Fable 5 seat)."
        )


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigurationError(f"Could not inspect routing state {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigurationError(f"Routing state is not a regular file: {path}")
    if info.st_nlink != 1:
        raise ConfigurationError(f"Routing state has multiple hard links: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read routing state {path}: {exc}") from exc
    try:
        return validate_routing_state(state)
    except RoutingStateError as exc:
        raise ConfigurationError("Saved routing state is invalid.") from exc


def _validate_state_config(state: dict[str, Any] | None, config_path: Path) -> None:
    if state is None:
        return
    saved_path = state.get("config_file")
    if not isinstance(saved_path, str):
        raise ConfigurationError("Routing state is missing its config path.")
    if Path(saved_path).expanduser().resolve() != config_path.expanduser().resolve():
        raise ConfigurationError(
            "Routing state belongs to a different Codex config file; refusing to use it."
        )


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        _read_state(path)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)


def _remove_state(path: Path) -> None:
    if not path.exists():
        return
    _read_state(path)
    path.unlink()
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _agent_files_with_name(directory: Path, name: str) -> list[Path]:
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise ConfigurationError(f"Unsafe custom-agent directory: {directory}")
    matches: list[Path] = []
    for path in sorted(directory.glob("*.toml")):
        if path.is_symlink() or not path.is_file():
            raise ConfigurationError(f"Unsafe custom-agent path: {path}")
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(f"Could not inspect custom agent {path}: {exc}") from exc
        if parsed.get("name") == name:
            for field in ("description", "model", "developer_instructions"):
                if not isinstance(parsed.get(field), str) or not parsed[field]:
                    raise ConfigurationError(
                        f"Custom agent {path} has no valid {field!r} field."
                    )
            matches.append(path)
    return matches


def _project_agent_matches(
    workspace: Path,
    personal_agents: Path,
    name: str,
) -> list[Path]:
    matches: list[Path] = []
    personal_real = personal_agents.resolve()
    for root in (workspace, *workspace.parents):
        directory = root / ".codex" / "agents"
        if directory.is_symlink():
            raise ConfigurationError(f"Unsafe custom-agent directory: {directory}")
        if directory.exists() and directory.resolve() == personal_real:
            continue
        matches.extend(_agent_files_with_name(directory, name))
    return matches


def verify_agent_routes(
    codex_home: Path,
    workspace: Path,
    executor: dict[str, Any],
    planner: dict[str, Any] | None,
    advisor: dict[str, Any] | None,
) -> list[Path]:
    """Require personal role files and reject current-project shadowing."""

    verified: list[Path] = []
    personal_agents = codex_home / "agents"
    for label, route in (
        ("Executor", executor),
        ("Planner", planner),
        ("Advisor", advisor),
    ):
        if route is None or route.get("kind") != "agent":
            continue
        name = route.get("agent")
        if not isinstance(name, str):
            raise ConfigurationError(f"{label} custom-agent route has an invalid name.")
        personal = _agent_files_with_name(personal_agents, name)
        if len(personal) != 1:
            raise ConfigurationError(
                f"{label} custom-agent route {name!r} must resolve to exactly one "
                f"personal file under {personal_agents}; found {len(personal)}."
            )
        project = _project_agent_matches(workspace, personal_agents, name)
        if project:
            locations = ", ".join(str(path) for path in project)
            raise ConfigurationError(
                f"{label} personal agent {name!r} is shadowed by a project role: "
                f"{locations}. Use collision-resistant personal route names or remove "
                "the project collision."
            )
        verified.append(personal[0])
    return verified


def load_models(app: AppServer) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"includeHidden": True, "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        result = app.request("model/list", params)
        for item in result.get("data", []):
            if isinstance(item, dict) and isinstance(item.get("model"), str):
                models[item["model"]] = item
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            return models
        cursor = next_cursor


def resolve_model_effort(
    label: str,
    model: str,
    effort: str,
    catalog: dict[str, dict[str, Any]],
    confirm_unlisted: bool,
) -> str:
    item = catalog.get(model)
    if item is None:
        if not confirm_unlisted:
            raise ConfigurationError(
                f"{label} model {model!r} is not in this App Server model catalog."
            )
        if effort == "auto":
            raise ConfigurationError(
                f"{label} effort must be explicit when using an unlisted model."
            )
        return effort
    supported = {
        option.get("reasoningEffort")
        for option in item.get("supportedReasoningEfforts", [])
        if isinstance(option, dict)
    }
    resolved = item.get("defaultReasoningEffort") if effort == "auto" else effort
    if not isinstance(resolved, str) or not resolved:
        raise ConfigurationError(f"Could not resolve {label} effort for {model!r}.")
    if supported and resolved not in supported:
        values = ", ".join(sorted(value for value in supported if isinstance(value, str)))
        raise ConfigurationError(
            f"{label} effort {resolved!r} is not supported by {model!r}; choose {values}."
        )
    return resolved


def select_fable_server() -> str:
    for server, (launcher, prefix) in FABLE_SERVERS.items():
        executable = shutil.which(launcher)
        if not executable:
            continue
        try:
            result = subprocess.run(
                [executable, *prefix, "--version"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r"Python\s+(\d+)\.(\d+)", result.stdout)
        if result.returncode == 0 and match and tuple(map(int, match.groups())) >= (3, 11):
            return server
    raise ConfigurationError(
        "Claude Fable 5 requires a Python 3.11+ launcher named python3, python, "
        "or py. Install one and retry."
    )


def verify_fable_prerequisites(effort: str) -> dict[str, str]:
    try:
        from fable_advisor_mcp import AdvisorError, check_claude_auth, resolve_claude
    except ImportError as exc:  # pragma: no cover - corrupt package
        raise ConfigurationError("The bundled Claude Fable 5 bridge is missing.") from exc
    try:
        claude = resolve_claude()
        auth = check_claude_auth(claude)
        help_result = subprocess.run(
            [str(claude), "--help"],
            env={
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK",
                    "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY",
                }
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (AdvisorError, OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError(str(exc)) from exc
    required = ("--model", "--effort", "--safe-mode", "--prompt-suggestions")
    missing = [flag for flag in required if flag not in help_result.stdout]
    if help_result.returncode != 0 or missing:
        detail = ", ".join(missing) if missing else f"exit {help_result.returncode}"
        raise ConfigurationError(
            f"Claude Code is too old for the Fable advisor bridge ({detail}); update it."
        )
    effort_match = re.search(
        r"--effort\s+<level>.*?\((low[^)]*)\)",
        help_result.stdout,
        flags=re.DOTALL,
    )
    advertised_efforts = (
        set(re.findall(r"[a-z]+", effort_match.group(1)))
        if effort_match is not None
        else set()
    )
    if effort not in advertised_efforts:
        raise ConfigurationError(
            f"Claude Code does not advertise Fable effort {effort!r}; "
            "update Claude Code or choose a supported effort."
        )
    return {"claude": str(claude), **auth}


def _route_summary(route: dict[str, Any]) -> str:
    if route["kind"] == "agent":
        return f"custom agent {route['agent']}"
    if route["kind"] == "fable":
        return f"Claude Fable 5 {route['effort']}"
    return f"{route['model']}@{route['effort']}"


def _managed_personal_roles(codex_home: Path) -> tuple[dict[str, Path], list[str]]:
    """Find only collision-resistant v0.4 personal roles owned by this plugin."""

    roles: dict[str, Path] = {}
    issues: list[str] = []
    directory = codex_home / "agents"
    if not directory.exists() and not directory.is_symlink():
        return roles, issues
    if directory.is_symlink() or not directory.is_dir():
        return roles, [f"managed-role directory is unsafe: {directory}"]
    for path in sorted(directory.glob("*.toml")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"could not inspect {path}: {exc}")
            continue
        if not content.startswith(CUSTOM_AGENT_MANAGED_MARKER + "\n"):
            continue
        try:
            parsed = tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            issues.append(f"managed role is malformed: {path}: {exc}")
            continue
        name = parsed.get("name")
        if not isinstance(name, str) or not PERSONAL_MANAGED_ROLE_RE.fullmatch(name):
            continue
        if name in roles:
            issues.append(f"managed role {name!r} is duplicated")
            continue
        roles[name] = path
    return roles, issues


def _referenced_agent_names(state: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    if not isinstance(state, dict):
        return names
    for key in ("executor", "planner", "advisor"):
        route = state.get(key)
        if isinstance(route, dict) and route.get("kind") == "agent":
            name = route.get("agent")
            if isinstance(name, str):
                names.add(name)
    return names


def _spawn_route(route: dict[str, Any]) -> str:
    if route["kind"] == "agent":
        return f'agent_type = {json.dumps(route["agent"])}'
    return (
        f'model = {json.dumps(route["model"])}, '
        f'reasoning_effort = {json.dumps(route["effort"])}'
    )


def build_policy(
    executor: dict[str, Any],
    planner: dict[str, Any] | None,
    advisor: dict[str, Any] | None,
) -> tuple[str, str]:
    has_direct_route = executor["kind"] == "model" or (
        planner is not None and planner["kind"] == "model"
    ) or (
        advisor is not None and advisor["kind"] == "model"
    )
    provider_guard = (
        "Direct model overrides retain the root provider. Before using a direct "
        "model route, verify that the target model is on the same provider as the "
        "root. If providers differ or cannot be established, report the route "
        "unavailable and require a custom agent that pins model_provider."
        if has_direct_route
        else "Configured custom agents and MCP seats own their provider routes."
    )
    planner_mode = (
        "When a plan is needed, the configured Planner drafts it and handles any "
        "Advisor-requested revision. The root supplies a self-contained packet, owns "
        "the canonical plan and version, validates every result, and decides whether "
        "the work is simple enough not to require a plan."
        if planner is not None
        else "No Planner is configured. The root drafts and revises every plan."
    )
    advisor_mode = (
        "For a non-trivial plan, the root sends a fresh self-contained review call "
        "to the configured Advisor before Executor work. PLAN_APPROVED ends review "
        "early. PLAN_REVISE returns the canonical current plan and version, the "
        "latest critique, and the cumulative findings ledger to the same configured "
        "Planner route, or to the root when Planner is omitted, then reviews the "
        "revised plan again. There may be at most five total Advisor reviews."
        if advisor is not None
        else (
            "No Advisor is configured. Do not create a review loop; after a configured "
            "Planner drafts, the root validates the plan before releasing Executor work."
            if planner is not None
            else "No Advisor is configured. Do not create an Advisor review step."
        )
    )
    mode = f"""{MANAGED_MARKER}
This adds model routing to Codex's existing multi-agent flow; it is not a second scheduler.

If you are the root task model, you are the orchestrator. Own intent, planning, architecture, decomposition, delegation, integration, review, final verification, and the user-facing answer. Codex still decides whether a plan or subagent helps, how many independent slices exist, and what can run safely in parallel. Keep simple, tightly coupled, context-heavy, or root-owned work with the root. Do not delegate merely to prove the policy is active.

{planner_mode}

{advisor_mode}

The root owns the plan version, cumulative findings ledger, review count, validation, adjudication, and release to Executor. There is no Finalizer seat. For Advisor rounds two through five, send only the current plan and version plus a compact cumulative ledger, not prior transcripts. Ask the Advisor to confirm or contest dispositions without blindly repeating accepted findings. Reject a stale plan version or an invalid or incomplete ledger and halt before Executor.

On PLAN_REVISE, record the latest finding IDs before revision. After the Planner returns, validate and merge each INCORPORATED or reasoned REJECTED disposition into the cumulative ledger before another Advisor call. A round-five PLAN_REVISE halts before Executor and produces a non-approval artifact containing the latest plan and version, full ledger, latest findings, and choices available to the user. It must not claim approval. Any required Planner or Advisor route failure also halts before Executor. Only an explicit current-task best-effort instruction changes failure handling: Planner failure permits the root to take over planning for the remaining rounds; Advisor failure may proceed only with the result labeled NOT_ADVISOR_APPROVED. No best-effort setting is persisted.

When executor delegation materially improves speed, cost, quality, or context isolation, use only the configured executor route. Give each executor one bounded, self-contained packet with objective, relevant facts, constraints, owned files or read-only scope, dependencies, acceptance criteria, verification, and handoff format. Inspect every handoff, integrate it, and run final checks yourself.

Explicit user instructions win, including no-subagents and task-local seat overrides. Persistent and task-local Planner and Advisor routes must remain distinct: reject the same direct model ID, the same custom-agent name, or Fable in both seats. This policy does not create or change a Goal, weaken approvals, alter permissions, or force a worker count.

Planner and Advisor are policy-isolated, root-directed seats: they cannot contact each other or Executors, spawn descendants, edit files, execute work, or release Executor. They return only to the root. Fable MCP requests do not carry caller identity, so caller isolation is instruction-enforced even though the bridge itself disables tools and persistence. If you are a spawned child, stay inside the supplied packet, report only to the root, never call planning tools, and never spawn descendants. An Executor never redesigns the root plan or contacts Planner or Advisor.
"""
    if planner is not None and planner["kind"] == "fable":
        planner_hint = (
            "For the initial Planner draft, call `create_plan` from MCP server "
            f"{json.dumps(planner['server'])}; after PLAN_REVISE, call `revise_plan` "
            "from that server. These are root tool calls. Require PLAN_DRAFT from "
            "creation, then assign the canonical version. Require PLAN_REVISION, "
            "FINDINGS_LEDGER, and REVISED_PLAN from each revision."
        )
    elif planner is not None:
        planner_hint = (
            "For each Planner draft or revision, call this tool with "
            f"{_spawn_route(planner)}, fork_turns = \"none\". Send the complete "
            "self-contained packet for that round. Require PLAN_DRAFT initially; "
            "require PLAN_REVISION, the source version, complete findings ledger, "
            "and full revised plan after PLAN_REVISE."
        )
    else:
        planner_hint = "No Planner route is configured; the root drafts and revises."
    if advisor is not None and advisor["kind"] == "fable":
        advisor_hint = (
            "For an advisor review, call `review_plan` from MCP server "
            f"{json.dumps(advisor['server'])} with the round's self-contained packet. "
            "This is a read-only root tool call, not a spawned child. Require "
            "PLAN_APPROVED or PLAN_REVISE and fail closed unless the user explicitly "
            "made Advisor failure best-effort for the current task."
        )
    elif advisor is not None:
        advisor_hint = (
            "For an advisor review, call this tool with "
            f"{_spawn_route(advisor)}, fork_turns = \"none\". Send the complete "
            "review packet and require PLAN_APPROVED or PLAN_REVISE."
        )
    else:
        advisor_hint = "No advisor route is configured."
    usage = f"""{MANAGED_MARKER}
If you are the root task model, you are the orchestrator. Apply these routes only to children you decide to create.

For delegated executor work, call this tool with {_spawn_route(executor)}, fork_turns = "none". Send a self-contained task packet.

{planner_hint}

{advisor_hint}

{provider_guard}

Never use fork_turns = "all" with model, reasoning_effort, or agent_type: a full-history fork inherits the root route and rejects those overrides. Never silently substitute the root model when an exact child route is unavailable. Report the unavailable route to the root. A user's explicit current-task model, effort, agent, or no-subagents instruction overrides this saved default, but a task-local Planner and Advisor must still be distinct: reject the same direct model ID, the same custom-agent name, or Fable in both seats.

If you are a spawned child, do not call this tool or create descendants. Finish only your assigned packet and return to the root.
"""
    return mode, usage


def _compatibility_report(
    binaries: list[Path], allow_incompatible: bool
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    incompatible: list[str] = []
    for binary in binaries:
        supported, detail = supports_native_policy(binary)
        version = binary_version(binary)
        results.append(
            {
                "path": str(binary),
                "version": version,
                "supported": supported,
                "detail": detail,
            }
        )
        state = "supports native policy" if supported else f"incompatible: {detail}"
        print(f"Client: {binary} ({version}) — {state}")
        if not supported:
            incompatible.append(f"{binary} ({version})")
    if incompatible and not allow_incompatible:
        joined = ", ".join(incompatible)
        raise ConfigurationError(
            "Native setup would make the shared config unreadable to: "
            f"{joined}. Update those clients, use the per-task skill fallback, or "
            "repeat only after explicit approval with --allow-incompatible-client."
        )
    return results


def _current_values(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature": nested_get(config, "features", "multi_agent_v2"),
        "mode": nested_get(
            config, "features", "multi_agent_v2", "multi_agent_mode_hint_text"
        ),
        "usage": nested_get(
            config, "features", "multi_agent_v2", "usage_hint_text"
        ),
        "metadata": nested_get(
            config, "features", "multi_agent_v2", "hide_spawn_agent_metadata"
        ),
        "namespace": nested_get(
            config, "features", "multi_agent_v2", "tool_namespace"
        ),
        "mcp": {
            server: nested_get(
                config,
                "plugins",
                PLUGIN_ID,
                "mcp_servers",
                server,
                "enabled",
            )
            for server in FABLE_SERVERS
        },
    }


def _is_managed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(MANAGED_MARKER)


def _managed_matches(state: dict[str, Any], current: dict[str, Any]) -> bool:
    managed = state.get("managed")
    base_matches = (
        isinstance(managed, dict)
        and current["mode"] == managed.get("mode")
        and current["usage"] == managed.get("usage")
        and current["metadata"] is False
        and managed.get("namespace") == ROUTING_TOOL_NAMESPACE
        and current["namespace"] == ROUTING_TOOL_NAMESPACE
    )
    if not base_matches:
        return False
    managed_mcp = managed.get("mcp")
    return managed_mcp is None or all(
        current["mcp"].get(server, MISSING) == enabled
        for server, enabled in managed_mcp.items()
    )


def _batch_write(
    app: AppServer,
    edits: list[dict[str, Any]],
    version: str | None,
    *,
    reload_user_config: bool,
) -> dict[str, Any]:
    return app.request(
        "config/batchWrite",
        {
            "edits": edits,
            "expectedVersion": version,
            "reloadUserConfig": reload_user_config,
        },
    )


def _status(
    target: Path,
    codex_home: Path | None,
    binaries: list[Path],
    require_effective: bool,
) -> int:
    clients_compatible = True
    for binary in binaries:
        supported, detail = supports_native_policy(binary)
        label = "compatible" if supported else f"incompatible ({detail})"
        print(f"Client: {binary} ({binary_version(binary)}) — {label}")
        clients_compatible = clients_compatible and supported
    with AppServer(target, codex_home) as app:
        workspace = Path.cwd().resolve()
        read_result = app.request(
            "config/read",
            {"includeLayers": True, "cwd": str(workspace)},
        )
        config, _ = _user_layer(read_result)
        current = _current_values(config)
        effective_config = read_result.get("config")
        effective = _current_values(
            effective_config if isinstance(effective_config, dict) else {}
        )
        state_path = app.codex_home / STATE_FILENAME
        state = _read_state(state_path)
        _validate_state_config(state, app.config_path)
        managed_pair = _is_managed(current["mode"]) and _is_managed(
            current["usage"]
        )
        state_matches = state is not None and _managed_matches(state, current)
        if state is not None and managed_pair and not state_matches:
            routing_state = "managed fields conflict with local restore state"
        elif managed_pair:
            controls_ready = (
                current["metadata"] is False
                and current["namespace"] == ROUTING_TOOL_NAMESPACE
            )
            if not controls_ready:
                routing_state = "managed hints found but routing controls are incomplete"
            elif (
                effective["mode"] == current["mode"]
                and effective["usage"] == current["usage"]
                and effective["metadata"] is False
                and effective["namespace"] == ROUTING_TOOL_NAMESPACE
            ):
                routing_state = f"installed and effective in {workspace}"
            else:
                routing_state = f"installed but overridden in {workspace}"
        elif current["mode"] is MISSING and current["usage"] is MISSING:
            routing_state = "inactive"
        else:
            routing_state = "partial or user-authored"
        print(f"Native policy: {routing_state}")
        print(
            "V2 activation: not inferred by the installer; choose a v2 root "
            "model such as current Sol or Terra"
        )
        print(f"Config: {app.config_path}")
        fable_available = True
        if state_matches:
            print(f"Executor: {_route_summary(state['executor'])}")
            planner = state.get("planner")
            advisor = state.get("advisor")
            print(f"Planner: {_route_summary(planner) if planner else 'root'}")
            print(f"Advisor: {_route_summary(advisor) if advisor else 'none'}")
            fable_routes = [
                route
                for route in (planner, advisor)
                if isinstance(route, dict) and route.get("kind") == "fable"
            ]
            for route in fable_routes:
                try:
                    verify_fable_prerequisites(route["effort"])
                except ConfigurationError as exc:
                    fable_available = False
                    print(f"Claude Fable 5: unavailable — {exc}")
                else:
                    print(
                        "Claude Fable 5: ready — first-party login; no model call made"
                    )
            try:
                verified = verify_agent_routes(
                    app.codex_home,
                    workspace,
                    state["executor"],
                    planner,
                    advisor,
                )
            except (ConfigurationError, KeyError, TypeError) as exc:
                print(f"Custom-agent route: unavailable — {exc}")
                agent_routes_available = False
            else:
                agent_routes_available = True
                if verified:
                    print(
                        "Custom-agent route: verified — "
                        + ", ".join(str(path) for path in verified)
                    )
        elif routing_state.startswith("installed"):
            agent_routes_available = False
            print("Seats: managed policy found; local state is unavailable")
        elif state is not None:
            agent_routes_available = False
            print("Seats: suppressed because restore state is stale or conflicting")
        else:
            agent_routes_available = False

        managed_roles, role_issues = _managed_personal_roles(app.codex_home)
        referenced_roles = _referenced_agent_names(state if state_matches else None)
        orphaned_roles = {
            name: path
            for name, path in managed_roles.items()
            if name not in referenced_roles
        }
        for issue in role_issues:
            print(f"Managed custom-agent inspection: unavailable — {issue}")
        if orphaned_roles:
            rendered = ", ".join(
                f"{name} ({path})" for name, path in sorted(orphaned_roles.items())
            )
            print(f"Orphaned managed custom agents: {rendered}")
        else:
            print("Orphaned managed custom agents: none")
        if effective["metadata"] is False:
            print("V2 spawn metadata setting: visible when a v2 root is selected")
        else:
            print("V2 spawn metadata setting: hidden or inherited in this workspace")
        if effective["namespace"] == ROUTING_TOOL_NAMESPACE:
            print(f"V2 tool namespace: {ROUTING_TOOL_NAMESPACE}")
        else:
            print("V2 tool namespace: not routed through agents in this workspace")
        print(
            "Routing validation: not performed — config compatibility and policy "
            "effectiveness do not prove route acceptance or the effective child model"
        )
        healthy = (
            clients_compatible
            and routing_state.startswith("installed and effective")
            and state_matches
            and agent_routes_available
            and fable_available
            and not role_issues
            and not orphaned_roles
        )
    return 1 if require_effective and not healthy else 0


def _prepare_setup_state(
    config: dict[str, Any],
    existing_state: dict[str, Any] | None,
    mode: str,
    usage: str,
    executor: dict[str, Any],
    planner: dict[str, Any] | None,
    advisor: dict[str, Any] | None,
    config_path: Path,
    replace_existing: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    current = _current_values(config)
    feature = current["feature"]
    scalar_feature = isinstance(feature, bool)

    if existing_state is not None:
        if not _managed_matches(existing_state, current):
            raise ConfigurationError(
                "The managed routing fields changed outside this plugin. Refusing "
                "to overwrite them; inspect status and resolve the conflict first."
            )
        previous = existing_state.get("previous")
        if not isinstance(previous, dict):
            raise ConfigurationError("Managed routing state is missing its restore data.")
        previous = dict(previous)
        scalar_origin = existing_state.get("scalar_origin")
        if isinstance(scalar_origin, bool):
            managed_feature = existing_state.get("managed_feature")
            if current["feature"] != managed_feature:
                raise ConfigurationError(
                    "The converted multi_agent_v2 table gained other changes. Refusing "
                    "to update it because disable could no longer restore the original "
                    "boolean safely."
                )
    else:
        for label in ("mode", "usage"):
            value = current[label]
            if value is not MISSING and not _is_managed(value) and not replace_existing:
                raise ConfigurationError(
                    f"A user-authored {label} hint already exists. Re-run only after "
                    "review with --replace-existing-policy so it can be restored later."
                )
        recovered_mode = _is_managed(current["mode"])
        recovered_usage = _is_managed(current["usage"])
        recovered_any = recovered_mode or recovered_usage
        # Each marker independently proves ownership. Remove a surviving managed
        # string on disable, preserve any user-authored counterpart, and leave
        # unmarked metadata and namespace alone when restore state was lost.
        previous = {
            "mode": snapshot(MISSING) if recovered_mode else snapshot(current["mode"]),
            "usage": (
                snapshot(MISSING) if recovered_usage else snapshot(current["usage"])
            ),
            "metadata": (
                snapshot(MISSING, known=False)
                if recovered_any
                else snapshot(current["metadata"])
            ),
            "namespace": (
                snapshot(MISSING, known=False)
                if recovered_any
                else snapshot(current["namespace"])
            ),
        }
        scalar_origin = feature if scalar_feature else None

    if scalar_feature and existing_state is None:
        replacement = {
            "enabled": feature,
            "hide_spawn_agent_metadata": False,
            "tool_namespace": ROUTING_TOOL_NAMESPACE,
            "multi_agent_mode_hint_text": mode,
            "usage_hint_text": usage,
        }
        edits = [
            {
                "keyPath": "features.multi_agent_v2",
                "value": replacement,
                "mergeStrategy": "replace",
            }
        ]
        rollback = [
            {
                "keyPath": "features.multi_agent_v2",
                "value": feature,
                "mergeStrategy": "replace",
            }
        ]
        managed_feature = replacement
    elif existing_state is not None and isinstance(scalar_origin, bool):
        if not isinstance(feature, dict):
            raise ConfigurationError(
                "Managed scalar conversion is no longer a table; refusing to update it."
            )
        replacement = dict(feature)
        replacement.update(
            {
                "hide_spawn_agent_metadata": False,
                "tool_namespace": ROUTING_TOOL_NAMESPACE,
                "multi_agent_mode_hint_text": mode,
                "usage_hint_text": usage,
            }
        )
        edits = [
            {
                "keyPath": "features.multi_agent_v2",
                "value": replacement,
                "mergeStrategy": "replace",
            }
        ]
        rollback = [
            {
                "keyPath": "features.multi_agent_v2",
                "value": feature,
                "mergeStrategy": "replace",
            }
        ]
        managed_feature = replacement
    else:
        edits = [
            {
                "keyPath": "features.multi_agent_v2.hide_spawn_agent_metadata",
                "value": False,
                "mergeStrategy": "replace",
            },
            {
                "keyPath": "features.multi_agent_v2.tool_namespace",
                "value": ROUTING_TOOL_NAMESPACE,
                "mergeStrategy": "replace",
            },
            {
                "keyPath": "features.multi_agent_v2.multi_agent_mode_hint_text",
                "value": mode,
                "mergeStrategy": "replace",
            },
            {
                "keyPath": "features.multi_agent_v2.usage_hint_text",
                "value": usage,
                "mergeStrategy": "replace",
            },
        ]
        rollback = [
            edit
            for edit in (
                snapshot_edit(
                    "features.multi_agent_v2.hide_spawn_agent_metadata",
                    snapshot(current["metadata"]),
                ),
                snapshot_edit(
                    "features.multi_agent_v2.tool_namespace",
                    snapshot(current["namespace"]),
                ),
                snapshot_edit(
                    "features.multi_agent_v2.multi_agent_mode_hint_text",
                    snapshot(current["mode"]),
                ),
                snapshot_edit(
                    "features.multi_agent_v2.usage_hint_text",
                    snapshot(current["usage"]),
                ),
            )
            if edit is not None
        ]
        managed_feature = None

    existing_managed = existing_state.get("managed", {}) if existing_state else {}
    manage_mcp = (
        any(
            isinstance(route, dict) and route.get("kind") == "fable"
            for route in (planner, advisor)
        )
        or isinstance(existing_managed, dict)
        and isinstance(existing_managed.get("mcp"), dict)
    )
    managed_mcp: dict[str, bool] | None = None
    if manage_mcp:
        previous_mcp = previous.get("mcp")
        if not isinstance(previous_mcp, dict):
            previous_mcp = {}
        fable_route = next(
            (
                route
                for route in (planner, advisor)
                if isinstance(route, dict) and route.get("kind") == "fable"
            ),
            None,
        )
        selected = fable_route.get("server") if fable_route is not None else None
        existing_mcp = (
            existing_managed.get("mcp")
            if isinstance(existing_managed, dict)
            and isinstance(existing_managed.get("mcp"), dict)
            else {}
        )
        touched = set(existing_mcp)
        touched.update(
            server for server, value in current["mcp"].items() if value is not MISSING
        )
        if isinstance(selected, str):
            touched.add(selected)
        for server in touched:
            if server not in previous_mcp:
                previous_mcp[server] = snapshot(current["mcp"][server])
        previous["mcp"] = previous_mcp
        managed_mcp = {server: server == selected for server in FABLE_SERVERS if server in touched}
        for server, enabled in managed_mcp.items():
            edits.append(
                {
                    "keyPath": fable_key_path(server),
                    "value": enabled,
                    "mergeStrategy": "replace",
                }
            )
            rollback_edit = snapshot_edit(
                fable_key_path(server), snapshot(current["mcp"][server])
            )
            if rollback_edit is not None:
                rollback.append(rollback_edit)

    managed = {
        "mode": mode,
        "usage": usage,
        "metadata": False,
        "namespace": ROUTING_TOOL_NAMESPACE,
    }
    if managed_mcp is not None:
        managed["mcp"] = managed_mcp

    state = {
        "schema": STATE_SCHEMA,
        "policy_version": POLICY_VERSION,
        "managed_by": "codex-orchestration",
        "config_file": str(config_path),
        "executor": executor,
        "planner": planner,
        "advisor": advisor,
        "managed": managed,
        "previous": previous,
        "scalar_origin": scalar_origin,
        "managed_feature": managed_feature,
    }
    return state, edits, rollback


def _disable(
    app: AppServer,
    config: dict[str, Any],
    version: str | None,
    state: dict[str, Any] | None,
    apply: bool,
) -> int:
    current = _current_values(config)
    state_path = app.codex_home / STATE_FILENAME
    if state is None:
        managed_mode = _is_managed(current["mode"])
        managed_usage = _is_managed(current["usage"])
        if not (managed_mode or managed_usage):
            print("Native routing is already inactive.")
            return 0
        edits = []
        if managed_mode:
            edits.append(
                {
                    "keyPath": "features.multi_agent_v2.multi_agent_mode_hint_text",
                    "value": None,
                    "mergeStrategy": "replace",
                }
            )
        if managed_usage:
            edits.append(
                {
                    "keyPath": "features.multi_agent_v2.usage_hint_text",
                    "value": None,
                    "mergeStrategy": "replace",
                }
            )
        label = "string" if len(edits) == 1 else "strings"
        print(f"Will remove {len(edits)} proven managed hint {label}.")
        print(
            "Will leave hide_spawn_agent_metadata and tool_namespace unchanged "
            "because restore state is missing."
        )
    else:
        if not _managed_matches(state, current):
            raise ConfigurationError(
                "Managed routing fields were edited after setup. Refusing to erase "
                "those changes; restore the managed values or remove them manually."
            )
        previous = state.get("previous")
        if not isinstance(previous, dict):
            raise ConfigurationError("Routing state has no restore data.")
        scalar_origin = state.get("scalar_origin")
        if isinstance(scalar_origin, bool):
            if current["feature"] != state.get("managed_feature"):
                raise ConfigurationError(
                    "The converted multi_agent_v2 table gained other changes. Refusing "
                    "to restore its original boolean form because that would erase them."
                )
            edits = [
                {
                    "keyPath": "features.multi_agent_v2",
                    "value": scalar_origin,
                    "mergeStrategy": "replace",
                }
            ]
        else:
            edits = [
                edit
                for edit in (
                    snapshot_edit(
                        "features.multi_agent_v2.hide_spawn_agent_metadata",
                        previous.get("metadata", {"known": False}),
                    ),
                    snapshot_edit(
                        "features.multi_agent_v2.tool_namespace",
                        previous.get("namespace", {"known": False}),
                    ),
                    snapshot_edit(
                        "features.multi_agent_v2.multi_agent_mode_hint_text",
                        previous.get("mode", {"known": False}),
                    ),
                    snapshot_edit(
                        "features.multi_agent_v2.usage_hint_text",
                        previous.get("usage", {"known": False}),
                    ),
                )
                if edit is not None
            ]
        previous_mcp = previous.get("mcp")
        if isinstance(previous_mcp, dict):
            edits.extend(
                edit
                for edit in (
                    snapshot_edit(fable_key_path(server), previous_mcp[server])
                    for server in previous_mcp
                )
                if edit is not None
            )
        print("Will restore the pre-setup values of every owned routing field.")
    if not apply:
        print("Dry run only. Re-run with --disable --apply after reviewing this preview.")
        return 0
    result = _batch_write(app, edits, version, reload_user_config=True)
    if result.get("status") not in {"ok", "okOverridden"}:
        raise ConfigurationError(f"Unexpected config write status: {result.get('status')!r}")
    _remove_state(state_path)
    print("Native routing disabled. Start a new Codex task to clear the loaded policy.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        _validate_args(args)
        target = resolve_binary(args.codex_bin)
        binaries = discover_compatibility_binaries(target, args.compat_bin)
        if args.status:
            return _status(
                target,
                args.codex_home,
                binaries,
                args.require_effective,
            )
        # Disable must remain available when the policy itself is what makes an
        # older shared-config client incompatible.
        _compatibility_report(
            binaries,
            args.allow_incompatible_client or args.disable,
        )

        with AppServer(target, args.codex_home) as app:
            workspace = Path.cwd().resolve()
            read_result = app.request(
                "config/read",
                {"includeLayers": True, "cwd": str(workspace)},
            )
            config, version = _user_layer(read_result)
            if version is None and app.config_path.exists():
                raise ConfigurationError(
                    "Could not obtain the user config version needed for a safe write."
                )
            state_path = app.codex_home / STATE_FILENAME
            state = _read_state(state_path)
            _validate_state_config(state, app.config_path)
            if args.disable:
                return _disable(app, config, version, state, args.apply)

            catalog: dict[str, dict[str, Any]] = {}
            if args.executor_model or args.planner_model or args.advisor_model:
                try:
                    catalog = load_models(app)
                except ConfigurationError:
                    if not args.confirm_unlisted_models:
                        raise

            if args.executor_model:
                executor_effort = resolve_model_effort(
                    "Executor",
                    args.executor_model,
                    args.executor_effort,
                    catalog,
                    args.confirm_unlisted_models,
                )
                executor = {
                    "kind": "model",
                    "model": args.executor_model,
                    "effort": executor_effort,
                }
            else:
                executor = {"kind": "agent", "agent": args.executor_agent}

            planner: dict[str, Any] | None = None
            advisor: dict[str, Any] | None = None
            fable_auth: dict[str, str] | None = None
            fable_server = (
                select_fable_server()
                if args.planner_fable or args.advisor_fable
                else None
            )
            if args.planner_model:
                planner_effort = resolve_model_effort(
                    "Planner",
                    args.planner_model,
                    args.planner_effort,
                    catalog,
                    args.confirm_unlisted_models,
                )
                planner = {
                    "kind": "model",
                    "model": args.planner_model,
                    "effort": planner_effort,
                }
            elif args.planner_agent:
                planner = {"kind": "agent", "agent": args.planner_agent}
            elif args.planner_fable:
                planner = {
                    "kind": "fable",
                    "model": FABLE_MODEL,
                    "effort": normalize_fable_effort(args.planner_effort),
                    "server": fable_server,
                }

            if args.advisor_model:
                advisor_effort = resolve_model_effort(
                    "Advisor",
                    args.advisor_model,
                    args.advisor_effort,
                    catalog,
                    args.confirm_unlisted_models,
                )
                advisor = {
                    "kind": "model",
                    "model": args.advisor_model,
                    "effort": advisor_effort,
                }
            elif args.advisor_agent:
                advisor = {"kind": "agent", "agent": args.advisor_agent}
            elif args.advisor_fable:
                advisor = {
                    "kind": "fable",
                    "model": FABLE_MODEL,
                    "effort": normalize_fable_effort(args.advisor_effort),
                    "server": fable_server,
                }

            validate_planning_routes(planner, advisor)
            fable_efforts = {
                route["effort"]
                for route in (planner, advisor)
                if isinstance(route, dict) and route.get("kind") == "fable"
            }
            for effort in sorted(fable_efforts):
                fable_auth = verify_fable_prerequisites(effort)

            verified_agents = verify_agent_routes(
                app.codex_home,
                workspace,
                executor,
                planner,
                advisor,
            )
            mode, usage = build_policy(executor, planner, advisor)
            new_state, edits, rollback = _prepare_setup_state(
                config,
                state,
                mode,
                usage,
                executor,
                planner,
                advisor,
                app.config_path,
                args.replace_existing_policy,
            )
            print(f"Config: {app.config_path}")
            print("Orchestrator: model selected when each Codex task starts")
            print(f"Executor: {_route_summary(executor)}")
            print(f"Planner: {_route_summary(planner) if planner else 'root'}")
            print(f"Advisor: {_route_summary(advisor) if advisor else 'none'}")
            if args.planner_fable and args.planner_effort in FABLE_EFFORT_ALIASES:
                print(
                    f"Planner effort alias: {args.planner_effort} -> "
                    f"{planner['effort']} (Claude Code effective value)"
                )
            if args.advisor_fable and args.advisor_effort in FABLE_EFFORT_ALIASES:
                print(
                    f"Advisor effort alias: {args.advisor_effort} -> "
                    f"{advisor['effort']} (Claude Code effective value)"
                )
            if fable_auth is not None:
                print(
                    "Claude Fable 5 login: ready — first-party; "
                    "setup makes no model call"
                )
            if verified_agents:
                print(
                    "Custom-agent files: "
                    + ", ".join(str(path) for path in verified_agents)
                )
            print("Delegation: Codex decides when it helps; no fixed worker count")
            print("Fork mode: none for every routed child")
            print(
                f"Tool namespace: {ROUTING_TOOL_NAMESPACE} "
                "(required for routed spawn metadata on current v2 clients)"
            )
            if not args.apply:
                print("Dry run only. Re-run with --apply after reviewing this preview.")
                return 0

            result = _batch_write(app, edits, version, reload_user_config=True)
            if result.get("status") == "okOverridden":
                try:
                    rollback_result = _batch_write(
                        app,
                        rollback,
                        result.get("version"),
                        reload_user_config=True,
                    )
                    if rollback_result.get("status") not in {"ok", "okOverridden"}:
                        raise ConfigurationError(
                            "unexpected rollback status "
                            f"{rollback_result.get('status')!r}"
                        )
                except ConfigurationError as rollback_exc:
                    raise ConfigurationError(
                        "A higher-priority config layer overrides this routing policy, "
                        "and automatic rollback failed. The user layer may still contain "
                        f"the managed fields; run status before continuing: {rollback_exc}"
                    ) from rollback_exc
                raise ConfigurationError(
                    "A higher-priority config layer overrides this routing policy; "
                    "the user config change was rolled back."
                )
            if result.get("status") != "ok":
                raise ConfigurationError(
                    f"Unexpected config write status: {result.get('status')!r}"
                )
            try:
                _write_state(state_path, new_state)
            except (ConfigurationError, OSError) as state_exc:
                try:
                    rollback_result = _batch_write(
                        app,
                        rollback,
                        result.get("version"),
                        reload_user_config=True,
                    )
                    if rollback_result.get("status") not in {"ok", "okOverridden"}:
                        raise ConfigurationError(
                            "unexpected rollback status "
                            f"{rollback_result.get('status')!r}"
                        )
                except ConfigurationError as rollback_exc:
                    raise ConfigurationError(
                        "Config was written but state persistence and automatic rollback "
                        "both failed; the user config may still contain managed fields. "
                        f"State error: {state_exc}; rollback: {rollback_exc}"
                    ) from state_exc
                raise ConfigurationError(
                    f"Could not persist restore state; config write was rolled back: {state_exc}"
                ) from state_exc

            verify_result = app.request(
                "config/read",
                {"includeLayers": True, "cwd": str(workspace)},
            )
            verify_config, verify_version = _user_layer(verify_result)
            verify_current = _current_values(verify_config)
            effective_config = verify_result.get("config")
            effective_current = _current_values(
                effective_config if isinstance(effective_config, dict) else {}
            )
            user_matches = _managed_matches(new_state, verify_current)
            effective_matches = _managed_matches(new_state, effective_current)
            if not user_matches:
                raise ConfigurationError(
                    "The user routing fields changed after Codex accepted the write. "
                    "That newer edit was preserved; restore state was retained for "
                    "diagnosis. Run status and resolve the managed-field conflict "
                    "before setup or disable."
                )
            if not effective_matches:
                try:
                    rollback_result = _batch_write(
                        app,
                        rollback,
                        verify_version,
                        reload_user_config=True,
                    )
                    if rollback_result.get("status") not in {"ok", "okOverridden"}:
                        raise ConfigurationError(
                            "unexpected rollback status "
                            f"{rollback_result.get('status')!r}"
                        )
                    if state is None:
                        _remove_state(state_path)
                    else:
                        _write_state(state_path, state)
                except (ConfigurationError, OSError) as rollback_exc:
                    raise ConfigurationError(
                        "Codex accepted the write but current-workspace effective "
                        "readback did not match, and "
                        f"automatic rollback failed: {rollback_exc}"
                    ) from rollback_exc
                raise ConfigurationError(
                    "Codex accepted the user-layer write, but current-workspace "
                    "effective readback did not match; the prior config and restore "
                    "state were reinstated."
                )
            print(
                "Native routing policy installed. Start a new Codex task, select a "
                "v2 model such as current Sol or Terra as orchestrator, and use "
                "Codex normally."
            )
            return 0
    except (ConfigurationError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
