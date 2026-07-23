#!/usr/bin/env python3
"""Preview, apply, inspect, repair, or disable Codex-Orchestration's routing policy.

The script deliberately uses Codex App Server's config/read and config/batchWrite
RPCs instead of rewriting config.toml itself. Codex therefore owns TOML parsing,
validation, optimistic concurrency, comment preservation, atomic persistence, and
readback verification.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import external_credentials
import plugin_identity
from routing_state import (
    BUNDLED_MCP_SERVERS,
    FABLE_EFFORTS,
    FABLE_MODEL,
    KIMI_MODEL,
    MANAGED_MARKER,
    QWEN_MODEL,
    QWEN_REGION_CONFIG,
    QWEN_REGIONS,
    ROUTING_TOOL_NAMESPACE,
    RoutingStateError,
    validate_routing_state,
)

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("Python 3.11 or newer is required (missing tomllib).") from exc


POLICY_VERSION = 7
STATE_SCHEMA = 7
STATE_FILENAME = ".codex-orchestration-routing.json"
PROBE_VALUE = "CODEX_ORCHESTRATION_CAPABILITY_PROBE"
LEGACY_PLUGIN_ID = plugin_identity.LEGACY_PLUGIN_ID
FABLE_DEFAULT_EFFORT = "high"
FABLE_EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")
FABLE_EFFORT_ALIASES = {"ultra": "max"}
FABLE_SERVERS = {
    "fable-advisor-python3": ("python3", []),
    "fable-advisor-python": ("python", []),
    "fable-advisor-py": ("py", ["-3.11"]),
}
KIMI_SERVERS = {
    "kimi-designer-python3": ("python3", []),
    "kimi-designer-python": ("python", []),
    "kimi-designer-py": ("py", ["-3.11"]),
}
QWEN_SERVERS = {
    "qwen-advisor-python3": ("python3", []),
    "qwen-advisor-python": ("python", []),
    "qwen-advisor-py": ("py", ["-3.11"]),
}
RPC_TIMEOUT_SECONDS = 20
PROBE_TIMEOUT_SECONDS = 15
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,199}$")
AGENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PERSONAL_MANAGED_ROLE_RE = re.compile(
    r"^codex_orchestration_(?:executor|advisor|planner|designer)_[0-9a-f]{12}$"
)
CUSTOM_AGENT_MANAGED_MARKER = (
    "# Managed by codex-orchestration. Standalone custom agent v2."
)
MISSING = object()
EXECUTING_PLUGIN_ROOT = Path(__file__).resolve().parents[3]


class ConfigurationError(RuntimeError):
    pass


class StateTransactionIndeterminateError(ConfigurationError):
    pass


@contextlib.contextmanager
def _transaction_directory_lock(root: Path):
    """Serialize one native-routing transaction per effective CODEX_HOME."""

    if os.name == "posix":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - POSIX always provides it
            raise ConfigurationError("POSIX transaction locking is unavailable.") from exc
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(root, flags)
        locked = False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as exc:
                raise ConfigurationError(
                    "Another Codex-Orchestration native-routing transaction is "
                    "active; wait for it to finish and retry."
                ) from exc
            yield
        finally:
            try:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        return
    if os.name == "nt":  # pragma: no cover - exercised on Windows hosts
        import ctypes
        from ctypes import wintypes

        lock_identity = os.path.normcase(os.path.realpath(root))
        name_hash = hashlib.sha256(lock_identity.encode("utf-8")).hexdigest()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        mutex = kernel32.CreateMutexW(
            None, False, f"Global\\CodexOrchestrationNativeRouting-{name_hash}"
        )
        if not mutex:
            raise ConfigurationError("Could not create the Windows transaction mutex.")
        wait_result = kernel32.WaitForSingleObject(mutex, 0)
        if wait_result not in {0x00000000, 0x00000080}:
            kernel32.CloseHandle(mutex)
            raise ConfigurationError(
                "Another Codex-Orchestration native-routing transaction is active; "
                "wait for it to finish and retry."
            )
        try:
            yield
        finally:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
        return
    raise ConfigurationError(f"Unsupported transaction-locking platform: {os.name}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Manage a persistent Codex multi-agent routing policy. The model "
            "selected for each task remains the root orchestrator."
        )
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument(
        "--repair",
        action="store_true",
        help=(
            "Restore only drifted plugin-managed mode/usage hints from valid "
            "saved state after a preview."
        ),
    )
    action.add_argument("--disable", action="store_true")
    action.add_argument(
        "--prepare-qwen",
        action="store_true",
        help=(
            "Install the stable OS-credential helper for Qwen Advisor and print "
            "the trusted-terminal enrollment command."
        ),
    )
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
    advisor.add_argument(
        "--advisor-qwen",
        action="store_true",
        help="Use Qwen 3.8 Max Preview as a sealed Token Plan API Advisor.",
    )
    parser.add_argument(
        "--advisor-effort",
        default="auto",
        help="Exact supported advisor effort, or auto.",
    )
    parser.add_argument(
        "--qwen-region",
        choices=tuple(sorted(QWEN_REGIONS)),
        default="global",
        help="Alibaba plan region for --advisor-qwen (default: global).",
    )

    designer = parser.add_mutually_exclusive_group()
    designer.add_argument("--designer-model", help="Optional exact designer model ID.")
    designer.add_argument(
        "--designer-kimi",
        action="store_true",
        help="Use the installed Kimi Code subscription as K3 Designer through ACP.",
    )
    parser.add_argument(
        "--designer-effort",
        default="auto",
        help="Exact supported designer effort, or auto.",
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
    seat_settings = any(
        (
            args.executor_model,
            args.executor_agent,
            args.planner_model,
            args.planner_agent,
            args.planner_fable,
            args.advisor_model,
            args.advisor_agent,
            args.advisor_fable,
            args.advisor_qwen,
            args.designer_model,
            args.designer_kimi,
            args.executor_effort != "auto",
            args.planner_effort != "auto",
            args.advisor_effort != "auto",
            args.designer_effort != "auto",
        )
    )
    for action, selected in (
        ("--status", args.status),
        ("--repair", args.repair),
        ("--disable", args.disable),
        ("--prepare-qwen", args.prepare_qwen),
    ):
        if selected and seat_settings:
            raise ConfigurationError(f"{action} does not accept seat settings.")
    if args.repair and (
        args.replace_existing_policy or args.confirm_unlisted_models
    ):
        raise ConfigurationError(
            "--repair cannot be combined with setup replacement or model controls."
        )
    if args.prepare_qwen and (
        args.replace_existing_policy
        or args.confirm_unlisted_models
        or args.allow_incompatible_client
    ):
        raise ConfigurationError(
            "--prepare-qwen does not accept routing replacement or client controls."
        )
    if not args.status and not args.repair and not args.disable and not args.prepare_qwen and not (
        args.executor_model or args.executor_agent
    ):
        raise ConfigurationError(
            "Setup requires --executor-model or --executor-agent. "
            "Advisor omission means none. Designer omission means none."
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
    if args.advisor_qwen and args.advisor_effort not in {"auto", "native"}:
        raise ConfigurationError(
            "Qwen 3.8 Max Preview uses provider-native reasoning; omit "
            "--advisor-effort or use native."
        )
    if not (args.advisor_qwen or args.prepare_qwen) and args.qwen_region != "global":
        raise ConfigurationError("--qwen-region requires --advisor-qwen or --prepare-qwen.")
    if args.planner_fable and args.advisor_fable:
        raise ConfigurationError(
            "Planner and Advisor routes must be distinct; both cannot use Claude Fable 5."
        )
    if args.designer_kimi and args.designer_effort not in {"auto", "max"}:
        raise ConfigurationError("Kimi K3 Designer supports only max effort.")
    for label, value, pattern in (
        ("executor model", args.executor_model, MODEL_RE),
        ("planner model", args.planner_model, MODEL_RE),
        ("advisor model", args.advisor_model, MODEL_RE),
        ("designer model", args.designer_model, MODEL_RE),
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
        ("designer effort", args.designer_effort),
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
                        "version": "0.9.2",
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


def mcp_key_path(plugin_id: str, server: str) -> str:
    return (
        f"plugins.{json.dumps(plugin_id)}.mcp_servers."
        f"{json.dumps(server)}.enabled"
    )


def _state_plugin_id(state: dict[str, Any]) -> str:
    if state["schema"] >= 7:
        return state["plugin_id"]
    return LEGACY_PLUGIN_ID


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
    ) or planner_kind == advisor_kind == "fable" or (
        planner_kind == "model"
        and advisor_kind == "qwen_cli"
        and planner.get("model") == advisor.get("model")
    )
    if identical:
        raise ConfigurationError(
            "Planner and Advisor routes must be distinct (different direct model IDs, "
            "different custom-agent names, at most one Claude Fable 5 seat, and "
            "no direct Planner duplicate of the sealed Qwen Advisor)."
        )


def _read_state_snapshot(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return validated state and the digest of its exact observed bytes."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ConfigurationError(f"Could not inspect routing state {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConfigurationError(f"Routing state is not a regular file: {path}")
    if info.st_nlink != 1:
        raise ConfigurationError(f"Routing state has multiple hard links: {path}")
    try:
        payload = path.read_bytes()
        state = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read routing state {path}: {exc}") from exc
    try:
        validated = validate_routing_state(state)
    except RoutingStateError as exc:
        raise ConfigurationError("Saved routing state is invalid.") from exc
    return validated, hashlib.sha256(payload).hexdigest()


def _read_state(path: Path) -> dict[str, Any] | None:
    state, _ = _read_state_snapshot(path)
    return state


def _assert_state_digest(path: Path, expected_digest: str | None) -> None:
    _, observed_digest = _read_state_snapshot(path)
    if observed_digest != expected_digest:
        raise ConfigurationError(
            "Saved routing state changed concurrently; refusing state publication."
        )


def _rename_noreplace(src: Path, dst: Path) -> None:
    """Atomically consume *src* only when *dst* does not exist.

    State transactions use same-directory paths, which keeps the operation on
    one filesystem.  POSIX ``rename`` is deliberately not a fallback because
    it replaces an existing destination.
    """

    src = Path(src)
    dst = Path(dst)
    if os.path.abspath(src.parent) != os.path.abspath(dst.parent):
        raise OSError(
            errno.EXDEV,
            "atomic no-replace rename requires paths in the same directory",
            str(src),
            None,
            str(dst),
        )

    if sys.platform == "win32":
        try:
            # On Windows Python's os.rename maps to a native rename that fails
            # when the destination exists; unlike os.replace, it never opts in
            # to replacement.
            os.rename(src, dst)
        except OSError as exc:
            if exc.errno == errno.EEXIST or getattr(exc, "winerror", None) in {
                80,  # ERROR_FILE_EXISTS
                183,  # ERROR_ALREADY_EXISTS
            }:
                raise FileExistsError(
                    errno.EEXIST, os.strerror(errno.EEXIST), str(dst)
                ) from exc
            raise
        return

    if sys.platform.startswith("linux"):
        symbol_name = "renameat2"
        flags = 1  # RENAME_NOREPLACE
        arguments = (-100, os.fsencode(src), -100, os.fsencode(dst), flags)
    elif sys.platform == "darwin":
        symbol_name = "renamex_np"
        flags = 0x00000004  # RENAME_EXCL
        arguments = (os.fsencode(src), os.fsencode(dst), flags)
    else:
        raise ConfigurationError(
            f"Atomic no-replace rename is unsupported on {sys.platform}."
        )

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, symbol_name)
    except (OSError, AttributeError) as exc:
        raise ConfigurationError(
            f"Atomic no-replace rename is unavailable on {sys.platform}."
        ) from exc
    rename.restype = ctypes.c_int
    if symbol_name == "renameat2":
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
    else:
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]

    ctypes.set_errno(0)
    if rename(*arguments) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), str(dst))
    unsupported = {errno.ENOSYS, errno.EINVAL}
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    if error in unsupported:
        raise ConfigurationError(
            f"Atomic no-replace rename is unsupported by this {sys.platform} "
            f"filesystem or kernel: {os.strerror(error)}."
        )
    raise OSError(error, os.strerror(error), str(src), None, str(dst))


def _private_state_capture_path(path: Path) -> Path:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".cas-backup", dir=path.parent
    )
    os.close(descriptor)
    captured = Path(temporary)
    captured.unlink()
    return captured


def _restore_captured_state(
    path: Path, captured: Path, message: str, cause: BaseException
) -> None:
    """Restore by no-overwrite creation or retain a diagnosed recovery artifact."""

    try:
        _, captured_digest = _read_state_snapshot(captured)
    except BaseException as exc:
        raise StateTransactionIndeterminateError(
            f"{message} Could not establish the captured recovery digest; state "
            "is indeterminate. Run status before continuing."
        ) from exc
    try:
        _rename_noreplace(captured, path)
    except FileExistsError:
        raise ConfigurationError(
            f"{message} A newer routing-state pathname was preserved; captured "
            f"bytes remain at {captured}."
        ) from cause
    except BaseException as exc:
        try:
            _, canonical_digest = _read_state_snapshot(path)
            _, remaining_capture_digest = _read_state_snapshot(captured)
        except BaseException as read_exc:
            raise StateTransactionIndeterminateError(
                f"{message} Restoration outcome is indeterminate; recovery artifacts "
                "were preserved. Run status before continuing."
            ) from read_exc
        if (
            canonical_digest == captured_digest
            and remaining_capture_digest is None
        ):
            raise ConfigurationError(message) from cause
        raise ConfigurationError(
            f"{message} Automatic no-overwrite restoration failed; captured bytes "
            f"remain at {captured}: {exc}"
        ) from cause
    raise ConfigurationError(message) from cause


def _reconcile_state_capture(
    path: Path,
    captured: Path,
    expected_digest: str,
    cause: BaseException,
) -> None:
    try:
        _, canonical_digest = _read_state_snapshot(path)
        _, captured_digest = _read_state_snapshot(captured)
    except BaseException as read_exc:
        raise StateTransactionIndeterminateError(
            "Routing-state capture outcome is indeterminate; canonical and recovery "
            "paths were preserved. Run status before continuing."
        ) from read_exc
    if canonical_digest is None and captured_digest == expected_digest:
        return
    if canonical_digest == expected_digest and captured_digest is None:
        if not isinstance(cause, Exception):
            raise cause
        raise ConfigurationError(
            "Saved routing state changed concurrently; refusing state publication."
        ) from cause
    raise StateTransactionIndeterminateError(
        "Saved routing state changed concurrently; capture matched neither a "
        "completed nor a precommit move. All paths were preserved. Run status "
        "before continuing."
    ) from cause


def _capture_expected_state(
    path: Path, captured: Path, expected_digest: str
) -> None:
    """Move into a caller-owned capture pathname and validate exact captured bytes."""

    try:
        _rename_noreplace(path, captured)
    except FileExistsError as exc:
        raise ConfigurationError(
            "Saved routing state changed concurrently; refusing state publication."
        ) from exc
    except BaseException as exc:
        _reconcile_state_capture(path, captured, expected_digest, exc)
    try:
        _assert_state_digest(captured, expected_digest)
    except (ConfigurationError, OSError) as exc:
        _restore_captured_state(
            path,
            captured,
            "Saved routing state changed concurrently; refusing state publication.",
            exc,
        )
    except BaseException as exc:
        _reconcile_state_capture(path, captured, expected_digest, exc)


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


def _fsync_directory(path: Path) -> None:
    if sys.platform == "win32":
        # Python does not expose a supported directory-fsync handle on Windows.
        # Preserve the prior best-effort behavior instead of warning on every
        # successful Windows state transaction.
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _safe_warning(*parts: object) -> None:
    """Best-effort post-commit diagnostic that can never change the outcome."""

    try:
        sys.stderr.write("WARNING: " + "".join(str(part) for part in parts) + "\n")
        sys.stderr.flush()
    except BaseException:
        pass


def _write_state(
    path: Path,
    state: dict[str, Any],
    identity_guard: plugin_identity.PluginIdentityGuard,
    expected_digest: str | None,
) -> str:
    identity_guard.assert_unchanged("routing-state publication")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    payload_bytes = payload.encode("utf-8")
    payload_digest = hashlib.sha256(payload_bytes).hexdigest()
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    captured: Path | None = None
    committed = False
    preserve_transaction_evidence = False
    try:
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_digest is None:
            try:
                _rename_noreplace(temp_path, path)
            except FileExistsError as exc:
                raise ConfigurationError(
                    "Saved routing state changed concurrently; refusing state "
                    "publication."
                ) from exc
            except BaseException as exc:
                try:
                    _, canonical_digest = _read_state_snapshot(path)
                    _, remaining_temp_digest = _read_state_snapshot(temp_path)
                except BaseException as read_exc:
                    raise StateTransactionIndeterminateError(
                        "Routing-state publication outcome is indeterminate; state "
                        "paths were preserved. Run status before continuing."
                    ) from read_exc
                if (
                    canonical_digest == payload_digest
                    and remaining_temp_digest is None
                ):
                    committed = True
                    _safe_warning(
                        "routing state publication committed despite an interrupted "
                        "atomic rename: ",
                        exc,
                    )
                elif canonical_digest is None and remaining_temp_digest == payload_digest:
                    raise
                else:
                    raise StateTransactionIndeterminateError(
                        "Routing-state publication matched neither a completed nor a "
                        "precommit move; state paths were preserved. Run status before "
                        "continuing."
                    ) from exc
            else:
                committed = True
        else:
            captured = _private_state_capture_path(path)
            try:
                _capture_expected_state(path, captured, expected_digest)
            except StateTransactionIndeterminateError:
                raise
            except (ConfigurationError, OSError):
                raise
            except BaseException as exc:
                _reconcile_state_capture(path, captured, expected_digest, exc)
            try:
                _rename_noreplace(temp_path, path)
            except FileExistsError as exc:
                _restore_captured_state(
                    path,
                    captured,
                    "A newer routing-state pathname appeared during publication; "
                    "it was not overwritten.",
                    exc,
                )
            except BaseException as exc:
                try:
                    _, canonical_digest = _read_state_snapshot(path)
                    _, remaining_temp_digest = _read_state_snapshot(temp_path)
                    _, captured_digest = _read_state_snapshot(captured)
                except BaseException as read_exc:
                    raise StateTransactionIndeterminateError(
                        "Routing-state replacement outcome is indeterminate; state "
                        "and recovery paths were preserved. Run status before continuing."
                    ) from read_exc
                if (
                    canonical_digest == payload_digest
                    and remaining_temp_digest is None
                    and captured_digest == expected_digest
                ):
                    committed = True
                    _safe_warning(
                        "routing state replacement committed despite an interrupted "
                        "atomic rename: ",
                        exc,
                    )
                elif (
                    canonical_digest is None
                    and remaining_temp_digest == payload_digest
                    and captured_digest == expected_digest
                ):
                    _restore_captured_state(
                        path,
                        captured,
                        "A newer routing-state pathname appeared during publication; "
                        "it was not overwritten.",
                        exc,
                    )
                elif (
                    canonical_digest == expected_digest
                    and remaining_temp_digest == payload_digest
                    and captured_digest is None
                ):
                    raise
                else:
                    raise StateTransactionIndeterminateError(
                        "Routing-state replacement matched neither a completed nor a "
                        "recoverable precommit move; all paths were preserved. Run "
                        "status before continuing."
                    ) from exc
            else:
                committed = True
        try:
            _fsync_directory(path.parent)
        except BaseException as exc:
            recovery: tuple[object, ...] = ()
            if captured is not None:
                recovery = (
                    " Prior-state recovery remains at ",
                    captured,
                    ".",
                )
            _safe_warning(
                "routing state was published, but directory durability could not "
                "be confirmed.",
                *recovery,
                " Error: ",
                exc,
            )
        else:
            if captured is None:
                return payload_digest
            try:
                captured.unlink()
            except BaseException as exc:
                _safe_warning(
                    "routing state was published, but the prior-state recovery "
                    "artifact remains at ",
                    captured,
                    ": ",
                    exc,
                )
            else:
                try:
                    _fsync_directory(path.parent)
                except BaseException as exc:
                    _safe_warning(
                        "routing state was published and prior-state "
                        "recovery cleanup completed, but cleanup directory durability "
                        "could not be confirmed: ",
                        exc,
                    )
    except StateTransactionIndeterminateError:
        preserve_transaction_evidence = True
        raise
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        except BaseException:
            if not committed:
                raise
        if not preserve_transaction_evidence:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                # Never mask the publication result or its primary failure. A
                # successful rename already consumed this path.
                pass
            except BaseException:
                if not committed:
                    raise
    return payload_digest


def _remove_state(
    path: Path,
    identity_guard: plugin_identity.PluginIdentityGuard,
    expected_digest: str | None,
) -> None:
    identity_guard.assert_unchanged("routing-state removal")
    if expected_digest is None:
        _assert_state_digest(path, None)
        return
    captured = _private_state_capture_path(path)
    try:
        _capture_expected_state(path, captured, expected_digest)
    except StateTransactionIndeterminateError:
        raise
    except (ConfigurationError, OSError):
        raise
    except BaseException as exc:
        _reconcile_state_capture(path, captured, expected_digest, exc)
    if path.exists():
        raise ConfigurationError(
            "A newer routing-state pathname appeared during removal; it was preserved."
        )
    try:
        _fsync_directory(path.parent)
    except BaseException as exc:
        _safe_warning(
            "routing state was removed, but directory durability could not "
            "be confirmed. Prior-state recovery remains at ",
            captured,
            ". Error: ",
            exc,
        )
        return
    try:
        captured.unlink()
    except BaseException as exc:
        _safe_warning(
            "routing state was removed, but the prior-state recovery artifact "
            "remains at ",
            captured,
            ": ",
            exc,
        )
        return
    try:
        _fsync_directory(path.parent)
    except BaseException as exc:
        _safe_warning(
            "routing state was removed and prior-state recovery cleanup "
            "completed, but cleanup directory durability could not be confirmed: ",
            exc,
        )


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


def select_kimi_server() -> str:
    for server, (launcher, prefix) in KIMI_SERVERS.items():
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
        "Kimi K3 Designer requires a Python 3.11+ launcher named python3, python, or py."
    )


def select_qwen_server() -> str:
    for server, (launcher, prefix) in QWEN_SERVERS.items():
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
        "Qwen Advisor requires a Python 3.11+ launcher named python3, python, or py."
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


def verify_kimi_prerequisites() -> dict[str, str]:
    try:
        from kimi_designer_mcp import KimiDesignerError, check_prerequisites
    except ImportError as exc:  # pragma: no cover - corrupt package
        raise ConfigurationError("The bundled Kimi K3 Designer bridge is missing.") from exc
    try:
        return check_prerequisites()
    except KimiDesignerError as exc:
        raise ConfigurationError(str(exc)) from exc


def verify_qwen_prerequisites(region: str) -> dict[str, str]:
    try:
        from qwen_advisor_mcp import QwenAdvisorError, check_prerequisites
    except ImportError as exc:  # pragma: no cover - corrupt package
        raise ConfigurationError("The bundled Qwen Advisor bridge is missing.") from exc
    try:
        return check_prerequisites(region)
    except QwenAdvisorError as exc:
        raise ConfigurationError(str(exc)) from exc


def prepare_qwen_credential(
    codex_home_override: Path | None,
    region: str,
    *,
    apply: bool,
) -> int:
    home = (
        codex_home_override.expanduser().absolute()
        if codex_home_override is not None
        else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        .expanduser()
        .absolute()
    )
    selected = QWEN_REGION_CONFIG[region]
    provider = selected["credential_provider"]
    target = home / "codex-orchestration" / "bin" / external_credentials.HELPER_NAME
    print(f"Qwen Advisor region: {region}")
    print(f"Credential store label: {provider}")
    print(f"Stable helper: {target}")
    if not apply:
        print(
            "Dry run only. Re-run with --prepare-qwen --apply to install or verify "
            "the helper and receive the trusted-terminal enrollment command."
        )
        return 0
    try:
        helper, _ = external_credentials.install_stable_helper(home)
    except external_credentials.CredentialSetupError as exc:
        raise ConfigurationError(f"Could not prepare the Qwen credential helper: {exc}") from exc
    if external_credentials.credential_ready(helper, provider):
        print("Qwen Advisor credential: configured in the OS credential store")
        return 0
    command = external_credentials.enrollment_command(helper, provider)
    rendered = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
    print("Qwen Advisor credential: not configured")
    print("Run this command in a trusted local terminal; the secret prompt is hidden:")
    print(rendered)
    return 0


def _route_summary(route: dict[str, Any]) -> str:
    if route["kind"] == "agent":
        return f"custom agent {route['agent']}"
    if route["kind"] == "fable":
        return f"Claude Fable 5 {route['effort']}"
    if route["kind"] == "kimi_cli":
        return "Kimi K3 max (Kimi Code subscription)"
    if route["kind"] == "qwen_cli":
        return f"Qwen 3.8 Max Preview native ({route['region']} plan)"
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
    designer: dict[str, Any] | None = None,
) -> tuple[str, str]:
    has_direct_route = executor["kind"] == "model" or (
        planner is not None and planner["kind"] == "model"
    ) or (
        advisor is not None and advisor["kind"] == "model"
    ) or (
        designer is not None and designer["kind"] == "model"
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
    designer_mode = (
        "After any required plan approval, the root may send bounded visual, UX, "
        "interaction, information-architecture, or design-system work to the "
        "configured Designer. The root supplies approved requirements, exact "
        "deliverables, constraints, and any owned design artifacts. Designer may "
        "edit only explicitly delegated design artifacts; otherwise it returns a "
        "design handoff. It does not revise the canonical plan, change implementation "
        "code, or release Executor. The root validates the handoff and decides what "
        "Executor receives."
        if designer is not None
        else (
            "No Designer is configured. The root owns design decisions or delegates "
            "them through ordinary bounded Executor work when useful."
        )
    )
    mode = f"""{MANAGED_MARKER}
This adds model routing to Codex's existing multi-agent flow; it is not a second scheduler.

If you are the root task model, you are the orchestrator. Own intent, planning, architecture, decomposition, delegation, integration, review, final verification, and the user-facing answer. Codex still decides whether a plan or subagent helps, how many independent slices exist, and what can run safely in parallel. Keep simple, tightly coupled, context-heavy, or root-owned work with the root. Do not delegate merely to prove the policy is active.

{planner_mode}

{advisor_mode}

{designer_mode}

The root owns the plan version, cumulative findings ledger, review count, validation, adjudication, and release to Executor. There is no Finalizer seat. For Advisor rounds two through five, send only the current plan and version plus a compact cumulative ledger, not prior transcripts. Ask the Advisor to confirm or contest dispositions without blindly repeating accepted findings. Reject a stale plan version or an invalid or incomplete ledger and halt before Executor.

On PLAN_REVISE, record the latest finding IDs before revision. After the Planner returns, validate and merge each INCORPORATED or reasoned REJECTED disposition into the cumulative ledger before another Advisor call. A round-five PLAN_REVISE halts before Executor and produces a non-approval artifact containing the latest plan and version, full ledger, latest findings, and choices available to the user. It must not claim approval. Any required Planner or Advisor route failure also halts before Executor. Only an explicit current-task best-effort instruction changes failure handling: Planner failure permits the root to take over planning for the remaining rounds; Advisor failure may proceed only with the result labeled NOT_ADVISOR_APPROVED. No best-effort setting is persisted.

When executor delegation materially improves speed, cost, quality, or context isolation, use only the configured executor route. Give each executor one bounded, self-contained packet with objective, relevant facts, constraints, owned files or read-only scope, dependencies, acceptance criteria, verification, and handoff format. Inspect every handoff, integrate it, and run final checks yourself.

Explicit user instructions win, including no-subagents and task-local seat overrides. An explicit current-task choice may override a saved Advisor or Executor model, effort, or agent route. When no Planner route is configured, the root owns planning; a fresh direct Advisor using the same model ID as the root is not a duplicate configured Planner route. Persistent and task-local Planner and Advisor routes that are both configured must still remain distinct: reject two configured direct routes with the same model ID, the same custom-agent name, Fable in both seats, or a direct Qwen Planner paired with the sealed Qwen Advisor. This policy does not create or change a Goal, weaken approvals, alter permissions, or force a worker count.

Planner and Advisor are policy-isolated, root-directed seats: they cannot contact each other, Designer, or Executors, spawn descendants, edit files, execute work, or release Executor. They return only to the root. Designer is also root-directed: it cannot contact Planner, Advisor, or Executor, spawn descendants, redesign the root plan, change implementation code, or release Executor. Designer may edit only explicitly delegated design artifacts. Bundled MCP requests do not carry caller identity, so caller isolation is instruction-enforced even when a bridge disables tools and persistence. If you are a spawned child, stay inside the supplied packet, report only to the root, never call planning tools, and never spawn descendants. An Executor never redesigns the root plan or contacts Planner, Advisor, or Designer.
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
    if advisor is not None and advisor["kind"] == "qwen_cli":
        advisor_hint = (
            "For an advisor review, call `review_plan` from MCP server "
            f"{json.dumps(advisor['server'])} with the round's self-contained packet. "
            "This is a sealed read-only root tool call through Alibaba's Token Plan "
            "JSON API, not a "
            "spawned child. Require PLAN_APPROVED or PLAN_REVISE and runtime model "
            "qwen3.8-max-preview; fail closed unless the user explicitly made "
            "Advisor failure best-effort for the current task."
        )
    elif advisor is not None and advisor["kind"] == "fable":
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
    if designer is not None and designer["kind"] == "kimi_cli":
        designer_hint = (
            "For delegated design work, call `create_design_handoff` from MCP server "
            f"{json.dumps(designer['server'])}. This is a read-only root tool call, "
            "not a spawned child. Send approved requirements, bounded deliverables, "
            "constraints, and the required handoff format. Require DESIGN_HANDOFF and "
            "runtime_model kimi-code/k3; fail closed on bridge or identity failure."
        )
    elif designer is not None:
        designer_hint = (
            "For delegated design work, call this tool with "
            f"{_spawn_route(designer)}, fork_turns = \"none\". Send approved "
            "requirements, bounded deliverables, explicit design-artifact ownership, "
            "constraints, and the required handoff format."
        )
    else:
        designer_hint = "No Designer route is configured."
    usage = f"""{MANAGED_MARKER}
If you are the root task model, you are the orchestrator. Apply these routes only to children you decide to create.

For delegated executor work, call this tool with {_spawn_route(executor)}, fork_turns = "none". Send a self-contained task packet.

{planner_hint}

{advisor_hint}

{designer_hint}

{provider_guard}

Never use fork_turns = "all" with model, reasoning_effort, or agent_type: a full-history fork inherits the root route and rejects those overrides. Never invent or substitute GPT-5.5, Terra, Qwen, Fable, or any other model solely to create provider or model diversity. If the exact explicit route is unavailable, report it unavailable. A user's explicit current-task model, effort, agent, or no-subagents instruction overrides this saved default. When no Planner route is configured, root-owned planning is not a configured Planner route, even if the root and a fresh direct Advisor use the same model ID. A task-local Planner and Advisor must still be distinct when both are configured; persistent configured Planner/Advisor routes remain distinct too: reject the same direct model ID, the same custom-agent name, Fable in both seats, or a direct Qwen Planner paired with the sealed Qwen Advisor.

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


def _current_values(config: dict[str, Any], plugin_id: str) -> dict[str, Any]:
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
                plugin_id,
                "mcp_servers",
                server,
                "enabled",
            )
            for server in BUNDLED_MCP_SERVERS
        },
    }


def _is_managed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(MANAGED_MARKER)


def _strict_equal(left: Any, right: Any) -> bool:
    """Compare config values without Python's bool/integer equivalence."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _restored_snapshot_value(saved: Any, fallback: Any) -> Any:
    if not isinstance(saved, dict) or not saved.get("known"):
        return fallback
    return saved.get("value") if saved.get("present") else MISSING


def _disable_expected_values(
    state: dict[str, Any] | None, current: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    expected = dict(current)
    expected["mcp"] = dict(current["mcp"])
    compare_feature = False
    if state is None:
        for field in ("mode", "usage"):
            if _is_managed(current[field]):
                expected[field] = MISSING
        return expected, compare_feature

    previous = state.get("previous")
    if not isinstance(previous, dict):
        raise ConfigurationError("Routing state has no restore data.")
    scalar_origin = state.get("scalar_origin")
    if isinstance(scalar_origin, bool):
        expected["feature"] = scalar_origin
        compare_feature = True
        for field in ("mode", "usage", "metadata", "namespace"):
            expected[field] = MISSING
    else:
        for field in ("mode", "usage", "metadata", "namespace"):
            expected[field] = _restored_snapshot_value(
                previous.get(field), current[field]
            )
    previous_mcp = previous.get("mcp")
    if isinstance(previous_mcp, dict):
        for server, saved in previous_mcp.items():
            expected["mcp"][server] = _restored_snapshot_value(
                saved, current["mcp"].get(server, MISSING)
            )
    return expected, compare_feature


def _disable_values_match(
    expected: dict[str, Any], observed: dict[str, Any], compare_feature: bool
) -> bool:
    fields = ("mode", "usage", "metadata", "namespace")
    if compare_feature and not _strict_equal(expected["feature"], observed["feature"]):
        return False
    if any(not _strict_equal(expected[field], observed[field]) for field in fields):
        return False
    return all(
        _strict_equal(expected_value, observed["mcp"].get(server, MISSING))
        for server, expected_value in expected["mcp"].items()
    )


def _managed_compensation_edits(
    state: dict[str, Any], current: dict[str, Any], plugin_id: str
) -> list[dict[str, Any]]:
    """Recreate exactly the validated managed config without crossing namespaces."""

    if isinstance(state.get("scalar_origin"), bool):
        edits = [
            {
                "keyPath": "features.multi_agent_v2",
                "value": current["feature"],
                "mergeStrategy": "replace",
            }
        ]
    else:
        paths = {
            "metadata": "features.multi_agent_v2.hide_spawn_agent_metadata",
            "namespace": "features.multi_agent_v2.tool_namespace",
            "mode": "features.multi_agent_v2.multi_agent_mode_hint_text",
            "usage": "features.multi_agent_v2.usage_hint_text",
        }
        edits = [
            {
                "keyPath": path,
                "value": current[field],
                "mergeStrategy": "replace",
            }
            for field, path in paths.items()
        ]

    managed = state.get("managed")
    managed_mcp = managed.get("mcp") if isinstance(managed, dict) else None
    if isinstance(managed_mcp, dict):
        for server in managed_mcp:
            if server not in BUNDLED_MCP_SERVERS:
                raise ConfigurationError(
                    f"Saved routing state names an unmanaged MCP server: {server}"
                )
            value = current["mcp"].get(server, MISSING)
            edits.append(
                {
                    "keyPath": mcp_key_path(plugin_id, server),
                    "value": None if value is MISSING else value,
                    "mergeStrategy": "replace",
                }
            )
    return edits


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
    if managed_mcp is not None and not all(
        _strict_equal(current["mcp"].get(server, MISSING), enabled)
        for server, enabled in managed_mcp.items()
    ):
        return False
    if isinstance(state.get("scalar_origin"), bool):
        return _strict_equal(current["feature"], state.get("managed_feature"))
    return True


def _batch_write(
    app: AppServer,
    edits: list[dict[str, Any]],
    version: str | None,
    *,
    identity_guard: plugin_identity.PluginIdentityGuard,
    identity_phase: str,
    reload_user_config: bool,
) -> dict[str, Any]:
    identity_guard.assert_unchanged(identity_phase)
    return app.request(
        "config/batchWrite",
        {
            "edits": edits,
            "expectedVersion": version,
            "reloadUserConfig": reload_user_config,
        },
    )


def _status_unbuffered(
    target: Path,
    codex_home: Path | None,
    binaries: list[Path],
    require_effective: bool,
    publish: Any = None,
) -> int:
    clients_compatible = True
    for binary in binaries:
        supported, detail = supports_native_policy(binary)
        label = "compatible" if supported else f"incompatible ({detail})"
        print(f"Client: {binary} ({binary_version(binary)}) — {label}")
        clients_compatible = clients_compatible and supported
    with contextlib.ExitStack() as stack:
        app = stack.enter_context(AppServer(target, codex_home))
        stack.enter_context(_transaction_directory_lock(app.codex_home))
        state_path = app.codex_home / STATE_FILENAME
        state, state_digest = _read_state_snapshot(state_path)
        _validate_state_config(state, app.config_path)
        saved_plugin_id = _state_plugin_id(state) if state is not None else None
        identity_guard = stack.enter_context(
            plugin_identity.guard_plugin_identity(
                target,
                EXECUTING_PLUGIN_ROOT,
                "status",
                saved_plugin_id=saved_plugin_id,
                codex_home=app.codex_home,
            )
        )
        plugin_id = identity_guard.selected_plugin_id
        executing_plugin_id = identity_guard.executing_plugin_id
        workspace = Path.cwd().resolve()
        read_result = app.request(
            "config/read",
            {"includeLayers": True, "cwd": str(workspace)},
        )
        config, _ = _user_layer(read_result)
        current = _current_values(config, plugin_id)
        effective_config = read_result.get("config")
        effective = _current_values(
            effective_config if isinstance(effective_config, dict) else {}, plugin_id
        )
        state_owner_matches = state is None or _state_plugin_id(state) == plugin_id
        execution_matches = plugin_id == executing_plugin_id
        managed_pair = _is_managed(current["mode"]) and _is_managed(
            current["usage"]
        )
        state_matches = (
            state is not None
            and state_owner_matches
            and _managed_matches(state, current)
        )
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
        print(f"Plugin identity: {plugin_id}")
        print(f"Executing plugin identity: {executing_plugin_id}")
        if not execution_matches:
            print(
                "Plugin identity mismatch: saved namespace owner "
                f"{plugin_id}; executing cache owner {executing_plugin_id}"
            )
        if routing_state == "managed fields conflict with local restore state":
            print(
                "Recovery: run --repair as a dry run only when the saved plugin "
                "policy should replace drifted managed hints."
            )
        print(
            "V2 activation: not inferred by the installer; choose a v2 root "
            "model such as current Sol or Terra"
        )
        print(f"Config: {app.config_path}")
        fable_available = True
        kimi_available = True
        qwen_available = True
        if state_matches:
            print(f"Executor: {_route_summary(state['executor'])}")
            planner = state.get("planner")
            advisor = state.get("advisor")
            designer = state.get("designer")
            print(f"Planner: {_route_summary(planner) if planner else 'root'}")
            print(f"Advisor: {_route_summary(advisor) if advisor else 'none'}")
            print(f"Designer: {_route_summary(designer) if designer else 'none'}")
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
            if isinstance(advisor, dict) and advisor.get("kind") == "qwen_cli":
                try:
                    ready = verify_qwen_prerequisites(advisor["region"])
                except ConfigurationError as exc:
                    qwen_available = False
                    print(f"Qwen Advisor: unavailable — {exc}")
                else:
                    print(
                        "Qwen Advisor: ready — OS credential store and sealed "
                        f"{ready['protocol']} {advisor['region']} route; "
                        "no model call made"
                    )
            if isinstance(designer, dict) and designer.get("kind") == "kimi_cli":
                try:
                    ready = verify_kimi_prerequisites()
                except ConfigurationError as exc:
                    kimi_available = False
                    print(f"Kimi K3 Designer: unavailable — {exc}")
                else:
                    print(
                        "Kimi K3 Designer: ready — Kimi Code OAuth subscription via "
                        f"ACP; Kimi {ready['kimi_version']}, acpx {ready['acpx_version']}; "
                        "no model call made"
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
            and state_owner_matches
            and execution_matches
            and agent_routes_available
            and fable_available
            and kimi_available
            and qwen_available
            and not role_issues
            and not orphaned_roles
        )
        identity_guard.assert_unchanged("status publication")
        _assert_state_digest(state_path, state_digest)
        if publish is not None:
            publish()
    return 1 if require_effective and not healthy else 0


def _status(
    target: Path,
    codex_home: Path | None,
    binaries: list[Path],
    require_effective: bool,
) -> int:
    """Publish status only after the identity guard's final recheck succeeds."""

    output = io.StringIO()
    destination = sys.stdout
    with contextlib.redirect_stdout(output):
        result = _status_unbuffered(
            target,
            codex_home,
            binaries,
            require_effective,
            lambda: print(output.getvalue(), end="", file=destination),
        )
    return result


def _prepare_setup_state(
    config: dict[str, Any],
    existing_state: dict[str, Any] | None,
    plugin_id: str,
    mode: str,
    usage: str,
    executor: dict[str, Any],
    planner: dict[str, Any] | None,
    advisor: dict[str, Any] | None,
    designer: dict[str, Any] | None,
    config_path: Path,
    replace_existing: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    current = _current_values(config, plugin_id)
    feature = current["feature"]
    scalar_feature = isinstance(feature, bool)

    if existing_state is not None:
        existing_plugin_id = _state_plugin_id(existing_state)
        existing_managed = existing_state.get("managed")
        legacy_mcp = (
            existing_state["schema"] < 7
            and isinstance(existing_managed, dict)
            and isinstance(existing_managed.get("mcp"), dict)
        )
        if existing_state["schema"] >= 7 and existing_plugin_id != plugin_id:
            raise ConfigurationError(
                "Saved routing state belongs to a different plugin identity; "
                "refusing to move restore data between marketplace namespaces."
            )
        if legacy_mcp and plugin_id != LEGACY_PLUGIN_ID:
            raise ConfigurationError(
                "Legacy MCP restore state belongs to the historical canonical plugin "
                "identity. Disable it before setting up a different marketplace identity."
            )
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
    bundled_routes = [
        route
        for route in (planner, advisor, designer)
        if isinstance(route, dict)
        and route.get("kind") in {"fable", "kimi_cli", "qwen_cli"}
    ]
    manage_mcp = (
        bool(bundled_routes)
        or isinstance(existing_managed, dict)
        and isinstance(existing_managed.get("mcp"), dict)
    )
    managed_mcp: dict[str, bool] | None = None
    if manage_mcp:
        previous_mcp = previous.get("mcp")
        if not isinstance(previous_mcp, dict):
            previous_mcp = {}
        selected_servers = {route["server"] for route in bundled_routes}
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
        touched.update(selected_servers)
        for server in touched:
            if server not in previous_mcp:
                previous_mcp[server] = snapshot(current["mcp"][server])
        previous["mcp"] = previous_mcp
        managed_mcp = {
            server: server in selected_servers
            for server in BUNDLED_MCP_SERVERS
            if server in touched
        }
        for server, enabled in managed_mcp.items():
            edits.append(
                {
                    "keyPath": mcp_key_path(plugin_id, server),
                    "value": enabled,
                    "mergeStrategy": "replace",
                }
            )
            rollback_edit = snapshot_edit(
                mcp_key_path(plugin_id, server), snapshot(current["mcp"][server])
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
        "plugin_id": plugin_id,
        "executor": executor,
        "planner": planner,
        "advisor": advisor,
        "designer": designer,
        "managed": managed,
        "previous": previous,
        "scalar_origin": scalar_origin,
        "managed_feature": managed_feature,
    }
    return state, edits, rollback


def _restore_pre_repair_hints(
    app: AppServer,
    rollback: list[dict[str, Any]],
    expected: dict[str, Any],
    version: str | None,
    workspace: Path,
    identity_guard: plugin_identity.PluginIdentityGuard,
    plugin_id: str,
) -> None:
    result = _batch_write(
        app,
        rollback,
        version,
        identity_guard=identity_guard,
        identity_phase="repair rollback",
        reload_user_config=True,
    )
    if result.get("status") not in {"ok", "okOverridden"}:
        raise ConfigurationError(
            f"unexpected rollback status {result.get('status')!r}"
        )
    read_result = app.request(
        "config/read",
        {"includeLayers": True, "cwd": str(workspace)},
    )
    user_config, _ = _user_layer(read_result)
    current = _current_values(user_config, plugin_id)
    if any(current[field] != value for field, value in expected.items()):
        raise ConfigurationError("pre-repair hint restoration could not be verified")


def _repair(
    app: AppServer,
    config: dict[str, Any],
    version: str | None,
    state: dict[str, Any] | None,
    state_digest: str | None,
    workspace: Path,
    identity_guard: plugin_identity.PluginIdentityGuard,
    plugin_id: str,
    apply: bool,
) -> int:
    """Restore only saved managed hint bytes after exact drift validation."""

    if state is None:
        raise ConfigurationError(
            "Routing repair requires valid saved plugin state; run status first."
        )
    if _state_plugin_id(state) != plugin_id:
        raise ConfigurationError(
            "Routing repair state belongs to a different plugin identity; refusing "
            "to repair across marketplace namespaces."
        )
    managed = state.get("managed")
    if not isinstance(managed, dict):
        raise ConfigurationError("Routing repair state has no managed values.")
    current = _current_values(config, plugin_id)
    drifted = [
        field for field in ("mode", "usage") if current[field] != managed[field]
    ]
    if not drifted:
        if _managed_matches(state, current):
            identity_guard.assert_unchanged("repair no-op publication")
            print("Native routing policy already matches its saved managed state.")
            return 0
        raise ConfigurationError(
            "Routing repair permits only managed mode/usage drift; another owned "
            "control or Fable launcher setting changed."
        )

    if any(not _is_managed(current[field]) for field in ("mode", "usage")):
        raise ConfigurationError(
            "Routing repair requires both live hints to retain the managed ownership "
            "marker; user-authored or missing text was preserved."
        )
    controls_match = (
        current["metadata"] is False
        and current["namespace"] == ROUTING_TOOL_NAMESPACE
    )
    managed_mcp = managed.get("mcp")
    mcp_matches = managed_mcp is None or all(
        _strict_equal(current["mcp"].get(server, MISSING), enabled)
        for server, enabled in managed_mcp.items()
    )
    if not controls_match or not mcp_matches:
        raise ConfigurationError(
            "Routing repair permits only managed mode/usage drift; another owned "
            "control or Fable launcher setting changed."
        )

    if isinstance(state.get("scalar_origin"), bool):
        feature = current["feature"]
        expected_feature = state.get("managed_feature")
        if not isinstance(feature, dict) or not isinstance(expected_feature, dict):
            raise ConfigurationError(
                "Routing repair cannot validate the converted multi_agent_v2 table."
            )
        repaired_feature = dict(feature)
        repaired_feature["multi_agent_mode_hint_text"] = managed["mode"]
        repaired_feature["usage_hint_text"] = managed["usage"]
        if not _strict_equal(repaired_feature, expected_feature):
            raise ConfigurationError(
                "Routing repair permits only managed mode/usage drift; the converted "
                "multi_agent_v2 table has other changes."
            )

    key_paths = {
        "mode": "features.multi_agent_v2.multi_agent_mode_hint_text",
        "usage": "features.multi_agent_v2.usage_hint_text",
    }
    edits = [
        {
            "keyPath": key_paths[field],
            "value": managed[field],
            "mergeStrategy": "replace",
        }
        for field in drifted
    ]
    rollback = [
        {
            "keyPath": key_paths[field],
            "value": current[field],
            "mergeStrategy": "replace",
        }
        for field in drifted
    ]
    rendered = " and ".join(drifted)
    label = "hint" if len(drifted) == 1 else "hints"
    print(f"Config: {app.config_path}")
    print(f"Will restore saved managed {rendered} {label} only.")
    print(
        "Will preserve the restore snapshot, seat routes, namespace, spawn metadata, "
        "Fable launcher enablement, credentials, chats, and sessions."
    )
    fable_configured = any(
        isinstance(route, dict) and route.get("kind") == "fable"
        for route in (state.get("planner"), state.get("advisor"))
    )
    if fable_configured:
        print(
            "This repair does not change Claude Fable 5 authentication or request "
            "re-authentication."
        )
    if not apply:
        identity_guard.assert_unchanged("repair dry-run publication")
        print("Dry run only. Re-run with --repair --apply after reviewing this preview.")
        return 0

    result = _batch_write(
        app,
        edits,
        version,
        identity_guard=identity_guard,
        identity_phase="repair write",
        reload_user_config=True,
    )
    if result.get("status") == "okOverridden":
        try:
            _restore_pre_repair_hints(
                app,
                rollback,
                {field: current[field] for field in drifted},
                result.get("version"),
                workspace,
                identity_guard,
                plugin_id,
            )
        except ConfigurationError as rollback_exc:
            raise ConfigurationError(
                "A higher-priority layer overrides the repaired policy, and restoring "
                f"the pre-repair hints failed: {rollback_exc}"
            ) from rollback_exc
        raise ConfigurationError(
            "A higher-priority layer overrides the repaired policy; the pre-repair "
            "managed hints were restored."
        )
    if result.get("status") != "ok":
        raise ConfigurationError(
            f"Unexpected config write status: {result.get('status')!r}"
        )

    verify_result = app.request(
        "config/read",
        {"includeLayers": True, "cwd": str(workspace)},
    )
    verify_config, verify_version = _user_layer(verify_result)
    verify_current = _current_values(verify_config, plugin_id)
    effective_config = verify_result.get("config")
    effective_current = _current_values(
        effective_config if isinstance(effective_config, dict) else {}, plugin_id
    )
    if not _managed_matches(state, verify_current):
        raise ConfigurationError(
            "The user routing fields changed after Codex accepted the repair. That "
            "newer edit was preserved; saved restore state remains available."
        )
    if not _managed_matches(state, effective_current):
        try:
            _restore_pre_repair_hints(
                app,
                rollback,
                {field: current[field] for field in drifted},
                verify_version,
                workspace,
                identity_guard,
                plugin_id,
            )
        except ConfigurationError as rollback_exc:
            raise ConfigurationError(
                "Repair readback was overridden, and restoring the pre-repair hints "
                f"failed: {rollback_exc}"
            ) from rollback_exc
        raise ConfigurationError(
            "Repair did not become effective in this workspace; the pre-repair "
            "managed hints were restored."
        )

    state_path = app.codex_home / STATE_FILENAME
    _, current_state_digest = _read_state_snapshot(state_path)
    if current_state_digest != state_digest:
        raise ConfigurationError(
            "Saved routing state changed concurrently during repair. It was not "
            "overwritten; run status before any further routing change."
        )

    identity_guard.assert_unchanged("repair success publication")
    print(
        "Native routing policy repaired; fully quit and reopen Codex, then start a "
        "new task so the current policy and MCP bridge are loaded together."
    )
    return 0


def _disable(
    app: AppServer,
    config: dict[str, Any],
    version: str | None,
    state: dict[str, Any] | None,
    state_digest: str | None,
    workspace: Path,
    identity_guard: plugin_identity.PluginIdentityGuard,
    plugin_id: str,
    apply: bool,
) -> int:
    current = _current_values(config, plugin_id)
    state_path = app.codex_home / STATE_FILENAME
    managed_compensation: list[dict[str, Any]] | None = None
    if state is None:
        managed_mode = _is_managed(current["mode"])
        managed_usage = _is_managed(current["usage"])
        if not (managed_mode or managed_usage):
            identity_guard.assert_unchanged("disable inactive publication")
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
        managed_compensation = _managed_compensation_edits(
            state, current, plugin_id
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
                    snapshot_edit(
                        mcp_key_path(plugin_id, server), previous_mcp[server]
                    )
                    for server in previous_mcp
                )
                if edit is not None
            )
        print("Will restore the pre-setup values of every owned routing field.")
    if not apply:
        identity_guard.assert_unchanged("disable dry-run publication")
        print("Dry run only. Re-run with --disable --apply after reviewing this preview.")
        return 0
    result = _batch_write(
        app,
        edits,
        version,
        identity_guard=identity_guard,
        identity_phase="disable write",
        reload_user_config=True,
    )
    if result.get("status") == "okOverridden":
        raise ConfigurationError(
            "A higher-priority layer overrides the restored routing values; saved "
            "restore state was retained."
        )
    if result.get("status") != "ok":
        raise ConfigurationError(f"Unexpected config write status: {result.get('status')!r}")
    expected, compare_feature = _disable_expected_values(state, current)
    read_result = app.request(
        "config/read", {"includeLayers": True, "cwd": str(workspace)}
    )
    user_config, post_disable_version = _user_layer(read_result)
    user_current = _current_values(user_config, plugin_id)
    if not _disable_values_match(expected, user_current, compare_feature):
        raise ConfigurationError(
            "The user routing fields changed after Codex accepted disable; the newer "
            "edit was preserved and saved restore state remains available."
        )
    effective_config = read_result.get("config")
    effective_current = _current_values(
        effective_config if isinstance(effective_config, dict) else {}, plugin_id
    )
    if not _disable_values_match(expected, effective_current, compare_feature):
        raise ConfigurationError(
            "The restored routing values are overridden in this workspace; saved "
            "restore state remains available."
        )
    if state is None:
        _remove_state(state_path, identity_guard, state_digest)
    else:
        try:
            _remove_state(state_path, identity_guard, state_digest)
        except BaseException as removal_exc:
            try:
                _, observed_state_digest = _read_state_snapshot(state_path)
            except BaseException as state_read_exc:
                raise ConfigurationError(
                    "Disable config restoration completed, but routing-state removal "
                    "and canonical-state readback failed. Config and state may be "
                    "inconsistent. Run status before continuing."
                ) from state_read_exc

            if observed_state_digest is None:
                if not isinstance(removal_exc, Exception):
                    raise
                raise ConfigurationError(
                    "Native routing config was disabled and canonical restore state "
                    "was removed despite a removal diagnostic; config and state are "
                    "paired."
                ) from removal_exc

            if observed_state_digest == state_digest:
                try:
                    if managed_compensation is None:
                        raise ConfigurationError(
                            "managed compensation edits were unavailable"
                        )
                    compensation_result = _batch_write(
                        app,
                        managed_compensation,
                        post_disable_version,
                        identity_guard=identity_guard,
                        identity_phase="disable state-failure compensation",
                        reload_user_config=True,
                    )
                    if compensation_result.get("status") not in {
                        "ok",
                        "okOverridden",
                    }:
                        raise ConfigurationError(
                            "unexpected compensation status "
                            f"{compensation_result.get('status')!r}"
                        )
                    compensation_read = app.request(
                        "config/read",
                        {"includeLayers": True, "cwd": str(workspace)},
                    )
                    compensation_config, _ = _user_layer(compensation_read)
                    compensation_current = _current_values(
                        compensation_config, plugin_id
                    )
                    if not _managed_matches(state, compensation_current):
                        raise ConfigurationError(
                            "forward-compensated config did not match saved managed "
                            "state"
                        )
                except BaseException as compensation_exc:
                    raise ConfigurationError(
                        "Disable config restoration completed, but state removal did "
                        "not commit and forward managed-config compensation failed. "
                        "Config and state may be inconsistent. Run status before "
                        "continuing."
                    ) from compensation_exc
                if not isinstance(removal_exc, Exception):
                    raise
                raise ConfigurationError(
                    "Routing-state removal failed before commit; the exact managed "
                    "config and saved state were re-paired."
                ) from removal_exc

            raise ConfigurationError(
                "Disable config restoration completed, but canonical routing state "
                "matched neither the exact saved state nor committed removal. Config "
                "and state may be inconsistent. Run status before continuing."
            ) from removal_exc
    identity_guard.assert_unchanged("disable success publication")
    print("Native routing disabled. Start a new Codex task to clear the loaded policy.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        _validate_args(args)
        if args.prepare_qwen:
            return prepare_qwen_credential(
                args.codex_home,
                args.qwen_region,
                apply=args.apply,
            )
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

        with contextlib.ExitStack() as stack:
            app = stack.enter_context(AppServer(target, args.codex_home))
            stack.enter_context(_transaction_directory_lock(app.codex_home))
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
            state, state_digest = _read_state_snapshot(state_path)
            _validate_state_config(state, app.config_path)
            if args.disable and state is not None:
                if state["schema"] >= 7:
                    identity_selector = "disable-schema7"
                    saved_plugin_id = state["plugin_id"]
                else:
                    identity_selector = "disable-legacy"
                    saved_plugin_id = None
            elif args.repair:
                identity_selector = "repair" if state is not None else "setup"
                saved_plugin_id = _state_plugin_id(state) if state is not None else None
            else:
                identity_selector = "setup"
                saved_plugin_id = None
            identity_guard = stack.enter_context(
                plugin_identity.guard_plugin_identity(
                    target,
                    EXECUTING_PLUGIN_ROOT,
                    identity_selector,
                    saved_plugin_id=saved_plugin_id,
                    codex_home=app.codex_home,
                )
            )
            plugin_id = identity_guard.selected_plugin_id
            if args.disable:
                return _disable(
                    app,
                    config,
                    version,
                    state,
                    state_digest,
                    workspace,
                    identity_guard,
                    plugin_id,
                    args.apply,
                )
            if args.repair:
                return _repair(
                    app,
                    config,
                    version,
                    state,
                    state_digest,
                    workspace,
                    identity_guard,
                    plugin_id,
                    args.apply,
                )

            catalog: dict[str, dict[str, Any]] = {}
            if (
                args.executor_model
                or args.planner_model
                or args.advisor_model
                or args.designer_model
            ):
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
            designer: dict[str, Any] | None = None
            fable_auth: dict[str, str] | None = None
            fable_server = (
                select_fable_server()
                if args.planner_fable or args.advisor_fable
                else None
            )
            kimi_server = select_kimi_server() if args.designer_kimi else None
            qwen_server = select_qwen_server() if args.advisor_qwen else None
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
            elif args.advisor_qwen:
                advisor = {
                    "kind": "qwen_cli",
                    "model": QWEN_MODEL,
                    "effort": "native",
                    "region": args.qwen_region,
                    "server": qwen_server,
                }

            if args.designer_model:
                designer_effort = resolve_model_effort(
                    "Designer",
                    args.designer_model,
                    args.designer_effort,
                    catalog,
                    args.confirm_unlisted_models,
                )
                designer = {
                    "kind": "model",
                    "model": args.designer_model,
                    "effort": designer_effort,
                }
            elif args.designer_kimi:
                designer = {
                    "kind": "kimi_cli",
                    "model": KIMI_MODEL,
                    "effort": "max",
                    "server": kimi_server,
                }
            validate_planning_routes(planner, advisor)
            fable_efforts = {
                route["effort"]
                for route in (planner, advisor)
                if isinstance(route, dict) and route.get("kind") == "fable"
            }
            for effort in sorted(fable_efforts):
                fable_auth = verify_fable_prerequisites(effort)
            qwen_ready = (
                verify_qwen_prerequisites(args.qwen_region)
                if args.advisor_qwen
                else None
            )
            kimi_ready = verify_kimi_prerequisites() if args.designer_kimi else None

            verified_agents = verify_agent_routes(
                app.codex_home,
                workspace,
                executor,
                planner,
                advisor,
            )
            mode, usage = build_policy(executor, planner, advisor, designer)
            new_state, edits, rollback = _prepare_setup_state(
                config,
                state,
                plugin_id,
                mode,
                usage,
                executor,
                planner,
                advisor,
                designer,
                app.config_path,
                args.replace_existing_policy,
            )
            print(f"Config: {app.config_path}")
            print("Orchestrator: model selected when each Codex task starts")
            print(f"Executor: {_route_summary(executor)}")
            print(f"Planner: {_route_summary(planner) if planner else 'root'}")
            print(f"Advisor: {_route_summary(advisor) if advisor else 'none'}")
            print(f"Designer: {_route_summary(designer) if designer else 'none'}")
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
            if qwen_ready is not None:
                print(
                    "Qwen Advisor: ready — OS credential store and sealed "
                    f"{qwen_ready['protocol']} {args.qwen_region} route; "
                    "setup makes no model call"
                )
            if kimi_ready is not None:
                print(
                    "Kimi K3 Designer: ready — existing Kimi Code OAuth subscription "
                    f"via ACP; Kimi {kimi_ready['kimi_version']}, "
                    f"acpx {kimi_ready['acpx_version']}; setup makes no model call"
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
                identity_guard.assert_unchanged("setup dry-run publication")
                print("Dry run only. Re-run with --apply after reviewing this preview.")
                return 0

            result = _batch_write(
                app,
                edits,
                version,
                identity_guard=identity_guard,
                identity_phase="setup write",
                reload_user_config=True,
            )
            if result.get("status") == "okOverridden":
                try:
                    rollback_result = _batch_write(
                        app,
                        rollback,
                        result.get("version"),
                        identity_guard=identity_guard,
                        identity_phase="setup override rollback",
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
                published_state_digest = _write_state(
                    state_path,
                    new_state,
                    identity_guard,
                    state_digest,
                )
            except StateTransactionIndeterminateError:
                # The canonical state may already reflect the new config. Retain the
                # accepted config rather than making an unsafe rollback guess.
                raise
            except BaseException as state_exc:
                try:
                    rollback_result = _batch_write(
                        app,
                        rollback,
                        result.get("version"),
                        identity_guard=identity_guard,
                        identity_phase="setup state-failure rollback",
                        reload_user_config=True,
                    )
                    if rollback_result.get("status") not in {"ok", "okOverridden"}:
                        raise ConfigurationError(
                            "unexpected rollback status "
                            f"{rollback_result.get('status')!r}"
                        )
                except BaseException as rollback_exc:
                    try:
                        rollback_detail = str(rollback_exc)
                    except BaseException:
                        rollback_detail = type(rollback_exc).__name__
                    raise ConfigurationError(
                        "Config was written but state persistence and automatic rollback "
                        "both failed; the user config may still contain managed fields. "
                        "Run status before continuing. Rollback error: "
                        f"{rollback_detail}"
                    ) from rollback_exc
                if not isinstance(state_exc, Exception):
                    raise
                raise ConfigurationError(
                    f"Could not persist restore state; config write was rolled back: {state_exc}"
                ) from state_exc

            verify_result = app.request(
                "config/read",
                {"includeLayers": True, "cwd": str(workspace)},
            )
            verify_config, verify_version = _user_layer(verify_result)
            verify_current = _current_values(verify_config, plugin_id)
            effective_config = verify_result.get("config")
            effective_current = _current_values(
                effective_config if isinstance(effective_config, dict) else {},
                plugin_id,
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
                        identity_guard=identity_guard,
                        identity_phase="setup readback rollback",
                        reload_user_config=True,
                    )
                    if rollback_result.get("status") not in {"ok", "okOverridden"}:
                        raise ConfigurationError(
                            "unexpected rollback status "
                            f"{rollback_result.get('status')!r}"
                        )
                except BaseException as rollback_exc:
                    try:
                        rollback_detail = str(rollback_exc)
                    except BaseException:
                        rollback_detail = type(rollback_exc).__name__
                    raise ConfigurationError(
                        "Codex accepted the write but effective readback did not match, "
                        "and automatic rollback failed before config rollback "
                        "completed. The newly published restore state was retained; "
                        "config and state may be inconsistent. Run status before "
                        f"continuing. Rollback error: {rollback_detail}"
                    ) from rollback_exc
                try:
                    if state is None:
                        _remove_state(
                            state_path,
                            identity_guard,
                            published_state_digest,
                        )
                    else:
                        _write_state(
                            state_path,
                            state,
                            identity_guard,
                            published_state_digest,
                        )
                except BaseException as state_rollback_exc:
                    try:
                        _, observed_state_digest = _read_state_snapshot(state_path)
                    except BaseException as state_read_exc:
                        raise ConfigurationError(
                            "Config rollback completed after effective readback failed, "
                            "but restore-state rollback and canonical-state readback "
                            "failed. Config and state may be inconsistent. Run status "
                            "before continuing."
                        ) from state_read_exc

                    prior_state_digest = None if state is None else state_digest
                    if observed_state_digest == prior_state_digest:
                        if not isinstance(state_rollback_exc, Exception):
                            raise
                        raise ConfigurationError(
                            "Codex accepted the user-layer write, but effective "
                            "readback did not match. The prior config and exact prior "
                            "restore state were reinstated despite a rollback error."
                        ) from state_rollback_exc

                    if observed_state_digest == published_state_digest:
                        try:
                            compensation_result = _batch_write(
                                app,
                                edits,
                                rollback_result.get("version"),
                                identity_guard=identity_guard,
                                identity_phase="setup readback compensation",
                                reload_user_config=True,
                            )
                            if compensation_result.get("status") not in {
                                "ok",
                                "okOverridden",
                            }:
                                raise ConfigurationError(
                                    "unexpected compensation status "
                                    f"{compensation_result.get('status')!r}"
                                )
                            compensation_read = app.request(
                                "config/read",
                                {"includeLayers": True, "cwd": str(workspace)},
                            )
                            compensation_config, _ = _user_layer(compensation_read)
                            compensation_current = _current_values(
                                compensation_config, plugin_id
                            )
                            if not _managed_matches(new_state, compensation_current):
                                raise ConfigurationError(
                                    "forward-compensated user config did not match the "
                                    "newly published restore state"
                                )
                        except BaseException as compensation_exc:
                            raise ConfigurationError(
                                "Config rollback completed after effective readback "
                                "failed, but restore-state rollback did not commit and "
                                "forward config compensation failed. Config and state "
                                "may be inconsistent. Run status before continuing."
                            ) from compensation_exc
                        if not isinstance(state_rollback_exc, Exception):
                            raise
                        raise ConfigurationError(
                            "Codex accepted the write but effective readback did not "
                            "match. The newly published config and restore state were "
                            "re-paired after state rollback failed before commit."
                        ) from state_rollback_exc

                    raise ConfigurationError(
                        "Config rollback completed after effective readback failed, "
                        "but canonical restore state matched neither the exact prior "
                        "state nor the newly published state. Config and state may be "
                        "inconsistent. Run status before continuing."
                    ) from state_rollback_exc
                raise ConfigurationError(
                    "Codex accepted the user-layer write, but current-workspace "
                    "effective readback did not match; the prior config and restore "
                    "state were reinstated."
                )
            identity_guard.assert_unchanged("setup success publication")
            print(
                "Native routing policy installed. Start a new Codex task, select a "
                "v2 model such as current Sol or Terra as orchestrator, and use "
                "Codex normally."
            )
            return 0
    except (
        ConfigurationError,
        plugin_identity.PluginIdentityError,
        OSError,
        KeyError,
        TypeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
