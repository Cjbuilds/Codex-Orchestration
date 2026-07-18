#!/usr/bin/env python3
"""Transactionally update Codex-Orchestration from its canonical marketplace."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, NamedTuple
from urllib.parse import urlsplit


PLUGIN_NAME = "codex-orchestration"
MARKETPLACE_NAME = "codex-orchestration"
PLUGIN_ID = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
REPOSITORY_URL = "https://github.com/Cjbuilds/Codex-Orchestration"
COMMAND_TIMEOUT_SECONDS = 120
MAX_COMMAND_OUTPUT = 1_000_000
MAX_MANIFEST_BYTES = 128_000


class UpdateError(RuntimeError):
    """The source, candidate, native operation, or rollback failed validation."""


class SemVer(NamedTuple):
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: object) -> "SemVer":
        if type(value) is not str:
            raise UpdateError("plugin version is not a string")
        match = re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            value,
        )
        if match is None:
            raise UpdateError(f"invalid plugin version: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
            raise UpdateError(f"invalid plugin version: {value!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def compare(self, other: "SemVer") -> int:
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return -1 if left < right else 1
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left_part, right_part in zip(self.prerelease, other.prerelease):
            if left_part == right_part:
                continue
            left_numeric = left_part.isdigit()
            right_numeric = right_part.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left_part) < int(right_part) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left_part < right_part else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1


class TrustPolicy(NamedTuple):
    repository_url: str = REPOSITORY_URL
    require_canonical_url: bool = True


class Candidate(NamedTuple):
    version: str
    commit: str


Command = tuple[Path, ...]
Runner = Callable[[Command, list[str], dict[str, str]], object]
Stager = Callable[[Path, Path, Path, dict[str, str], TrustPolicy], Candidate]


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise UpdateError(f"JSON contains duplicate key {key!r}")
        value[key] = item
    return value


def _load_json(value: str, *, label: str) -> object:
    try:
        return json.loads(value, object_pairs_hook=_no_duplicate_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise UpdateError(f"{label} did not return strict JSON") from exc


def _trusted_search_path(extra: tuple[Path, ...] = ()) -> str:
    candidates = [
        *extra,
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    ]
    if os.name == "nt":
        root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates = [root / "System32", root, *extra]
    unique: list[str] = []
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_dir():
            resolved = str(candidate.resolve())
            if resolved not in unique:
                unique.append(resolved)
    return os.pathsep.join(unique)


def resolve_helper(name: str, *, extra: tuple[Path, ...] = ()) -> Path:
    found = shutil.which(name, path=_trusted_search_path(extra))
    if not found:
        raise UpdateError(f"required trusted helper is unavailable: {name}")
    value = Path(found).resolve()
    if not value.is_file() or not os.access(value, os.X_OK):
        raise UpdateError(f"trusted helper is not executable: {value}")
    return value


def resolve_binary(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.parent == Path(".") and os.sep not in value:
        return resolve_helper(value)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise UpdateError(f"Codex binary is not executable: {resolved}")
    return resolved


def resolve_command(binary: Path) -> Command:
    try:
        with binary.open("rb") as handle:
            first_line = handle.readline(512)
    except OSError as exc:
        raise UpdateError("could not inspect the Codex executable") from exc
    if not first_line.startswith(b"#!"):
        return (binary,)
    try:
        parts = shlex.split(first_line[2:].decode("utf-8").strip())
    except (UnicodeError, ValueError) as exc:
        raise UpdateError("Codex executable has an invalid shebang") from exc
    if not parts:
        raise UpdateError("Codex executable has an empty shebang")
    interpreter = Path(parts[0])
    arguments = parts[1:]
    if interpreter == Path("/usr/bin/env"):
        if len(arguments) != 1 or arguments[0].startswith("-"):
            raise UpdateError("Codex executable uses an unsupported env shebang")
        interpreter = resolve_helper(arguments[0], extra=(binary.parent,))
        arguments = []
    else:
        if not interpreter.is_absolute() or len(arguments) > 2:
            raise UpdateError("Codex executable uses an unsupported shebang")
        interpreter = interpreter.resolve(strict=True)
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise UpdateError("Codex shebang interpreter is not executable")
    return (interpreter, *(Path(argument) for argument in arguments), binary)


def resolve_codex_home(value: Path | None) -> Path:
    selected = value or Path(os.environ.get("CODEX_HOME", "~/.codex"))
    expanded = selected.expanduser()
    if not expanded.is_dir():
        raise UpdateError(f"CODEX_HOME does not exist: {expanded}")
    return expanded.resolve()


def _safe_environment(
    codex_home: Path,
    command: Command,
    git: Path,
    isolated_home: Path,
) -> dict[str, str]:
    """Return a deterministic allowlist with no host credentials or transport overrides."""

    environment = {
        "CODEX_HOME": str(codex_home),
        "HOME": str(isolated_home),
        "XDG_CONFIG_HOME": str(isolated_home / ".config"),
        "PATH": _trusted_search_path(tuple(item.parent for item in (*command, git))),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "protocol.file.allow",
        "GIT_CONFIG_VALUE_0": "never",
        "GIT_CONFIG_KEY_1": "protocol.ext.allow",
        "GIT_CONFIG_VALUE_1": "never",
        "GIT_CONFIG_KEY_2": "core.hooksPath",
        "GIT_CONFIG_VALUE_2": os.devnull,
        "GIT_CONFIG_KEY_3": "credential.helper",
        "GIT_CONFIG_VALUE_3": "",
    }
    if os.name == "nt":
        root = str(Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve())
        environment.update({"SystemRoot": root, "WINDIR": root, "PATHEXT": ".COM;.EXE;.BAT;.CMD"})
    return environment


@contextmanager
def _update_lock(codex_home: Path) -> Any:
    """Hold one OS-backed nonblocking lock for the complete update transaction."""

    lock_path = codex_home / ".tmp" / ".codex-orchestration-update.lock"
    _validate_directory_path(lock_path.parent, lock_path.parent, label="update lock directory")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise UpdateError("could not open the update lock safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UpdateError("update lock has unsafe metadata")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows
            os.chmod(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UpdateError("another plugin update is already running") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateError("another plugin update is already running") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        if taskkill.is_file():
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _execute(
    argv: list[str],
    *,
    environment: dict[str, str],
    cwd: Path,
    label: str,
) -> tuple[int, str, str]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise UpdateError(f"could not start {label}") from exc
    assert process.stdout is not None and process.stderr is not None
    buffers = [bytearray(), bytearray()]
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(stream: Any, target: bytearray) -> None:
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                return
            with lock:
                used = len(buffers[0]) + len(buffers[1])
                remaining = max(0, MAX_COMMAND_OUTPUT - used)
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    return

    threads = [
        threading.Thread(target=drain, args=(process.stdout, buffers[0]), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, buffers[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            timed_out = not overflow.is_set()
            _terminate_process_tree(process)
            break
        time.sleep(0.02)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        process.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
    process.stdout.close()
    process.stderr.close()
    if any(thread.is_alive() for thread in threads):
        raise UpdateError(f"{label} output readers did not stop")
    if overflow.is_set():
        raise UpdateError(f"{label} returned excessive output")
    if timed_out:
        raise UpdateError(f"{label} timed out; its process tree was terminated")
    try:
        stdout = bytes(buffers[0]).decode("utf-8")
        stderr = bytes(buffers[1]).decode("utf-8")
    except UnicodeError as exc:
        raise UpdateError(f"{label} returned non-UTF-8 output") from exc
    return process.returncode, stdout, stderr


def _run_json(command: Command, arguments: list[str], environment: dict[str, str]) -> object:
    codex_home = Path(environment["CODEX_HOME"])
    returncode, stdout, _stderr = _execute(
        [*(str(item) for item in command), *arguments],
        environment=environment,
        cwd=codex_home,
        label="Codex plugin command",
    )
    if returncode != 0:
        action = " ".join(arguments[:4])
        raise UpdateError(
            f"Codex {action} failed with exit {returncode}; command output was withheld"
        )
    return _load_json(stdout, label="Codex plugin command")


def _run_git(git: Path, arguments: list[str], environment: dict[str, str], cwd: Path) -> str:
    returncode, stdout, _stderr = _execute(
        [str(git), *arguments], environment=environment, cwd=cwd, label="Git command"
    )
    if returncode != 0:
        raise UpdateError("trusted Git command failed; output was withheld")
    return stdout.strip()


def _is_canonical_repository(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    path = parsed.path.rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    return path.lower() == "/cjbuilds/codex-orchestration"


def _repository_matches(value: object, policy: TrustPolicy) -> bool:
    if policy.require_canonical_url:
        return _is_canonical_repository(value)
    return type(value) is str and value.rstrip("/") == policy.repository_url.rstrip("/")


def _plugin_entry(payload: object) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != {"installed", "available"}:
        raise UpdateError("Codex plugin list returned an unsupported shape")
    installed = payload.get("installed")
    if type(installed) is not list:
        raise UpdateError("Codex plugin list did not return installed plugins")
    matches = [entry for entry in installed if type(entry) is dict and entry.get("pluginId") == PLUGIN_ID]
    if len(matches) != 1:
        raise UpdateError(f"expected exactly one installed {PLUGIN_ID} entry")
    return matches[0]


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _validate_directory_path(candidate: Path, expected: Path, *, label: str) -> Path:
    if not candidate.is_absolute() or candidate != expected:
        raise UpdateError(f"{label} is outside the trusted marketplace")
    current = expected.anchor and Path(expected.anchor) or Path(".")
    for segment in expected.parts[1:] if expected.anchor else expected.parts:
        current = current / segment
        try:
            info = current.lstat()
        except OSError as exc:
            raise UpdateError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise UpdateError(f"{label} contains a symlink or reparse point")
    resolved = candidate.resolve(strict=True)
    if resolved != expected or not resolved.is_dir():
        raise UpdateError(f"{label} is unsafe")
    return resolved


def _validate_entry(
    entry: dict[str, Any], codex_home: Path, policy: TrustPolicy
) -> tuple[str, bool, Path]:
    if (
        entry.get("name") != PLUGIN_NAME
        or entry.get("marketplaceName") != MARKETPLACE_NAME
        or entry.get("installed") is not True
        or type(entry.get("enabled")) is not bool
    ):
        raise UpdateError("installed plugin identity or state is invalid")
    version = entry.get("version")
    SemVer.parse(version)
    assert isinstance(version, str)
    marketplace = entry.get("marketplaceSource")
    if (
        type(marketplace) is not dict
        or marketplace.get("sourceType") != "git"
        or not _repository_matches(marketplace.get("source"), policy)
    ):
        raise UpdateError("the plugin is not installed from the trusted Git marketplace")
    source = entry.get("source")
    source_path = source.get("path") if type(source) is dict else None
    if type(source_path) is not str or not source_path:
        raise UpdateError("installed plugin source path is unavailable")
    expected = codex_home / ".tmp" / "marketplaces" / MARKETPLACE_NAME / "plugins" / PLUGIN_NAME
    resolved = _validate_directory_path(Path(source_path).expanduser(), expected, label="installed plugin source path")
    return version, entry["enabled"], resolved


def _read_manifest(plugin_root: Path) -> str:
    parent = plugin_root / ".codex-plugin"
    _validate_directory_path(parent, parent, label="candidate manifest directory")
    manifest = parent / "plugin.json"
    before_parent = parent.lstat()
    try:
        before = manifest.lstat()
    except OSError as exc:
        raise UpdateError("candidate plugin manifest is missing") from exc
    if stat.S_ISLNK(before.st_mode) or _is_reparse(before):
        raise UpdateError("candidate plugin manifest is symlinked")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(manifest, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            data = handle.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise UpdateError("candidate plugin manifest cannot be read safely") from exc
    after_parent = parent.lstat()
    def identity(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or len(data) > MAX_MANIFEST_BYTES
        or identity(before) != identity(opened)
        or identity(opened) != identity(after)
        or identity(before_parent) != identity(after_parent)
    ):
        raise UpdateError("candidate plugin manifest changed or has unsafe metadata")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise UpdateError("candidate plugin manifest is not UTF-8") from exc


def _candidate_version(plugin_root: Path, _policy: TrustPolicy) -> str:
    payload = _load_json(_read_manifest(plugin_root), label="plugin manifest")
    if type(payload) is not dict:
        raise UpdateError("candidate plugin manifest is not an object")
    if payload.get("name") != PLUGIN_NAME or not _is_canonical_repository(payload.get("repository")):
        raise UpdateError("candidate plugin manifest identity is invalid")
    version = payload.get("version")
    SemVer.parse(version)
    assert isinstance(version, str)
    return version


def _stage_candidate(
    git: Path,
    marketplace_root: Path,
    codex_home: Path,
    environment: dict[str, str],
    policy: TrustPolicy,
) -> Candidate:
    branch = _run_git(git, ["symbolic-ref", "--quiet", "--short", "HEAD"], environment, marketplace_root)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch) or ".." in branch:
        raise UpdateError("trusted marketplace branch is unsupported")
    remote = _run_git(git, ["config", "--get", "remote.origin.url"], environment, marketplace_root)
    if not _repository_matches(remote, policy):
        raise UpdateError("marketplace Git remote is not trusted")
    staging_parent = codex_home / ".tmp"
    with tempfile.TemporaryDirectory(prefix="codex-orchestration-stage-", dir=staging_parent) as raw:
        staging = Path(raw) / "repository"
        clone_arguments = ["clone", "--quiet", "--no-tags"]
        if policy.require_canonical_url:
            clone_arguments.append("--depth=1")
        clone_arguments.extend(
            ["--single-branch", "--branch", branch, policy.repository_url, str(staging)]
        )
        _run_git(
            git,
            clone_arguments,
            environment,
            staging_parent,
        )
        commit = _run_git(git, ["rev-parse", "HEAD^{commit}"], environment, staging)
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise UpdateError("staged candidate commit is invalid")
        version = _candidate_version(staging / "plugins" / PLUGIN_NAME, policy)
        return Candidate(version, commit)


def _validate_upgrade(payload: object, marketplace_root: Path) -> None:
    if type(payload) is not dict:
        raise UpdateError("marketplace upgrade returned an invalid result")
    if payload.get("selectedMarketplaces") != [MARKETPLACE_NAME] or payload.get("errors") != []:
        raise UpdateError("marketplace upgrade reported an unexpected result")
    roots = payload.get("upgradedRoots")
    if type(roots) is not list or roots != [str(marketplace_root)]:
        raise UpdateError("marketplace upgrade returned an unexpected root")
    _validate_directory_path(Path(roots[0]), marketplace_root, label="marketplace upgrade root")


def _rollback(
    command: Command,
    runner: Runner,
    git: Path,
    environment: dict[str, str],
    codex_home: Path,
    policy: TrustPolicy,
    marketplace_root: Path,
    old_commit: str,
    old_version: str,
    install_attempted: bool,
) -> None:
    _run_git(git, ["reset", "--hard", old_commit], environment, marketplace_root)
    _run_git(git, ["clean", "-dffx"], environment, marketplace_root)
    if install_attempted:
        result = runner(command, ["plugin", "add", PLUGIN_ID, "--json"], environment)
        if type(result) is not dict or result.get("version") != old_version:
            raise UpdateError("native rollback did not reinstall the prior plugin")
    entry = _plugin_entry(runner(command, ["plugin", "list", "--json"], environment))
    version, enabled, _root = _validate_entry(entry, codex_home, policy)
    if version != old_version or enabled is not True:
        raise UpdateError("rollback did not restore the prior enabled plugin")


def perform_update(
    command: Command,
    codex_home: Path,
    *,
    git: Path,
    runner: Runner = _run_json,
    stager: Stager = _stage_candidate,
    environment: dict[str, str] | None = None,
    policy: TrustPolicy = TrustPolicy(),
) -> tuple[str, str, bool]:
    codex_home = codex_home.expanduser().resolve()
    with _update_lock(codex_home), tempfile.TemporaryDirectory(
        prefix="codex-orchestration-home-", dir=codex_home / ".tmp"
    ) as raw_home:
        effective_environment = dict(environment) if environment is not None else _safe_environment(
            codex_home, command, git, Path(raw_home)
        )
        effective_environment["CODEX_HOME"] = str(codex_home)
        before_entry = _plugin_entry(runner(command, ["plugin", "list", "--json"], effective_environment))
        current_version, was_enabled, plugin_root = _validate_entry(before_entry, codex_home, policy)
        if not was_enabled:
            raise UpdateError("the plugin is disabled; update was refused without making changes")
        marketplace_root = plugin_root.parents[1]
        old_commit = _run_git(git, ["rev-parse", "HEAD^{commit}"], effective_environment, marketplace_root)
        candidate = stager(git, marketplace_root, codex_home, effective_environment, policy)
        precedence = SemVer.parse(candidate.version).compare(SemVer.parse(current_version))
        if precedence < 0:
            raise UpdateError(f"candidate {candidate.version} is a downgrade from {current_version}; refused")
        if precedence == 0:
            return current_version, candidate.version, False

        mutation_started = False
        install_attempted = False
        try:
            mutation_started = True
            upgrade = runner(
                command,
                ["plugin", "marketplace", "upgrade", MARKETPLACE_NAME, "--json"],
                effective_environment,
            )
            _validate_upgrade(upgrade, marketplace_root)
            upgraded_commit = _run_git(git, ["rev-parse", "HEAD^{commit}"], effective_environment, marketplace_root)
            if upgraded_commit != candidate.commit:
                raise UpdateError("native marketplace refresh did not match the staged commit")
            if _candidate_version(plugin_root, policy) != candidate.version:
                raise UpdateError("native marketplace refresh did not match the staged manifest")
            install_attempted = True
            installed = runner(command, ["plugin", "add", PLUGIN_ID, "--json"], effective_environment)
            if type(installed) is not dict or installed.get("version") != candidate.version:
                raise UpdateError("Codex plugin install returned an unexpected version")
            after_entry = _plugin_entry(runner(command, ["plugin", "list", "--json"], effective_environment))
            after_version, after_enabled, after_root = _validate_entry(after_entry, codex_home, policy)
            if after_version != candidate.version or after_enabled is not True or after_root != plugin_root:
                raise UpdateError("post-install verification found version, enabled-state, or source drift")
        except (UpdateError, OSError) as exc:
            if mutation_started:
                try:
                    _rollback(
                        command,
                        runner,
                        git,
                        effective_environment,
                        codex_home,
                        policy,
                        marketplace_root,
                        old_commit,
                        current_version,
                        install_attempted,
                    )
                except (UpdateError, OSError) as rollback_exc:
                    raise UpdateError(f"update failed and rollback also failed: {rollback_exc}") from exc
            raise
        return current_version, candidate.version, True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely update Codex-Orchestration from its canonical Git marketplace.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-home", type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        binary = resolve_binary(args.codex_bin)
        command = resolve_command(binary)
        git = resolve_helper("git")
        codex_home = resolve_codex_home(args.codex_home)
        previous, current, changed = perform_update(command, codex_home, git=git)
        if changed:
            print(f"Codex-Orchestration updated: {previous} -> {current}")
            print("Restart Codex Desktop and start a new task. Chats, credentials, and routing were not touched.")
        else:
            print(f"Codex-Orchestration is already current at {current}.")
        return 0
    except UpdateError as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
