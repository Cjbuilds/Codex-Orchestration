#!/usr/bin/env python3
"""Strict bundled provider definitions for external Codex roles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


SCHEMA = 1
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,199}$")
EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
BUNDLED_PROVIDER_DIR = Path(__file__).resolve().parent.parent / "providers"
_TOP_KEYS = frozenset(
    {
        "schema",
        "id",
        "version",
        "name",
        "lane",
        "experimental",
        "qualified",
        "base_url",
        "wire_api",
        "auth",
        "models",
        "runtime_identity",
        "subscription_adapter",
    }
)
_MODEL_KEYS = frozenset(
    {
        "default_effort",
        "supported_efforts",
        "context_window",
        "auto_compact_token_limit",
        "capability_source",
    }
)
_SUBSCRIPTION_KEYS = frozenset(
    {"module", "allowed_seats", "allowed_operations", "trust_strategy"}
)


class ProviderError(ValueError):
    """A bundled provider definition is unsupported or unsafe."""


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise ProviderError(detail)


def _endpoint(value: object, *, native: bool) -> str | None:
    if not native:
        _require(value is None, "subscription provider cannot define an API base URL")
        return None
    _require(type(value) is str, "native provider base URL must be a string")
    parsed = urlsplit(value)
    _require(parsed.scheme == "https", "native provider base URL must use HTTPS")
    _require(bool(parsed.hostname), "native provider host is missing")
    _require(parsed.username is None and parsed.password is None, "provider URL cannot contain credentials")
    _require(not parsed.query and not parsed.fragment, "provider URL cannot contain query or fragment")
    return value.rstrip("/")


def validate_provider(value: Any, *, expected_id: str | None = None) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == _TOP_KEYS, "provider template shape is unsupported")
    _require(type(value["schema"]) is int and value["schema"] == SCHEMA, "provider schema is unsupported")
    provider_id = value["id"]
    _require(type(provider_id) is str and PROVIDER_RE.fullmatch(provider_id) is not None, "provider ID is invalid")
    if expected_id is not None:
        _require(provider_id == expected_id, "provider filename and ID do not match")
    _require(type(value["version"]) is int and value["version"] > 0, "provider version is invalid")
    _require(type(value["name"]) is str and bool(value["name"].strip()), "provider name is invalid")
    lane = value["lane"]
    _require(lane in {"native", "subscription"}, "provider lane is invalid")
    _require(type(value["experimental"]) is bool, "experimental must be boolean")
    _require(type(value["qualified"]) is bool, "qualified must be boolean")
    base_url = _endpoint(value["base_url"], native=lane == "native")
    if lane == "native":
        _require(value["wire_api"] == "responses", "native provider must use Responses")
        _require(value["auth"] in {"secure_store", "user_helper", "none"}, "native auth kind is unsupported")
        _require(
            value["subscription_adapter"] is None,
            "native provider cannot define a subscription adapter",
        )
    else:
        _require(value["wire_api"] is None, "subscription provider cannot define a wire API")
        _require(value["auth"] == "first_party_cli", "subscription auth must be first-party CLI")
        adapter = value["subscription_adapter"]
        _require(
            type(adapter) is dict and set(adapter) == _SUBSCRIPTION_KEYS,
            "subscription adapter shape is unsupported",
        )
        _require(
            adapter["module"] == "fable_advisor_mcp",
            "subscription adapter module is not sealed",
        )
        _require(
            adapter["allowed_seats"] == ["planner", "advisor"],
            "subscription seats are unsupported",
        )
        _require(
            adapter["allowed_operations"]
            == ["create_plan", "revise_plan", "review_plan"],
            "subscription operations are unsupported",
        )
        _require(
            adapter["trust_strategy"]
            == "first_party_auth_and_runtime_metadata",
            "subscription trust strategy is unsupported",
        )
    _require(value["runtime_identity"] in {"conditional", "cli_metadata"}, "runtime identity mode is unsupported")
    models = value["models"]
    _require(type(models) is dict and bool(models), "provider must define at least one model")
    for model_id, model in models.items():
        _require(type(model_id) is str and MODEL_RE.fullmatch(model_id) is not None, "model ID is invalid")
        _require(type(model) is dict and set(model) == _MODEL_KEYS, "model shape is unsupported")
        efforts = model["supported_efforts"]
        _require(type(efforts) is list and bool(efforts) and len(efforts) == len(set(efforts)), "model efforts are invalid")
        _require(all(type(item) is str and EFFORT_RE.fullmatch(item) is not None for item in efforts), "model effort is invalid")
        _require(model["default_effort"] in efforts, "default effort is unsupported")
        window = model["context_window"]
        _require(window is None or (type(window) is int and window > 0), "context window is invalid")
        compact = model["auto_compact_token_limit"]
        _require(
            compact is None or (type(compact) is int and compact > 0),
            "auto compact token limit is invalid",
        )
        _require(
            window is None or compact is None or compact < window,
            "auto compact token limit must be below the context window",
        )
        _require(type(model["capability_source"]) is str and bool(model["capability_source"]), "capability source is invalid")
    if base_url is not None:
        value = dict(value)
        value["base_url"] = base_url
    return value


def load_provider(provider_id: str) -> dict[str, Any]:
    _require(PROVIDER_RE.fullmatch(provider_id) is not None, "provider ID is invalid")
    root = BUNDLED_PROVIDER_DIR.resolve()
    path = (root / f"{provider_id}.json").resolve()
    _require(path.parent == root, "provider path escapes the bundled directory")
    _require(path.is_file() and not path.is_symlink(), "bundled provider is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("bundled provider is not valid UTF-8 JSON") from exc
    return validate_provider(value, expected_id=provider_id)


def endpoint_sha256(provider: dict[str, Any]) -> str:
    value = provider.get("base_url") or "subscription"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_effort(provider: dict[str, Any], model_id: str, requested: str) -> str:
    validate_provider(provider, expected_id=provider["id"])
    model = provider["models"].get(model_id)
    if model is None:
        raise ProviderError(f"model {model_id!r} is not in provider {provider['id']!r}")
    effort = model["default_effort"] if requested == "auto" else requested
    if effort not in model["supported_efforts"]:
        supported = ", ".join(model["supported_efforts"])
        raise ProviderError(
            f"effort {effort!r} is unsupported for {model_id!r}; supported: {supported}"
        )
    return effort


def require_qualified(provider: dict[str, Any]) -> None:
    validate_provider(provider, expected_id=provider["id"])
    if not provider["qualified"]:
        raise ProviderError(
            f"provider {provider['id']!r} is not qualified; complete the isolated Gate 0 procedure"
        )
