#!/usr/bin/env python3
"""Install and describe the nonsecret command-backed auth boundary."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Any

import external_cli_trust


HELPER_NAME = "external_auth_helper.py"
HELPER_MARKER = b"codex-orchestration-managed-external-auth-helper-v1"
PROVIDER_AUTH_TIMEOUT_MS = 5_000


class CredentialSetupError(RuntimeError):
    """The stable helper cannot be installed without unsafe replacement."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise CredentialSetupError(detail)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_regular(path: Path, *, managed: bool) -> bytes | None:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    _require(not path.is_symlink() and stat.S_ISREG(info.st_mode), f"unsafe helper path: {path}")
    _require(info.st_nlink == 1, f"hard-linked helper path is unsafe: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise CredentialSetupError("could not inspect the existing auth helper") from exc
    if managed:
        _require(HELPER_MARKER in content, "refusing to replace an unmanaged auth helper")
    return content


def _prepare_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        info = path.stat(follow_symlinks=False)
        _require(not path.is_symlink() and stat.S_ISDIR(info.st_mode), f"unsafe directory: {path}")
        return
    path.mkdir(mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)


def install_stable_helper(codex_home: Path) -> tuple[Path, str]:
    """Copy the packaged helper under CODEX_HOME with a stable absolute path."""

    source = Path(__file__).resolve().with_name(HELPER_NAME)
    content = _safe_regular(source, managed=False)
    _require(content is not None and HELPER_MARKER in content, "packaged auth helper is invalid")
    home = codex_home.expanduser().absolute()
    _require(not home.is_symlink(), "Codex home must not be symlinked")
    _require(home.is_dir(), "Codex home must already exist")
    managed_root = home / "codex-orchestration"
    binary_root = managed_root / "bin"
    _prepare_directory(managed_root)
    _prepare_directory(binary_root)
    target = binary_root / HELPER_NAME
    existing = _safe_regular(target, managed=True)
    digest = hashlib.sha256(content).hexdigest()
    if existing == content:
        if os.name == "posix":
            target.chmod(0o700)
        return target, digest
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(12)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_regular(temporary, managed=True)
        os.replace(temporary, target)
        if os.name == "posix":
            target.chmod(0o700)
        _fsync_directory(binary_root)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    _require(_safe_regular(target, managed=True) == content, "auth helper verification failed")
    return target, digest


def verify_stable_helper(codex_home: Path) -> tuple[Path, str]:
    """Verify the installed helper exactly matches the packaged helper without writing."""

    source = Path(__file__).resolve().with_name(HELPER_NAME)
    expected = _safe_regular(source, managed=False)
    _require(
        expected is not None and HELPER_MARKER in expected,
        "packaged auth helper is invalid",
    )
    target = (
        codex_home.expanduser().absolute()
        / "codex-orchestration"
        / "bin"
        / HELPER_NAME
    )
    actual = _safe_regular(target, managed=True)
    _require(actual is not None, "managed auth helper is missing")
    _require(actual == expected, "managed auth helper drifted")
    if os.name == "posix":
        info = target.stat(follow_symlinks=False)
        _require(bool(info.st_mode & stat.S_IXUSR), "managed auth helper is not executable")
    return target, hashlib.sha256(actual).hexdigest()


def _helper_command(
    helper: Path,
    action: str,
    provider_id: str,
    *,
    platform: str | None = None,
    python_executable: Path | None = None,
) -> list[str]:
    _require(helper.is_absolute(), "auth helper path must be absolute")
    selected_platform = sys.platform if platform is None else platform
    arguments = [str(helper), action, "--provider", provider_id]
    if selected_platform == "win32":
        interpreter = Path(python_executable or sys.executable).expanduser().resolve()
        _require(interpreter.is_absolute(), "Python interpreter path must be absolute")
        return [str(interpreter), *arguments]
    return arguments


def auth_config(
    helper: Path,
    provider_id: str,
    *,
    platform: str | None = None,
    python_executable: Path | None = None,
) -> dict[str, Any]:
    """Return only nonsecret fields accepted by Codex command-backed auth."""

    command = _helper_command(
        helper,
        "get",
        provider_id,
        platform=platform,
        python_executable=python_executable,
    )
    return {
        "command": command[0],
        "args": command[1:],
        "timeout_ms": PROVIDER_AUTH_TIMEOUT_MS,
        "refresh_interval_ms": 0,
    }


def enrollment_command(
    helper: Path,
    provider_id: str,
    *,
    platform: str | None = None,
    python_executable: Path | None = None,
) -> list[str]:
    """Return a command the user must run in a trusted terminal, never in chat."""

    return _helper_command(
        helper,
        "enroll",
        provider_id,
        platform=platform,
        python_executable=python_executable,
    )


def credential_ready(
    helper: Path,
    provider_id: str,
    *,
    platform: str | None = None,
    python_executable: Path | None = None,
) -> bool:
    """Check presence through a secret-discarding helper operation."""

    try:
        completed = subprocess.run(
            _helper_command(
                helper,
                "status",
                provider_id,
                platform=platform,
                python_executable=python_executable,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=20,
            shell=False,
            env=external_cli_trust.sanitized_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "configured"
