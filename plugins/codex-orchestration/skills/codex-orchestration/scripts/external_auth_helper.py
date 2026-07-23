#!/usr/bin/env python3
"""Stable OS-credential-store reader for Codex command-backed provider auth.

This helper is copied under CODEX_HOME before a provider is configured. It is
self-contained so an installed provider never points into a versioned plugin
cache. Secrets are accepted only by a hidden local prompt, stored in the
operating-system credential store, and printed only for the ``get`` operation
invoked by Codex itself.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import getpass
from pathlib import Path
import re
import shutil
import subprocess
import sys


MANAGED_HELPER_MARKER = "codex-orchestration-managed-external-auth-helper-v1"
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
SERVICE_PREFIX = "com.cjbuilds.codex-orchestration.external"
TIMEOUT_SECONDS = 20
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class HelperError(RuntimeError):
    """A credential operation failed without disclosing provider output."""


def _provider(value: str) -> str:
    if PROVIDER_RE.fullmatch(value) is None:
        raise HelperError("Provider ID is invalid.")
    return value


def _service(provider: str) -> str:
    return f"{SERVICE_PREFIX}.{provider}"


def _run_capture(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("The OS credential store could not be queried.") from exc
    if completed.returncode != 0:
        raise HelperError("No usable credential is configured for this provider.")
    value = completed.stdout.strip()
    if not value:
        raise HelperError("The OS credential store returned an empty credential.")
    return value


def _run_interactive(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            stdin=None,
            stdout=None,
            stderr=None,
            text=False,
            check=False,
            timeout=None,
            shell=False,
        )
    except OSError as exc:
        raise HelperError("The OS credential store could not be opened.") from exc
    if completed.returncode != 0:
        raise HelperError("The credential-store operation did not complete.")


def _darwin(action: str, provider: str) -> str | None:
    security = Path("/usr/bin/security")
    if not security.is_file():
        raise HelperError("macOS Keychain command is unavailable.")
    common = ["-a", provider, "-s", _service(provider)]
    if action in {"get", "status"}:
        return _run_capture([str(security), "find-generic-password", *common, "-w"])
    if action == "enroll":
        # Apple documents that a final -w with no argument prompts securely.
        _run_interactive(
            [str(security), "add-generic-password", "-U", *common, "-w"]
        )
        return None
    if action == "delete":
        _run_interactive([str(security), "delete-generic-password", *common])
        return None
    raise HelperError("Credential action is unsupported.")


def _linux(action: str, provider: str) -> str | None:
    executable = shutil.which("secret-tool")
    if executable is None:
        raise HelperError(
            "Secret Service is unavailable; configure a separately trusted user helper."
        )
    attributes = ["service", _service(provider), "account", provider]
    if action in {"get", "status"}:
        return _run_capture([executable, "lookup", *attributes])
    if action == "enroll":
        _run_interactive(
            [
                executable,
                "store",
                f"--label=Codex Orchestration: {provider}",
                *attributes,
            ]
        )
        return None
    if action == "delete":
        _run_interactive([executable, "clear", *attributes])
        return None
    raise HelperError("Credential action is unsupported.")


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _windows_get(provider: str) -> str:
    pointer = ctypes.POINTER(_CredentialW)()
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CredentialW)),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    if not advapi.CredReadW(_service(provider), CRED_TYPE_GENERIC, 0, pointer):
        raise HelperError("No usable credential is configured for this provider.")
    try:
        credential = pointer.contents
        size = int(credential.CredentialBlobSize)
        if size <= 0 or not credential.CredentialBlob:
            raise HelperError("The OS credential store returned an empty credential.")
        raw = ctypes.string_at(credential.CredentialBlob, size)
        value = raw.decode("utf-16-le").rstrip("\x00")
        if not value:
            raise HelperError("The OS credential store returned an empty credential.")
        return value
    finally:
        advapi.CredFree(pointer)


def _windows_enroll(provider: str) -> None:
    value = getpass.getpass("Provider API key (input is hidden): ")
    if not value:
        raise HelperError("An empty credential was not stored.")
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CredentialW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = _service(provider)
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = provider
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    try:
        if not advapi.CredWriteW(ctypes.byref(credential), 0):
            raise HelperError("Windows Credential Manager rejected the credential.")
    finally:
        ctypes.memset(blob, 0, len(raw))
        value = ""


def _windows_delete(provider: str) -> None:
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi.CredDeleteW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi.CredDeleteW.restype = wintypes.BOOL
    if not advapi.CredDeleteW(_service(provider), CRED_TYPE_GENERIC, 0):
        raise HelperError("Windows Credential Manager could not delete the credential.")


def _windows(action: str, provider: str) -> str | None:
    if action in {"get", "status"}:
        return _windows_get(provider)
    if action == "enroll":
        _windows_enroll(provider)
        return None
    if action == "delete":
        _windows_delete(provider)
        return None
    raise HelperError("Credential action is unsupported.")


def dispatch(action: str, provider: str, *, platform: str | None = None) -> str | None:
    selected = platform or sys.platform
    checked = _provider(provider)
    if selected == "darwin":
        return _darwin(action, checked)
    if selected.startswith("linux"):
        return _linux(action, checked)
    if selected == "win32":
        return _windows(action, checked)
    raise HelperError("This platform requires a separately trusted user helper.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Use the OS credential store for one external model provider."
    )
    parser.add_argument("action", choices=("get", "status", "enroll", "delete"))
    parser.add_argument("--provider", required=True)
    args = parser.parse_args(argv)
    try:
        value = dispatch(args.action, args.provider)
    except HelperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.action == "get":
        assert value is not None
        print(value)
    elif args.action == "status":
        print("configured")
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
