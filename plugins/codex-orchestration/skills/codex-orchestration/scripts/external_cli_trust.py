#!/usr/bin/env python3
"""Pin and re-attest subscription CLI executables without storing auth data."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Any


VERSION_TIMEOUT_SECONDS = 10
MAX_VERSION_CHARS = 512
SENSITIVE_ENV_EXACT_NAMES = frozenset(
    {"API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSPHRASE"}
)
SENSITIVE_ENV_SUFFIXES = (
    "_API_KEY",
    "_AUTH_TOKEN",
    "_ACCESS_TOKEN",
    "_ACCESS_KEY",
    "_TOKEN",
    "_SECRET",
    "_CLIENT_SECRET",
    "_PRIVATE_KEY",
    "_PASSWORD",
    "_PASSPHRASE",
    "_CREDENTIAL",
    "_CREDENTIALS",
)


class CliTrustError(RuntimeError):
    """A CLI is unsafe, changed, or failed attestation."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise CliTrustError(detail)


def _safe_target(path: Path) -> tuple[Path, os.stat_result]:
    try:
        target = path.expanduser().resolve(strict=True)
        info = target.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise CliTrustError("CLI executable could not be resolved.") from exc
    _require(target.is_absolute(), "CLI executable must be absolute")
    _require(not target.is_symlink() and stat.S_ISREG(info.st_mode), "CLI target is unsafe")
    _require(info.st_nlink == 1, "hard-linked CLI targets are not trusted")
    if os.name == "nt":
        _require(
            target.suffix.lower() in {".exe", ".com"},
            "Windows CLI target must be a native executable",
        )
    _require(os.access(target, os.X_OK), "CLI target is not executable")
    return target, info


def fingerprint(path: Path) -> tuple[Path, str]:
    """Hash a regular single-link target and detect changes during the read."""

    target, before = _safe_target(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise CliTrustError("CLI target could not be opened safely.") from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        _require(
            (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
            "CLI target changed before hashing",
        )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _require(
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns),
        "CLI target changed while hashing",
    )
    _require(after.st_nlink == 1, "CLI target became hard linked while hashing")
    return target, digest.hexdigest()


def sanitized_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in SENSITIVE_ENV_EXACT_NAMES
        and not key.upper().endswith(SENSITIVE_ENV_SUFFIXES)
    }


def version(path: Path, arguments: tuple[str, ...] = ("--version",)) -> str:
    try:
        result = subprocess.run(
            [str(path), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=VERSION_TIMEOUT_SECONDS,
            shell=False,
            env=sanitized_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CliTrustError("CLI version attestation could not run.") from exc
    if result.returncode != 0:
        raise CliTrustError(
            f"CLI version attestation exited with {result.returncode}; output withheld."
        )
    value = (result.stdout.strip() or result.stderr.strip()).splitlines()
    _require(bool(value), "CLI version attestation returned no version")
    checked = value[0].strip()
    _require(0 < len(checked) <= MAX_VERSION_CHARS, "CLI version string is invalid")
    return checked


def attest(path: Path, *, publisher: str) -> dict[str, Any]:
    _require(bool(publisher.strip()), "CLI publisher must be explicit")
    target, digest = fingerprint(path)
    observed_version = version(target)
    target_after, digest_after = fingerprint(target)
    _require(target_after == target and digest_after == digest, "CLI changed during attestation")
    return {
        "path": str(target),
        "strategy": "sha256",
        "fingerprint": f"sha256:{digest}",
        "publisher": publisher,
        "version": observed_version,
    }


def verify(record: dict[str, Any]) -> Path:
    expected_keys = {"path", "strategy", "fingerprint", "publisher", "version"}
    _require(type(record) is dict and set(record) == expected_keys, "CLI trust record is invalid")
    _require(record["strategy"] == "sha256", "CLI trust strategy is unsupported")
    target, digest = fingerprint(Path(record["path"]))
    _require(str(target) == record["path"], "CLI path changed; re-trust is required")
    _require(f"sha256:{digest}" == record["fingerprint"], "CLI_CHANGED: re-trust is required")
    observed_version = version(target)
    _require(observed_version == record["version"], "CLI_CHANGED: version drift requires re-trust")
    target_after, digest_after = fingerprint(target)
    _require(digest_after == digest, "CLI_CHANGED: executable changed during verification")
    return target_after
