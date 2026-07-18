#!/usr/bin/env python3
"""Strict nonsecret state for Codex-Orchestration external model roles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from external_readiness import parse_readiness


SCHEMA = 1
MANAGED_BY = "codex-orchestration"
REGISTRY_FILENAME = ".codex-orchestration-external-models.json"
JOURNAL_FILENAME = ".codex-orchestration-external-transaction.json"
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,199}$")
EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOP_KEYS = frozenset(
    {"schema", "managed_by", "codex_home", "providers", "roles", "cli_trust"}
)
_PROVIDER_KEYS = frozenset(
    {
        "adapter",
        "adapter_version",
        "lane",
        "endpoint",
        "endpoint_sha256",
        "auth_kind",
        "state",
        "qualified",
        "capability_checked_at",
        "capability_source",
        "owned_config_keys",
        "config_snapshot_sha256",
    }
)
_ROLE_KEYS = frozenset(
    {
        "purpose",
        "provider",
        "model",
        "default_effort",
        "supported_efforts",
        "effort_source",
        "agent_name",
        "agent_file",
        "agent_sha256",
        "effort_agents",
        "state",
    }
)
_EFFORT_AGENT_KEYS = frozenset({"name", "file", "sha256"})
_TRUST_KEYS = frozenset(
    {"path", "strategy", "fingerprint", "publisher", "version"}
)
_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)


class RegistryError(ValueError):
    """External registry bytes do not match the exact supported contract."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise RegistryError(detail)


def _nonsecret_key(key: object) -> bool:
    return type(key) is str and not any(part in key.lower() for part in _FORBIDDEN_KEY_PARTS)


def _validate_nonsecret_tree(value: Any, *, path: str = "registry") -> None:
    if type(value) is dict:
        for key, child in value.items():
            _require(_nonsecret_key(key), f"{path} contains a secret-capable key")
            _validate_nonsecret_tree(child, path=f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _validate_nonsecret_tree(child, path=f"{path}[{index}]")
    else:
        _require(
            value is None or type(value) in {str, int, bool},
            f"{path} contains an unsupported value type",
        )


def _valid_path(value: object) -> bool:
    return type(value) is str and bool(value) and "\x00" not in value


def _valid_sha(value: object) -> bool:
    return type(value) is str and SHA256_RE.fullmatch(value) is not None


def _validate_provider(provider_id: str, value: Any) -> None:
    _require(PROVIDER_RE.fullmatch(provider_id) is not None, "invalid provider ID")
    _require(type(value) is dict and set(value) == _PROVIDER_KEYS, "provider shape is unsupported")
    _require(
        type(value["adapter"]) is str
        and PROVIDER_RE.fullmatch(value["adapter"]) is not None,
        "provider adapter is invalid",
    )
    _require(type(value["adapter_version"]) is int and value["adapter_version"] > 0, "adapter version is invalid")
    _require(value["lane"] in {"native", "subscription"}, "provider lane is invalid")
    _require(_valid_path(value["endpoint"]), "provider endpoint is invalid")
    _require(_valid_sha(value["endpoint_sha256"]), "provider endpoint digest is invalid")
    _require(value["auth_kind"] in {"secure_store", "user_helper", "first_party_cli", "none"}, "provider auth kind is invalid")
    parse_readiness(value["state"])
    _require(type(value["qualified"]) is bool, "provider qualification must be boolean")
    _require(value["capability_checked_at"] is None or type(value["capability_checked_at"]) is str, "capability time is invalid")
    _require(value["capability_source"] is None or type(value["capability_source"]) is str, "capability source is invalid")
    owned = value["owned_config_keys"]
    _require(type(owned) is list and len(owned) == len(set(owned)), "owned config keys are invalid")
    _require(all(_nonsecret_key(item) and item.startswith(f"model_providers.{provider_id}.") for item in owned), "owned config key is unsafe")
    snapshot = value["config_snapshot_sha256"]
    _require(snapshot is None or _valid_sha(snapshot), "provider config digest is invalid")


def _validate_role(role_id: str, value: Any, providers: dict[str, Any]) -> None:
    _require(ROLE_RE.fullmatch(role_id) is not None, "invalid role ID")
    _require(type(value) is dict and set(value) == _ROLE_KEYS, "role shape is unsupported")
    _require(type(value["purpose"]) is str and bool(value["purpose"].strip()), "role purpose is invalid")
    _require(value["provider"] in providers, "role provider is not registered")
    _require(type(value["model"]) is str and MODEL_RE.fullmatch(value["model"]) is not None, "role model is invalid")
    efforts = value["supported_efforts"]
    _require(type(efforts) is list and bool(efforts) and len(efforts) == len(set(efforts)), "supported efforts are invalid")
    _require(all(type(item) is str and EFFORT_RE.fullmatch(item) is not None for item in efforts), "supported effort is invalid")
    _require(value["default_effort"] in efforts, "default effort is unsupported")
    _require(type(value["effort_source"]) is str and bool(value["effort_source"]), "effort source is invalid")
    _require(type(value["agent_name"]) is str and ROLE_RE.fullmatch(value["agent_name"]) is not None, "agent name is invalid")
    _require(_valid_path(value["agent_file"]), "agent path is invalid")
    _require(_valid_sha(value["agent_sha256"]), "agent digest is invalid")
    effort_agents = value["effort_agents"]
    _require(
        type(effort_agents) is dict and set(effort_agents) == set(efforts),
        "effort agent variants are invalid",
    )
    for effort, agent in effort_agents.items():
        _require(
            type(agent) is dict and set(agent) == _EFFORT_AGENT_KEYS,
            f"effort agent {effort!r} shape is invalid",
        )
        _require(
            type(agent["name"]) is str and ROLE_RE.fullmatch(agent["name"]) is not None,
            "effort agent name is invalid",
        )
        _require(_valid_path(agent["file"]), "effort agent path is invalid")
        _require(_valid_sha(agent["sha256"]), "effort agent digest is invalid")
    default_agent = effort_agents[value["default_effort"]]
    _require(default_agent["name"] == value["agent_name"], "default agent name is inconsistent")
    _require(default_agent["file"] == value["agent_file"], "default agent path is inconsistent")
    _require(default_agent["sha256"] == value["agent_sha256"], "default agent digest is inconsistent")
    parse_readiness(value["state"])


def _validate_cli_trust(provider_id: str, value: Any, providers: dict[str, Any]) -> None:
    _require(provider_id in providers, "CLI trust provider is not registered")
    _require(type(value) is dict and set(value) == _TRUST_KEYS, "CLI trust shape is unsupported")
    _require(_valid_path(value["path"]), "CLI trust path is invalid")
    _require(value["strategy"] in {"publisher", "sha256"}, "CLI trust strategy is invalid")
    _require(type(value["fingerprint"]) is str and bool(value["fingerprint"]), "CLI fingerprint is invalid")
    _require(value["publisher"] is None or type(value["publisher"]) is str, "CLI publisher is invalid")
    _require(value["version"] is None or type(value["version"]) is str, "CLI version is invalid")


def validate_registry(value: Any) -> dict[str, Any]:
    """Validate one exact schema and reject every secret-capable extension."""

    _require(type(value) is dict, "registry must be an object")
    _validate_nonsecret_tree(value)
    _require(set(value) == _TOP_KEYS, "registry top-level shape is unsupported")
    _require(type(value["schema"]) is int and value["schema"] == SCHEMA, "registry schema is unsupported")
    _require(value["managed_by"] == MANAGED_BY, "registry owner is invalid")
    _require(_valid_path(value["codex_home"]), "Codex home is invalid")
    providers = value["providers"]
    roles = value["roles"]
    cli_trust = value["cli_trust"]
    _require(type(providers) is dict, "providers must be an object")
    _require(type(roles) is dict, "roles must be an object")
    _require(type(cli_trust) is dict, "CLI trust must be an object")
    for provider_id, provider in providers.items():
        _validate_provider(provider_id, provider)
    for role_id, role in roles.items():
        _validate_role(role_id, role, providers)
    agent_names = [
        agent["name"]
        for role in roles.values()
        for agent in role["effort_agents"].values()
    ]
    _require(len(agent_names) == len(set(agent_names)), "agent names collide")
    for provider_id, trust in cli_trust.items():
        _validate_cli_trust(provider_id, trust, providers)
    return value


def empty_registry(codex_home: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "managed_by": MANAGED_BY,
        "codex_home": str(codex_home.expanduser().resolve()),
        "providers": {},
        "roles": {},
        "cli_trust": {},
    }


def canonical_bytes(value: dict[str, Any]) -> bytes:
    validate_registry(value)
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_existing(path: Path) -> os.stat_result | None:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    _require(not path.is_symlink() and stat.S_ISREG(current.st_mode), "registry path is unsafe")
    _require(current.st_nlink == 1, "registry must not be hard linked")
    if os.name == "posix":
        _require(stat.S_IMODE(current.st_mode) == 0o600, "registry mode must be 0600")
    return current


def read_registry(path: Path) -> tuple[dict[str, Any], str]:
    _safe_existing(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"could not read registry: {exc}") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError("registry is not valid UTF-8 JSON") from exc
    return validate_registry(value), sha256_bytes(raw)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_registry(
    path: Path,
    value: dict[str, Any],
    *,
    expected_sha256: str | None = None,
) -> str:
    """Atomically write validated bytes with compare-before-replace semantics."""

    raw = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require(not path.parent.is_symlink(), "registry directory is symlinked")
    existing = _safe_existing(path)
    if expected_sha256 is not None:
        _require(existing is not None, "expected registry is missing")
        _require(sha256_bytes(path.read_bytes()) == expected_sha256, "registry changed before write")
    elif existing is not None:
        raise RegistryError("existing registry requires an expected digest")

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        _require(_safe_existing(temporary) is not None, "staged registry is unsafe")
        if expected_sha256 is not None:
            _require(sha256_bytes(path.read_bytes()) == expected_sha256, "registry changed during write")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return sha256_bytes(raw)
