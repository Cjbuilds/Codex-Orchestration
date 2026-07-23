"""Fail-closed transaction identity guard for Codex Orchestration.

This module intentionally covers one plugin and one transaction lifetime.  It is
not a general-purpose filesystem integrity or sandboxing API.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Mapping, Sequence


PLUGIN_NAME = "codex-orchestration"
LEGACY_PLUGIN_ID = "codex-orchestration@codex-orchestration"
MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
_MARKETPLACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PluginIdentityError(RuntimeError):
    """Base class for sanitized, fail-closed identity failures."""


class InventoryCommandError(PluginIdentityError):
    pass


class InventoryFormatError(PluginIdentityError):
    pass


class PackageIdentityError(PluginIdentityError):
    pass


class SelectionError(PluginIdentityError):
    pass


class IdentityDriftError(PluginIdentityError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise InventoryFormatError(f"Plugin inventory has an invalid {label}.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InventoryFormatError(f"Plugin inventory has an invalid {label}.")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise InventoryFormatError(f"Plugin inventory has an invalid {label}.")
    return value


def _inventory_plugin_id(value: Any) -> tuple[str, str, str]:
    result = _text(value, "plugin ID")
    if result.count("@") != 1:
        raise InventoryFormatError("Plugin inventory has an invalid plugin ID.")
    name, marketplace = result.split("@")
    if (
        _MARKETPLACE_RE.fullmatch(name) is None
        or _MARKETPLACE_RE.fullmatch(marketplace) is None
    ):
        raise InventoryFormatError("Plugin inventory has an invalid plugin ID.")
    return result, name, marketplace


def _plugin_id(value: Any) -> str:
    result, name, _ = _inventory_plugin_id(value)
    if name != PLUGIN_NAME:
        raise InventoryFormatError("Plugin inventory has an invalid plugin ID.")
    return result


def _record_identity(
    raw: Mapping[str, Any],
) -> tuple[str, str, str, bool, bool]:
    plugin_id, identity_name, identity_marketplace = _inventory_plugin_id(
        raw.get("pluginId")
    )
    name = _text(raw.get("name"), "plugin name")
    marketplace = _text(raw.get("marketplaceName"), "marketplace name")
    if name != identity_name or marketplace != identity_marketplace:
        raise InventoryFormatError("Plugin inventory identity fields are inconsistent.")
    installed = _boolean(raw.get("installed"), "installed state")
    enabled = _boolean(raw.get("enabled"), "enabled state")
    return plugin_id, name, marketplace, installed, enabled


def _canonical_path(value: Any, label: str) -> Path:
    raw = _text(value, label)
    path = Path(raw)
    if not path.is_absolute():
        raise InventoryFormatError(f"Plugin inventory has an invalid {label}.")
    # Do not resolve the leaf before it is opened with no-follow/reparse-point
    # semantics; doing so would silently accept a linked source root.
    return Path(os.path.abspath(path))


def _cache_component(value: Any, label: str) -> str:
    component = _text(value, label)
    if (
        component in {".", ".."}
        or len(component) > 128
        or component[0] in {".", "-"}
        or any(character in component for character in ("/", "\\", ":"))
        or not all(
            character.isascii()
            and (character.isalnum() or character in {".", "_", "+", "-"})
            for character in component
        )
    ):
        raise InventoryFormatError(f"Plugin inventory has an invalid {label}.")
    return component


def _expected_cache_path(
    codex_home: str | os.PathLike[str] | None,
    record: Mapping[str, Any],
) -> Path:
    if codex_home is None:
        raise SelectionError("Effective CODEX_HOME is required for plugin cache identity.")
    home = Path(codex_home)
    if not home.is_absolute() or any(
        ord(character) < 32 or ord(character) == 127 for character in str(home)
    ):
        raise SelectionError("Effective CODEX_HOME is invalid for plugin cache identity.")
    marketplace = _cache_component(record["marketplace_name"], "marketplace cache component")
    name = _cache_component(record["name"], "plugin cache component")
    version = _cache_component(record["version"], "version cache component")
    home = Path(os.path.abspath(home))
    candidate = home / "plugins" / "cache" / marketplace / name / version
    cache_root = home / "plugins" / "cache"
    try:
        if os.path.commonpath((str(cache_root), str(candidate))) != str(cache_root):
            raise SelectionError("Plugin cache path escapes effective CODEX_HOME.")
    except ValueError as error:
        raise SelectionError("Plugin cache path escapes effective CODEX_HOME.") from error
    return candidate


def _path_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _validate_exact_cache_path(path: Path) -> None:
    if _path_key(os.path.realpath(path)) != _path_key(path):
        raise SelectionError("Executing plugin cache path is aliased or reparsed.")


if os.name == "nt":
    from ctypes import wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_SHARE_READ = 0x1
    _OPEN_EXISTING = 3
    _FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _FILE_ID_INFO_CLASS = 18
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", _FILE_ID_128)]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _win_handle_identity(handle: int) -> str:
    info = _FILE_ID_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle, _FILE_ID_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise PackageIdentityError("Windows file identity inspection failed.")
    return f"{info.VolumeSerialNumber:016x}:{bytes(info.FileId.Identifier).hex()}"


def _win_reparse(handle: int) -> bool:
    info = _FILE_ATTRIBUTE_TAG_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise PackageIdentityError("Windows file attribute inspection failed.")
    return bool(info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT)


class _Retained:
    """A path opened once; all identity reads are from its retained handle."""

    def __init__(self, path: Path, *, directory: bool) -> None:
        self.path = path
        self.directory = directory
        self.fd: int | None = None
        self.handle: int | None = None
        if os.name == "nt":
            access = _FILE_READ_ATTRIBUTES if directory else _GENERIC_READ
            flags = _FILE_FLAG_OPEN_REPARSE_POINT
            flags |= _FILE_FLAG_BACKUP_SEMANTICS if directory else _FILE_FLAG_SEQUENTIAL_SCAN
            handle = _kernel32.CreateFileW(
                str(path), access, _FILE_SHARE_READ, None, _OPEN_EXISTING, flags, None
            )
            if handle == _INVALID_HANDLE:
                raise PackageIdentityError("Plugin payload could not be opened safely.")
            self.handle = int(handle)
            try:
                if _win_reparse(self.handle):
                    raise PackageIdentityError("Plugin payload contains a reparse point.")
                self.fd = msvcrt.open_osfhandle(self.handle, os.O_RDONLY)
                self.handle = None  # descriptor now owns the native handle
            except Exception:
                self.close()
                raise
        else:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_DIRECTORY", 0) if directory else 0
            try:
                self.fd = os.open(path, flags)
                try:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except (ImportError, BlockingIOError, OSError) as error:
                    raise PackageIdentityError("Plugin payload lock acquisition failed.") from error
            except OSError as error:
                raise PackageIdentityError("Plugin payload could not be opened safely.") from error
        snapshot = self.snapshot(include_hash=not directory)
        mode = snapshot["mode"]
        if directory != stat.S_ISDIR(mode):
            self.close()
            raise PackageIdentityError("Plugin payload has an unexpected file type.")
        if not directory and (not stat.S_ISREG(mode) or snapshot["links"] != 1):
            self.close()
            raise PackageIdentityError("Plugin payload has an unexpected hard link or type.")

    def _native_handle(self) -> int:
        if self.fd is not None and os.name == "nt":
            return int(msvcrt.get_osfhandle(self.fd))
        if self.handle is not None:
            return self.handle
        raise PackageIdentityError("Retained Windows handle is unavailable.")

    def snapshot(self, *, include_hash: bool) -> dict[str, Any]:
        if self.fd is not None:
            info = os.fstat(self.fd)
            identity = (
                _win_handle_identity(self._native_handle())
                if os.name == "nt"
                else f"{info.st_dev:x}:{info.st_ino:x}"
            )
        else:
            raise PackageIdentityError("Retained payload handle is unavailable.")
        result: dict[str, Any] = {
            "identity": identity,
            "mode": info.st_mode,
            "size": info.st_size,
            "links": info.st_nlink,
        }
        if include_hash:
            offset = os.lseek(self.fd, 0, os.SEEK_CUR)
            hasher = hashlib.sha256()
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(self.fd, 1024 * 128)
                    if not chunk:
                        break
                    hasher.update(chunk)
            finally:
                os.lseek(self.fd, offset, os.SEEK_SET)
            result["sha256"] = hasher.hexdigest()
        return result

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.handle is not None and os.name == "nt":
            _kernel32.CloseHandle(self.handle)
            self.handle = None


class _Package:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[tuple[str, _Retained]] = []
        self._capture()

    def _capture(self) -> None:
        root = Path(os.path.abspath(self.root))
        try:
            self.items.append((".", _Retained(root, directory=True)))
            seen: set[str] = set()
            for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
                directory_names[:] = sorted(name for name in directory_names if name != "__pycache__")
                file_names.sort()
                current_path = Path(current)
                for name in directory_names:
                    path = current_path / name
                    relative = path.relative_to(root).as_posix()
                    self.items.append((relative, _Retained(path, directory=True)))
                for name in file_names:
                    path = current_path / name
                    relative = path.relative_to(root).as_posix()
                    retained = _Retained(path, directory=False)
                    identity = retained.snapshot(include_hash=False)["identity"]
                    if identity in seen:
                        retained.close()
                        raise PackageIdentityError("Plugin payload contains duplicate file identity.")
                    seen.add(identity)
                    self.items.append((relative, retained))
            captured = tuple(relative for relative, _ in self.items)
            if self._enumerate_names() != captured:
                raise IdentityDriftError(
                    "Plugin payload names drifted during identity capture."
                )
        except Exception:
            self.close()
            raise

    def _enumerate_names(self) -> tuple[str, ...]:
        names = ["."]
        for current, directory_names, file_names in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            directory_names[:] = sorted(
                name for name in directory_names if name != "__pycache__"
            )
            file_names.sort()
            current_path = Path(current)
            names.extend(
                (current_path / name).relative_to(self.root).as_posix()
                for name in directory_names
            )
            names.extend(
                (current_path / name).relative_to(self.root).as_posix()
                for name in file_names
            )
        return tuple(names)

    @property
    def root_identity(self) -> str:
        return self.items[0][1].snapshot(include_hash=False)["identity"]

    def _fingerprint(self, *, include_file_identity: bool) -> str:
        if self._enumerate_names() != tuple(relative for relative, _ in self.items):
            raise IdentityDriftError("Plugin payload names drifted.")
        payload = []
        for relative, retained in self.items:
            if retained.directory:
                continue
            snap = retained.snapshot(include_hash=True)
            if not stat.S_ISREG(snap["mode"]) or snap["links"] != 1:
                raise IdentityDriftError("Plugin payload identity drifted.")
            item = {
                "path": relative,
                "size": snap["size"],
                "sha256": snap["sha256"],
            }
            if include_file_identity:
                item["file_identity"] = snap["identity"]
            payload.append(item)
        return _digest(payload)

    def fingerprint(self) -> str:
        return self._fingerprint(include_file_identity=True)

    def content_fingerprint(self) -> str:
        """Fingerprint payload bytes and paths, excluding filesystem identity."""
        return self._fingerprint(include_file_identity=False)

    def close(self) -> None:
        for _, retained in reversed(self.items):
            retained.close()
        self.items.clear()


def _client_identity(codex_binary: Path, retained: _Retained) -> dict[str, Any]:
    snap = retained.snapshot(include_hash=True)
    if not stat.S_ISREG(snap["mode"]) or snap["links"] != 1:
        raise PackageIdentityError("Codex client identity is not a regular file.")
    return {
        "path": os.path.normcase(str(codex_binary)),
        "size": snap["size"],
        "file_identity": snap["identity"],
        "sha256": snap["sha256"],
    }


def run_inventory(
    codex_binary: str | os.PathLike[str],
    *,
    codex_home: str | os.PathLike[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    binary = Path(codex_binary)
    if not binary.is_absolute():
        raise InventoryCommandError("Codex binary path must be absolute.")
    environment = None
    if codex_home is not None:
        home = Path(codex_home)
        if not home.is_absolute():
            raise InventoryCommandError("Codex home path must be absolute.")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(home)
    paths: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(delete=False) as stdout_file, tempfile.NamedTemporaryFile(delete=False) as stderr_file:
            paths = [stdout_file.name, stderr_file.name]
            try:
                process = subprocess.Popen(
                    [str(binary), "plugin", "list", "--json"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    **({"env": environment} if environment is not None else {}),
                )
            except OSError as error:
                raise InventoryCommandError("Codex plugin inventory command could not start.") from error
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise InventoryCommandError("Codex plugin inventory command timed out.") from error
        for path in paths:
            if os.path.getsize(path) > MAX_OUTPUT_BYTES:
                raise InventoryCommandError("Codex plugin inventory output exceeded its bound.")
        if return_code != 0:
            raise InventoryCommandError("Codex plugin inventory command failed.")
        try:
            with open(paths[0], "r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise InventoryFormatError("Codex plugin inventory JSON is malformed.") from error
    finally:
        for path in paths:
            with contextlib.suppress(OSError):
                os.unlink(path)


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"installed", "available"}:
        raise InventoryFormatError("Plugin inventory has an unexpected top-level shape.")
    if not isinstance(value["installed"], list) or not isinstance(value["available"], list):
        raise InventoryFormatError("Plugin inventory has an unexpected top-level shape.")
    if any(not isinstance(item, dict) for item in value["installed"] + value["available"]):
        raise InventoryFormatError("Plugin inventory contains an invalid record.")
    return value["installed"]


def _canonical_record(raw: Mapping[str, Any], package: _Package | None = None) -> tuple[dict[str, Any], _Package]:
    plugin_id, name, marketplace, installed, enabled = _record_identity(raw)
    version = _text(raw.get("version"), "plugin version")
    source = raw.get("source")
    market_source = raw.get("marketplaceSource")
    if not isinstance(source, dict) or set(source) != {"source", "path"}:
        raise InventoryFormatError("Plugin inventory source is malformed.")
    if not isinstance(market_source, dict) or set(market_source) != {"sourceType", "source"}:
        raise InventoryFormatError("Plugin inventory marketplace source is malformed.")
    source_type = _text(source["source"], "source type")
    if source_type != "local":
        raise InventoryFormatError("Plugin inventory source type is unsupported.")
    source_path = _canonical_path(source["path"], "source path")
    marketplace_source = {
        "source_type": _text(market_source["sourceType"], "marketplace source type"),
        "source": _text(market_source["source"], "marketplace source"),
    }
    owned = package or _Package(source_path)
    return ({
        "plugin_id": plugin_id,
        "name": name,
        "marketplace_name": marketplace,
        "version": version,
        "installed": installed,
        "enabled": enabled,
        "source_type": source_type,
        "source_path": os.path.normcase(os.path.realpath(source_path)),
        "marketplace_source": marketplace_source,
        "root_file_identity": owned.root_identity,
        "package_fingerprint": owned.fingerprint(),
    }, owned)


def _inventory(
    value: Any,
    retained_packages: Mapping[str, _Package] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, _Package]]:
    result: list[dict[str, Any]] = []
    transient: dict[str, _Package] = {}
    seen: set[str] = set()
    try:
        for raw in _records(value):
            plugin_id, name, _, installed, _ = _record_identity(raw)
            if not installed or name != PLUGIN_NAME:
                continue
            package = (
                retained_packages.get(plugin_id)
                if retained_packages is not None
                else None
            )
            record, opened = _canonical_record(raw, package)
            if record["plugin_id"] in seen:
                if package is None:
                    opened.close()
                raise InventoryFormatError("Plugin inventory contains a duplicate plugin ID.")
            seen.add(record["plugin_id"])
            result.append(record)
            if package is None:
                transient[record["plugin_id"]] = opened
        result.sort(key=lambda item: item["plugin_id"])
        return result, transient
    except Exception:
        for package in transient.values():
            package.close()
        raise


def _select(
    records: Sequence[Mapping[str, Any]],
    selector: str,
    executing_plugin_root: Path,
    saved_plugin_id: str | None,
    codex_home: str | os.PathLike[str] | None,
) -> Mapping[str, Any]:
    if selector in {"setup", "repair"}:
        if codex_home is None:
            raise SelectionError("Effective CODEX_HOME is required for setup or repair.")
        _validate_exact_cache_path(executing_plugin_root)
        matches = [
            record
            for record in records
            if record["name"] == PLUGIN_NAME
            and record["installed"]
            and record["enabled"]
            and _path_key(_expected_cache_path(codex_home, record))
            == _path_key(executing_plugin_root)
        ]
        if len(matches) != 1:
            raise SelectionError("Enabled executing plugin identity is missing or ambiguous.")
        if selector == "repair" and saved_plugin_id is not None and _plugin_id(saved_plugin_id) != matches[0]["plugin_id"]:
            raise SelectionError("Saved plugin identity does not match the executing plugin.")
        return matches[0]
    if selector == "disable-schema7":
        if saved_plugin_id is None:
            raise SelectionError("Saved plugin identity is required for schema-7 disable.")
        target = _plugin_id(saved_plugin_id)
    elif selector == "disable-legacy":
        target = LEGACY_PLUGIN_ID
    else:
        raise SelectionError("Plugin operation selector is unsupported.")
    matches = [record for record in records if record["plugin_id"] == target]
    if len(matches) != 1:
        raise SelectionError("Requested installed plugin identity is missing or ambiguous.")
    return matches[0]


def _operation_selected_record(
    record: Mapping[str, Any], executing_package: _Package
) -> dict[str, Any]:
    selected = dict(record)
    selected["executing_cache"] = {
        "path": _path_key(executing_package.root),
        "root_file_identity": executing_package.root_identity,
        "package_fingerprint": executing_package.fingerprint(),
    }
    return selected


class PluginIdentityGuard:
    """Context-managed, non-persistent identity transaction guard."""

    def __init__(
        self,
        codex_binary: str | os.PathLike[str],
        executing_plugin_root: str | os.PathLike[str],
        selector: str,
        *,
        saved_plugin_id: str | None = None,
        codex_home: str | os.PathLike[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        supplied_binary = Path(codex_binary)
        if not supplied_binary.is_absolute():
            raise InventoryCommandError("Codex binary path must be absolute.")
        self.codex_binary = Path(os.path.abspath(supplied_binary))
        self.executing_plugin_root = Path(os.path.abspath(executing_plugin_root))
        self.selector = selector
        self.saved_plugin_id = saved_plugin_id
        self.codex_home = codex_home
        self.timeout = timeout
        self.selected_plugin_id = ""
        self.full_inventory_sha256 = ""
        self.operation_identity_sha256 = ""
        self._client: _Retained | None = None
        self._executing_package: _Package | None = None
        self._packages: dict[str, _Package] = {}
        self._package: _Package | None = None
        self._selected_template: dict[str, Any] | None = None

    def __enter__(self) -> "PluginIdentityGuard":
        try:
            self._client = _Retained(self.codex_binary, directory=False)
            self._executing_package = _Package(self.executing_plugin_root)
            client = _client_identity(self.codex_binary, self._client)
            first, opened = _inventory(
                run_inventory(
                    self.codex_binary,
                    codex_home=self.codex_home,
                    timeout=self.timeout,
                )
            )
            self._packages = opened
            selected = _select(
                first,
                self.selector,
                self.executing_plugin_root,
                self.saved_plugin_id,
                self.codex_home,
            )
            self.selected_plugin_id = str(selected["plugin_id"])
            self._package = self._packages[self.selected_plugin_id]
            if (
                self._package.content_fingerprint()
                != self._executing_package.content_fingerprint()
            ):
                raise SelectionError(
                    "Executing plugin cache payload does not match the selected source payload."
                )
            second, transient = _inventory(
                run_inventory(
                    self.codex_binary,
                    codex_home=self.codex_home,
                    timeout=self.timeout,
                ),
                self._packages,
            )
            for package in transient.values():
                package.close()
            first_digest = _digest(first)
            second_digest = _digest(second)
            if first_digest != second_digest:
                raise IdentityDriftError("Plugin inventory drifted during identity capture.")
            selected_second = next(item for item in second if item["plugin_id"] == self.selected_plugin_id)
            self.full_inventory_sha256 = second_digest
            self._selected_template = dict(selected_second)
            self.operation_identity_sha256 = _digest({
                "namespace": PLUGIN_NAME,
                "selector": self.selector,
                "client": client,
                "selected": _operation_selected_record(
                    selected_second, self._executing_package
                ),
            })
            return self
        except Exception:
            self.close()
            raise

    def assert_unchanged(self, phase: str) -> None:
        _text(phase, "guard phase")
        if (
            not self._client
            or not self._package
            or not self._executing_package
            or not self._selected_template
        ):
            raise IdentityDriftError("Plugin identity guard is not active.")
        records, transient = _inventory(
            run_inventory(
                self.codex_binary,
                codex_home=self.codex_home,
                timeout=self.timeout,
            ),
            self._packages,
        )
        for package in transient.values():
            package.close()
        if _digest(records) != self.full_inventory_sha256:
            raise IdentityDriftError(f"Plugin inventory drifted before {phase}.")
        selected = next((item for item in records if item["plugin_id"] == self.selected_plugin_id), None)
        if selected is None:
            raise IdentityDriftError(f"Selected plugin identity disappeared before {phase}.")
        operation = _digest({
            "namespace": PLUGIN_NAME,
            "selector": self.selector,
            "client": _client_identity(self.codex_binary, self._client),
            "selected": _operation_selected_record(
                selected, self._executing_package
            ),
        })
        if operation != self.operation_identity_sha256:
            raise IdentityDriftError(f"Operation identity drifted before {phase}.")

    def close(self) -> None:
        for package in self._packages.values():
            package.close()
        self._packages.clear()
        self._package = None
        if self._executing_package:
            self._executing_package.close()
            self._executing_package = None
        if self._client:
            self._client.close()
            self._client = None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def guard_plugin_identity(
    codex_binary: str | os.PathLike[str],
    executing_plugin_root: str | os.PathLike[str],
    selector: str,
    *,
    saved_plugin_id: str | None = None,
    codex_home: str | os.PathLike[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PluginIdentityGuard:
    return PluginIdentityGuard(
        codex_binary,
        executing_plugin_root,
        selector,
        saved_plugin_id=saved_plugin_id,
        codex_home=codex_home,
        timeout=timeout,
    )
