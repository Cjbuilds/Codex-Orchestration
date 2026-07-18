#!/usr/bin/env python3
"""Preview or apply persistent Codex-Orchestration custom agents."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, NamedTuple

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python < 3.11
    raise SystemExit("Python 3.11 or newer is required (missing tomllib).") from exc


MANAGED_MARKER = (
    "# Managed by codex-orchestration. Standalone custom agent v2."
)
ROUTING_MARKER = "# Managed by codex-orchestration. Model routing only."
PREVIOUS_ROUTING_MARKER = "# Managed by configure-agent-team. Model routing only."
V1_MARKER = "# Managed by configure-agent-team."
LEGACY_CONFIG_MARKERS = {
    ROUTING_MARKER,
    PREVIOUS_ROUTING_MARKER,
    V1_MARKER,
}
LEGACY_LAYER_MARKERS = {ROUTING_MARKER, PREVIOUS_ROUTING_MARKER}

DEFAULT_EXECUTOR_NAME = "codex_orchestration_executor"
DEFAULT_ADVISOR_NAME = "codex_orchestration_advisor"
DEFAULT_EXECUTOR_FILENAME = "codex-orchestration-executor.toml"
DEFAULT_ADVISOR_FILENAME = "codex-orchestration-advisor.toml"
EXECUTOR_NAME = DEFAULT_EXECUTOR_NAME
ADVISOR_NAME = DEFAULT_ADVISOR_NAME
EXECUTOR_FILENAME = DEFAULT_EXECUTOR_FILENAME
ADVISOR_FILENAME = DEFAULT_ADVISOR_FILENAME
LEGACY_EXECUTOR_LAYER = "executor-model.toml"
LEGACY_ADVISOR_LAYER = "advisor-model.toml"
LEGACY_V1_DEFAULT_FILENAME = "orchestrated_executor.toml"

PROVIDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
BUILTIN_PROVIDERS = {"openai", "ollama", "lmstudio", "amazon-bedrock"}
VERSION_TIMEOUT_SECONDS = 10
CATALOG_TIMEOUT_SECONDS = 30
METADATA_COPY_TIMEOUT_SECONDS = 15
TRANSACTION_MARKER = "codex-orchestration-transaction-v1"
TRANSACTION_JOURNAL = ".codex-orchestration-transaction.json"
TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{24}$")

LEGACY_EXECUTOR_DESCRIPTION = (
    "Optional model-only route for delegated work after Codex has independently "
    "decided a compatible subagent is useful. Selecting this role does not "
    "authorize delegation."
)
LEGACY_ADVISOR_DESCRIPTION = (
    "Optional model-only route for a read-only second opinion on the root "
    "orchestrator's plan and proposed executor tasks. It reports only to the "
    "root orchestrator and never directs or coordinates executors. Selecting "
    "this role does not force review or delegation."
)
V1_DESCRIPTION = (
    "Bounded execution worker for implementation, tests, research, and verification."
)
V1_NICKNAMES = ["Forge", "Relay", "Vector", "Scout", "Delta"]

EXECUTOR_DESCRIPTION = (
    "Use for a bounded implementation or verification slice after the root has "
    "decided delegation will help."
)
ADVISOR_DESCRIPTION = (
    "Use once for a review-only second opinion on a non-trivial plan before any "
    "executor starts; this agent requests a read-only sandbox."
)


class ConfigurationError(RuntimeError):
    pass


class LegacyRoute(NamedTuple):
    role: str
    layer_path: Path
    layer_text: str
    routing: dict[str, str]
    has_config_section: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persist a namespaced executor and optional advisor as standalone "
            "Codex custom agents. The selected task model remains the root "
            "orchestrator."
        )
    )
    parser.add_argument("--scope", choices=("project", "personal"), default="project")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument(
        "--codex-home",
        type=Path,
        help="Override CODEX_HOME for personal scope only.",
    )
    parser.add_argument(
        "--personal-route-names",
        action="store_true",
        help=(
            "Use stable, CODEX_HOME-specific personal role names so project roles "
            "from older installations cannot shadow the global native route."
        ),
    )
    executor = parser.add_mutually_exclusive_group(required=True)
    executor.add_argument("--executor-model")
    executor.add_argument(
        "--remove-saved-roles",
        action="store_true",
        help="Remove only the fully managed executor and advisor for this scope.",
    )
    parser.add_argument(
        "--executor-effort",
        help="Exact host-supported effort, or auto (default).",
    )
    parser.add_argument("--executor-provider")
    advisor = parser.add_mutually_exclusive_group()
    advisor.add_argument("--advisor-model", help="Optional exact advisor model ID.")
    advisor.add_argument(
        "--remove-advisor",
        action="store_true",
        help="Remove only an advisor managed by this skill.",
    )
    parser.add_argument(
        "--advisor-effort",
        help="Exact host-supported advisor effort, or auto (default when selected).",
    )
    parser.add_argument("--advisor-provider")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument(
        "--confirm-unlisted-models",
        action="store_true",
        help="Accept exact IDs confirmed by another active-host capability source.",
    )
    parser.add_argument(
        "--migrate-legacy",
        action="store_true",
        help=(
            "Back up and remove exact, fully validated output from prior "
            "known legacy Codex-Orchestration formats. Root model and "
            "agents.max_* settings are preserved."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Write files; default is dry-run.")
    return parser.parse_args()


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def select_agent_identities(personal_base: Path, scoped_personal: bool) -> None:
    """Select fixed project names or stable collision-resistant personal names."""

    global EXECUTOR_NAME, ADVISOR_NAME, EXECUTOR_FILENAME, ADVISOR_FILENAME
    if scoped_personal:
        suffix = hashlib.sha256(
            os.fsencode(str(personal_base.expanduser().resolve()))
        ).hexdigest()[:12]
        EXECUTOR_NAME = f"codex_orchestration_executor_{suffix}"
        ADVISOR_NAME = f"codex_orchestration_advisor_{suffix}"
        EXECUTOR_FILENAME = f"codex-orchestration-executor-{suffix}.toml"
        ADVISOR_FILENAME = f"codex-orchestration-advisor-{suffix}.toml"
    else:
        EXECUTOR_NAME = DEFAULT_EXECUTOR_NAME
        ADVISOR_NAME = DEFAULT_ADVISOR_NAME
        EXECUTOR_FILENAME = DEFAULT_EXECUTOR_FILENAME
        ADVISOR_FILENAME = DEFAULT_ADVISOR_FILENAME


def parse_toml(text: str, label: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"{label} is not valid TOML: {exc}") from exc


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    if not path.is_file():
        raise ConfigurationError(f"Managed path is not a regular file: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"Managed file is not valid UTF-8 and cannot be changed safely: {path}"
        ) from exc


def executor_instructions() -> str:
    return (
        "You are an executor working for the root Codex task.\n\n"
        "Work only on the bounded task packet the root gives you. You may make "
        "local implementation choices inside that slice, but do not redesign the "
        "overall plan, broaden scope, contact the advisor, or spawn more agents.\n\n"
        "Respect assigned file ownership, dependencies, acceptance criteria, and "
        "stop conditions. Inspect before editing, preserve unrelated work, and "
        "report a blocker instead of guessing when the packet is inconsistent, "
        "unsafe, overlapping, or missing required context.\n\n"
        "Run the smallest relevant verification. Return a concise handoff to the "
        "root: status, work completed, files or evidence, checks run, and remaining "
        "risks or blockers. The root owns integration and the final user-facing answer."
    )


def advisor_instructions() -> str:
    return (
        "You are a review-only advisor to the root Codex task. Your saved role "
        "requests a read-only sandbox; regardless of the live permission mode, your "
        "assigned behavior is review only.\n\n"
        "Review only the packet supplied by the root: requirements, repository "
        "facts, plan, executor slices, dependencies, risks, acceptance criteria, "
        "and verification.\n\n"
        "Do not edit files, use mutating tools, spawn agents, delegate work, or "
        "contact executors. Address the root only.\n\n"
        "Return PLAN_APPROVED only when you found no material gap in the supplied "
        "packet. It reports the result of this bounded review, not a guarantee of "
        "objective completeness or success. Otherwise return PLAN_REVISE followed "
        "by concise, prioritized, material gaps and the concrete correction for each. "
        "Do not raise preference-only or stylistic objections."
    )


def v1_executor_instructions() -> str:
    """Exact developer instructions emitted by commit 60e83e2."""
    return (
        "Act as a bounded execution worker for a parent orchestrator.\n\n"
        "Stay inside the assigned objective, file ownership, constraints, and stop "
        "conditions. Do not broaden scope or delegate to more agents. Inspect before "
        "editing, preserve unrelated work, and avoid overlapping writes. Use tools "
        "directly, verify the assigned result, and report blockers instead of guessing.\n\n"
        "Return a concise handoff containing status, files changed or evidence inspected, "
        "result, verification performed, residual risks, and any exact follow-up the "
        "orchestrator must handle. Do not present the final user-facing answer; the "
        "orchestrator owns synthesis and final verification."
    )


def build_agent_file(
    role: str,
    model: str,
    effort: str,
    provider: str | None,
) -> str:
    if role == "executor":
        name = EXECUTOR_NAME
        description = EXECUTOR_DESCRIPTION
        instructions = executor_instructions()
    elif role == "advisor":
        name = ADVISOR_NAME
        description = ADVISOR_DESCRIPTION
        instructions = advisor_instructions()
    else:  # pragma: no cover - guarded by internal callers
        raise ConfigurationError(f"Unknown custom-agent role: {role!r}")

    fields = [
        MANAGED_MARKER,
        f"name = {toml_string(name)}",
        f"description = {toml_string(description)}",
        f"model = {toml_string(model)}",
        f"model_reasoning_effort = {toml_string(effort)}",
    ]
    if provider:
        fields.append(f"model_provider = {toml_string(provider)}")
    if role == "advisor":
        fields.append('sandbox_mode = "read-only"')
    fields.extend(
        [
            f"developer_instructions = {toml_string(instructions)}",
            "",
        ]
    )
    result = "\n".join(fields)
    parse_toml(result, f"Generated {role} custom agent")
    return result


def has_exact_marker(text: str, markers: set[str]) -> bool:
    outside = outside_line_flags(text)
    return any(
        is_outside and line.strip() in markers
        for line, is_outside in zip(text.splitlines(keepends=True), outside)
    )


def has_first_line_marker(text: str, markers: set[str]) -> bool:
    lines = text.splitlines()
    return bool(lines and lines[0] in markers)


def legacy_config_marker(text: str) -> str | None:
    """Return one legacy marker only when it is the exact first physical line."""
    lines = text.splitlines(keepends=True)
    outside = outside_line_flags(text)
    matches = [
        (index, line.rstrip("\r\n"))
        for index, (line, is_outside) in enumerate(zip(lines, outside))
        if is_outside and line.rstrip("\r\n") in LEGACY_CONFIG_MARKERS
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ConfigurationError(
            "Multiple legacy ownership markers were found in config.toml; refusing "
            "to infer which release owned it."
        )
    index, marker = matches[0]
    if index != 0:
        raise ConfigurationError(
            "A legacy ownership marker is not in its emitted first-line location; "
            "it does not prove ownership."
        )
    return marker


def validate_managed_agent(text: str, path: Path, role: str) -> bool:
    if not text:
        return False
    if not has_first_line_marker(text, {MANAGED_MARKER}):
        return False
    parsed = parse_toml(text, f"Existing custom agent {path}")
    expected_name = EXECUTOR_NAME if role == "executor" else ADVISOR_NAME
    expected_description = (
        EXECUTOR_DESCRIPTION if role == "executor" else ADVISOR_DESCRIPTION
    )
    expected_instructions = (
        executor_instructions() if role == "executor" else advisor_instructions()
    )
    allowed = {
        "name",
        "description",
        "model",
        "model_reasoning_effort",
        "model_provider",
        "developer_instructions",
    }
    if role == "advisor":
        allowed.add("sandbox_mode")
    extra = set(parsed) - allowed
    missing = {
        "name",
        "description",
        "model",
        "model_reasoning_effort",
        "developer_instructions",
    } - set(parsed)
    if extra or missing:
        detail = []
        if extra:
            detail.append("extra=" + ",".join(sorted(extra)))
        if missing:
            detail.append("missing=" + ",".join(sorted(missing)))
        raise ConfigurationError(
            f"Managed custom agent {path} has a changed schema ({'; '.join(detail)}). "
            "Refusing to replace or remove it."
        )
    expected_constants = {
        "name": expected_name,
        "description": expected_description,
        "developer_instructions": expected_instructions,
    }
    if role == "advisor":
        expected_constants["sandbox_mode"] = "read-only"
    changed = sorted(
        key for key, value in expected_constants.items() if parsed.get(key) != value
    )
    if role == "executor" and "sandbox_mode" in parsed:
        changed.append("sandbox_mode")
    route_keys = ("model", "model_reasoning_effort", "model_provider")
    non_strings = sorted(
        key for key in route_keys if key in parsed and not isinstance(parsed[key], str)
    )
    if changed or non_strings:
        detail = ", ".join(sorted(set(changed + non_strings)))
        raise ConfigurationError(
            f"Managed custom agent {path} has user-modified managed fields: {detail}. "
            "Refusing to overwrite it."
        )
    return True


def _escaped(text: str, index: int) -> bool:
    count = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        count += 1
        index -= 1
    return count % 2 == 1


def _advance_multiline_state(line: str, state: str | None) -> str | None:
    """Track TOML multiline strings closely enough to locate real table headers."""
    index = 0
    length = len(line)
    while index < length:
        if state is not None:
            found = line.find(state, index)
            while found >= 0 and state == '"""' and _escaped(line, found):
                found = line.find(state, found + 3)
            if found < 0:
                return state
            state = None
            index = found + 3
            continue
        char = line[index]
        if char == "#":
            return None
        if line.startswith('"""', index) or line.startswith("'''", index):
            state = line[index : index + 3]
            index += 3
            continue
        if char == '"':
            index += 1
            while index < length:
                if line[index] == '"' and not _escaped(line, index):
                    index += 1
                    break
                index += 1
            continue
        if char == "'":
            closing = line.find("'", index + 1)
            index = length if closing < 0 else closing + 1
            continue
        index += 1
    return state


def outside_line_flags(text: str) -> list[bool]:
    flags: list[bool] = []
    state: str | None = None
    for line in text.splitlines(keepends=True):
        flags.append(state is None)
        state = _advance_multiline_state(line, state)
    return flags


def _table_header(line: str) -> str | None:
    stripped = line.lstrip()
    if not stripped.startswith("["):
        return None
    array = stripped.startswith("[[")
    opening = 2 if array else 1
    closing = "]]" if array else "]"
    quote: str | None = None
    escaped = False
    index = opening
    while index < len(stripped):
        char = stripped[index]
        if quote == '"':
            if char == '"' and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            escaped = False
            index += 1
            continue
        if stripped.startswith(closing, index):
            tail = stripped[index + len(closing) :].strip()
            if tail and not tail.startswith("#"):
                return None
            return stripped[opening:index].strip()
        index += 1
    return None


def real_table_headers(text: str) -> list[tuple[int, str]]:
    headers: list[tuple[int, str]] = []
    flags = outside_line_flags(text)
    for index, (line, outside) in enumerate(
        zip(text.splitlines(keepends=True), flags)
    ):
        if not outside:
            continue
        header = _table_header(line)
        if header is not None:
            headers.append((index, header))
    return headers


def remove_legacy_tables_and_markers(text: str, roles: set[str]) -> str:
    """Surgically remove proven legacy role tables and exact ownership markers."""
    original_parsed = parse_toml(text, "Existing Codex config")
    lines = text.splitlines(keepends=True)
    headers = real_table_headers(text)
    spans: list[tuple[int, int]] = []
    for role in sorted(roles):
        target = f"agents.{role}"
        matches = [index for index, (_, header) in enumerate(headers) if header == target]
        if len(matches) != 1:
            raise ConfigurationError(
                f"Could not identify exactly one generated [{target}] table in config.toml. "
                "Refusing surgical migration."
            )
        header_index = matches[0]
        start = headers[header_index][0]
        end = headers[header_index + 1][0] if header_index + 1 < len(headers) else len(lines)
        spans.append((start, end))

    removed_indexes: set[int] = set()
    for start, end in spans:
        removed_indexes.add(start)
        seen_assignments: set[str] = set()
        for index in range(start + 1, end):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith("#"):
                # Comments and spacing may belong to the user even when they follow
                # a generated table, so keep them in place.
                continue
            assignment = ASSIGNMENT_RE.match(lines[index])
            key = assignment.group(1) if assignment else None
            if key not in {"description", "config_file"}:
                raise ConfigurationError(
                    "Legacy role table contains text outside its two generated "
                    "assignments; refusing to remove nearby user content."
                )
            seen_assignments.add(key)
            removed_indexes.add(index)
        if seen_assignments != {"description", "config_file"}:
            raise ConfigurationError(
                "Legacy role table does not contain exactly the generated description "
                "and config_file assignments."
            )
    outside = outside_line_flags(text)
    output = [
        line
        for index, line in enumerate(lines)
        if index not in removed_indexes
        and not (
            outside[index] and line.rstrip("\r\n") in LEGACY_CONFIG_MARKERS
        )
    ]
    result = "".join(output)
    new_parsed = parse_toml(result, "Migrated Codex config")

    def without_roles(value: dict[str, Any]) -> dict[str, Any]:
        copied = deepcopy(value)
        agents = copied.get("agents")
        if isinstance(agents, dict):
            for role in roles:
                agents.pop(role, None)
            if not agents:
                copied.pop("agents", None)
        return copied

    if without_roles(original_parsed) != without_roles(new_parsed):
        raise ConfigurationError(
            "Legacy config cleanup would change unrelated Codex settings; refusing migration."
        )
    return result


def validate_legacy_layer(text: str, path: Path) -> dict[str, str] | None:
    if not text:
        return None
    if not has_first_line_marker(text, LEGACY_LAYER_MARKERS):
        if has_exact_marker(text, LEGACY_LAYER_MARKERS):
            raise ConfigurationError(
                f"Legacy marker in {path} is not on its emitted first line; it "
                "does not prove ownership."
            )
        return None
    parsed = parse_toml(text, f"Legacy model layer {path}")
    allowed = {"model", "model_reasoning_effort", "model_provider"}
    if set(parsed) - allowed or "model" not in parsed:
        raise ConfigurationError(
            f"Legacy managed model layer {path} has an unknown or incomplete schema."
        )
    if any(not isinstance(value, str) for value in parsed.values()):
        raise ConfigurationError(f"Legacy model layer {path} has non-string routing values.")
    return dict(parsed)


def inspect_legacy_routes(
    config_text: str,
    parsed_config: dict[str, Any],
    base: Path,
) -> tuple[dict[str, LegacyRoute], list[LegacyRoute]]:
    agents = parsed_config.get("agents") or {}
    if not isinstance(agents, dict):
        raise ConfigurationError("Existing agents configuration is not a TOML table")
    config_owned = legacy_config_marker(config_text) is not None
    managed: dict[str, LegacyRoute] = {}
    orphans: list[LegacyRoute] = []
    specifications = (
        (
            "executor",
            LEGACY_EXECUTOR_LAYER,
            LEGACY_EXECUTOR_DESCRIPTION,
        ),
        (
            "advisor",
            LEGACY_ADVISOR_LAYER,
            LEGACY_ADVISOR_DESCRIPTION,
        ),
    )
    for role, filename, description in specifications:
        path = base / "agents" / filename
        text = read_text(path)
        routing = validate_legacy_layer(text, path)
        section = agents.get(role)
        expected_path = f"agents/{filename}"
        potential = section is not None or routing is not None
        if not potential:
            continue
        if section is None:
            if routing is not None:
                orphans.append(LegacyRoute(role, path, text, routing, False))
            continue
        if not config_owned:
            # A user-owned legacy-named role does not collide with the namespaced role.
            if routing is not None:
                raise ConfigurationError(
                    f"Legacy-looking [{role}] route has incomplete ownership provenance. "
                    "Remove or migrate it manually before continuing."
                )
            continue
        if not isinstance(section, dict) or set(section) != {"description", "config_file"}:
            raise ConfigurationError(
                f"Legacy-looking [agents.{role}] has changed fields; refusing migration."
            )
        if section.get("description") != description or section.get("config_file") != expected_path:
            raise ConfigurationError(
                f"Legacy-looking [agents.{role}] does not match a published template."
            )
        if routing is None:
            raise ConfigurationError(
                f"Legacy [agents.{role}] points to a missing or unmanaged layer {path}."
            )
        managed[role] = LegacyRoute(role, path, text, routing, True)
    return managed, orphans


def validate_v1_agent(text: str, path: Path) -> bool:
    if not has_first_line_marker(text, {V1_MARKER}):
        return False
    parsed = parse_toml(text, f"Legacy v1 agent {path}")
    allowed = {
        "name",
        "description",
        "nickname_candidates",
        "model",
        "model_reasoning_effort",
        "model_provider",
        "developer_instructions",
    }
    required = {
        "name",
        "description",
        "nickname_candidates",
        "model",
        "developer_instructions",
    }
    if set(parsed) - allowed or required - set(parsed):
        raise ConfigurationError(
            f"Legacy marker found in {path}, but its schema is not the published v1 template."
        )
    expected = {
        "description": V1_DESCRIPTION,
        "nickname_candidates": V1_NICKNAMES,
        "developer_instructions": v1_executor_instructions(),
    }
    if any(parsed.get(key) != value for key, value in expected.items()):
        raise ConfigurationError(
            f"Legacy v1 agent {path} was modified; refusing automatic deletion."
        )
    name = parsed.get("name")
    if not isinstance(name, str) or not ROLE_RE.fullmatch(name) or path.stem != name:
        raise ConfigurationError(
            f"Legacy v1 agent {path} has a filename/name mismatch or invalid role name."
        )
    for key in ("model", "model_reasoning_effort", "model_provider"):
        if key in parsed and not isinstance(parsed[key], str):
            raise ConfigurationError(f"Legacy v1 agent {path} has non-string {key}.")
    return True


def discover_v1_agents(
    agents_dir: Path,
    excluded: set[Path],
    config_owned: bool,
) -> list[tuple[Path, str]]:
    if not agents_dir.exists():
        return []
    if agents_dir.is_symlink() or not agents_dir.is_dir():
        raise ConfigurationError(f"Refusing to inspect unsafe agents directory {agents_dir}.")
    found: list[tuple[Path, str]] = []
    for candidate in sorted(agents_dir.glob("*.toml")):
        if candidate in excluded:
            continue
        if candidate.is_symlink():
            raise ConfigurationError(
                f"Refusing to inspect symlinked custom-agent file {candidate}."
            )
        if not candidate.is_file():
            continue
        content = read_text(candidate)
        if not has_first_line_marker(content, {V1_MARKER}):
            if has_exact_marker(content, {V1_MARKER}):
                raise ConfigurationError(
                    f"Legacy marker in {candidate} is not on its emitted first line; "
                    "it does not prove ownership."
                )
            continue
        if not config_owned:
            raise ConfigurationError(
                f"Legacy v1 marker found in {candidate} without matching config ownership."
            )
        validate_v1_agent(content, candidate)
        found.append((candidate, content))
    return found


def scan_name_conflicts(
    agents_dir: Path,
    managed_targets: dict[Path, str],
) -> None:
    if not agents_dir.exists():
        return
    if agents_dir.is_symlink() or not agents_dir.is_dir():
        raise ConfigurationError(f"Refusing to inspect unsafe agents directory {agents_dir}.")
    wanted = {EXECUTOR_NAME, ADVISOR_NAME}
    for candidate in sorted(agents_dir.glob("*.toml")):
        if candidate.is_symlink():
            raise ConfigurationError(
                f"Refusing to inspect symlinked custom-agent file {candidate}."
            )
        if not candidate.is_file():
            continue
        content = read_text(candidate)
        parsed = parse_toml(content, f"Custom-agent file {candidate}")
        name = parsed.get("name")
        if name not in wanted:
            continue
        expected_role = managed_targets.get(candidate)
        if expected_role is not None:
            if validate_managed_agent(content, candidate, expected_role):
                continue
        raise ConfigurationError(
            f"Custom-agent name {name!r} is already defined by unmanaged file {candidate}."
        )


def resolve_codex_executable(codex_bin: str) -> str:
    executable = shutil.which(codex_bin) if "/" not in codex_bin else codex_bin
    if not executable:
        raise ConfigurationError(f"Codex executable not found: {codex_bin}")
    path = Path(executable).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ConfigurationError(
            f"Codex executable is not a regular executable file: {path}"
        )
    return str(path)


def codex_subprocess_env(codex_home: Path | None) -> dict[str, str]:
    environment = os.environ.copy()
    if codex_home is not None:
        environment["CODEX_HOME"] = str(codex_home)
    return environment


def catalog_source(
    codex_bin: str,
    cwd: Path | None = None,
    codex_home: Path | None = None,
) -> str:
    executable = resolve_codex_executable(codex_bin)
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=VERSION_TIMEOUT_SECONDS,
            cwd=cwd,
            env=codex_subprocess_env(codex_home),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigurationError(
            f"Codex binary version check timed out after {VERSION_TIMEOUT_SECONDS}s: "
            f"{executable}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"Codex binary version check returned invalid UTF-8: {executable}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"Could not inspect Codex binary version: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown version"
        raise ConfigurationError(f"Could not inspect Codex binary version: {detail}")
    version = completed.stdout.strip() or completed.stderr.strip() or "version unavailable"
    return f"{executable} ({version}); requested by --codex-bin={codex_bin}"


def load_catalog(
    codex_bin: str,
    provider: str | None,
    cwd: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, dict[str, Any]]:
    executable = resolve_codex_executable(codex_bin)
    command = [executable, "debug", "models"]
    if provider:
        command.extend(["-c", f'model_provider="{provider}"'])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=CATALOG_TIMEOUT_SECONDS,
            cwd=cwd,
            env=codex_subprocess_env(codex_home),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigurationError(
            f"Codex model inspection timed out after {CATALOG_TIMEOUT_SECONDS}s "
            f"for provider {provider or 'active'} using {executable}."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            "Codex model inspection returned invalid UTF-8 for provider "
            f"{provider or 'active'} using {executable}."
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"Could not run Codex model inspection: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise ConfigurationError(
            f"Could not inspect models for provider {provider or 'active'}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Codex returned invalid model JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("Codex model JSON root must be an object")
    models = payload.get("models")
    if not isinstance(models, list):
        raise ConfigurationError("Codex model JSON must contain a models array")
    return {
        model["slug"]: model
        for model in models
        if isinstance(model, dict) and isinstance(model.get("slug"), str)
    }


def validate_model(
    label: str,
    model_id: str,
    effort: str,
    catalog: dict[str, dict[str, Any]],
    confirm_unlisted: bool,
) -> str | None:
    if not model_id.strip() or model_id != model_id.strip():
        raise ConfigurationError(f"{label} model ID must be a non-empty exact ID")
    if not effort.strip() or effort != effort.strip():
        raise ConfigurationError(f"{label} effort must be a non-empty exact value")
    if model_id not in catalog:
        if not confirm_unlisted:
            raise ConfigurationError(
                f"{label} model {model_id!r} is not in the inspected CLI catalog. "
                "Confirm it through the active host or provider, then rerun with "
                "--confirm-unlisted-models."
            )
        return f"{label} model {model_id!r} was accepted from an external capability check."
    if effort == "auto":
        return None
    levels = catalog[model_id].get("supported_reasoning_levels", [])
    if not isinstance(levels, list):
        levels = []
    supported = {
        value
        for item in levels
        if isinstance(item, dict)
        for value in [item.get("effort")]
        if isinstance(value, str) and value
    }
    if effort not in supported:
        if not confirm_unlisted:
            listed = ", ".join(sorted(value for value in supported if value)) or "none"
            raise ConfigurationError(
                f"{label} effort {effort!r} is not listed for {model_id!r}; "
                f"catalog efforts: {listed}."
            )
        return f"{label} effort {effort!r} was accepted from an external capability check."
    return None


def resolve_role_effort(
    requested_effort: str,
    label: str,
    model_id: str,
    catalog: dict[str, dict[str, Any]],
) -> str:
    if requested_effort != "auto":
        return requested_effort
    model = catalog.get(model_id) or {}
    default = model.get("default_reasoning_level")
    if isinstance(default, str) and default:
        return default
    raise ConfigurationError(
        f"Cannot determine the default reasoning effort for {label.lower()} model "
        f"{model_id!r}. Choose an explicit {label.lower()} effort."
    )


def validate_provider(provider: str | None, config: dict[str, Any]) -> None:
    if provider is None:
        return
    if not PROVIDER_RE.fullmatch(provider):
        raise ConfigurationError(f"Invalid provider ID: {provider!r}")
    configured = config.get("model_providers") or {}
    if not isinstance(configured, dict):
        raise ConfigurationError("Personal model_providers setting is not a TOML table")
    if provider not in BUILTIN_PROVIDERS and provider not in configured:
        raise ConfigurationError(
            f"Provider {provider!r} is neither built in nor defined in the personal "
            "Codex config. Configure and authenticate it separately first."
        )


def ensure_safe_managed_path(path: Path, base: Path) -> None:
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise ConfigurationError(
            f"Managed path {path} is outside the Codex configuration directory {base}."
        ) from exc
    candidates = [base]
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        candidates.append(cursor)
    for candidate in candidates:
        if candidate.is_symlink():
            raise ConfigurationError(
                f"Refusing symlinked managed path component {candidate}."
            )


def unified_diff(path: Path, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def stage_text(
    path: Path,
    content: str,
    mode: int = 0o600,
    staged_path: Path | None = None,
) -> Path:
    staged: Path | None = None
    try:
        if staged_path is not None:
            if staged_path.parent != path.parent or staged_path.is_symlink():
                raise ConfigurationError(
                    f"Unsafe explicit staging path for {path}: {staged_path}"
                )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(staged_path, flags, mode)
            staged = staged_path
            with os.fdopen(
                descriptor, "w", encoding="utf-8", newline=""
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staged, mode)
            return staged
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            delete=False,
        ) as handle:
            staged = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, mode)
        return staged
    except OSError:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise


def _write_staged_content(
    staged: Path,
    content: str,
    expected_identity: tuple[int, int],
) -> None:
    """Write only to a private staged inode; the live file is never truncated."""
    flags = os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(staged, flags)
    try:
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != expected_identity:
            raise ConfigurationError(
                f"Staged-file identity changed while preparing {staged}."
            )
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise ConfigurationError(
                f"Refusing unsafe staged configuration file {staged}."
            )
        encoded = content.encode("utf-8")
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:  # pragma: no cover - defensive OS contract
                raise OSError("short write while staging configuration")
            remaining = remaining[written:]
        os.ftruncate(descriptor, os.lseek(descriptor, 0, os.SEEK_CUR))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return current.st_dev, current.st_ino


def _xattr_snapshot(path: Path) -> dict[str, bytes] | bytes | None:
    if all(hasattr(os, name) for name in ("listxattr", "getxattr")):
        try:
            names = os.listxattr(path, follow_symlinks=False)
            return {
                name: os.getxattr(path, name, follow_symlinks=False)
                for name in sorted(names)
            }
        except (OSError, TypeError) as exc:
            raise ConfigurationError(
                f"Could not inspect extended attributes for {path}: {exc}"
            ) from exc
    if sys.platform == "darwin" and Path("/usr/bin/xattr").is_file():
        try:
            completed = subprocess.run(
                ["/usr/bin/xattr", "-lx", str(path)],
                capture_output=True,
                check=False,
                timeout=METADATA_COPY_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigurationError(
                f"Could not inspect extended attributes for {path}: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ConfigurationError(
                f"Could not inspect extended attributes for {path}: {detail}"
            )
        return completed.stdout
    return None


def _acl_snapshot(path: Path) -> tuple[bytes, ...] | None:
    if sys.platform != "darwin" or not Path("/bin/ls").is_file():
        return None
    try:
        completed = subprocess.run(
            ["/bin/ls", "-led", str(path)],
            capture_output=True,
            check=False,
            timeout=METADATA_COPY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError(f"Could not inspect ACLs for {path}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigurationError(f"Could not inspect ACLs for {path}: {detail}")
    # The first line contains the filename and size. Remaining lines are ACL entries.
    return tuple(completed.stdout.splitlines()[1:])


def _windows_security_descriptor(path: Path) -> bytes | None:
    """Read owner, group, DACL, and mandatory label self-relatively."""

    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    requested = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000010
    error_insufficient_buffer = 122
    maximum_attempts = 3
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    get_file_security = advapi.GetFileSecurityW
    get_file_security.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_file_security.restype = wintypes.BOOL
    for _ in range(maximum_attempts):
        needed = wintypes.DWORD()
        ctypes.set_last_error(0)
        first = get_file_security(
            str(path), requested, None, 0, ctypes.byref(needed)
        )
        error = ctypes.get_last_error()
        if first:
            raise ConfigurationError(
                f"Windows returned a descriptor without a sizing buffer for {path}."
            )
        if error != error_insufficient_buffer or needed.value <= 0:
            raise ConfigurationError(
                f"Could not size the Windows security descriptor for {path}: {error}."
            )
        allocated = needed.value
        buffer = ctypes.create_string_buffer(allocated)
        ctypes.set_last_error(0)
        if get_file_security(
            str(path),
            requested,
            ctypes.cast(buffer, ctypes.c_void_p),
            allocated,
            ctypes.byref(needed),
        ):
            return bytes(buffer.raw[: needed.value])
        error = ctypes.get_last_error()
        if error != error_insufficient_buffer:
            raise ConfigurationError(
                f"Could not read the Windows security descriptor for {path}: {error}."
            )
    raise ConfigurationError(
        f"Windows security descriptor changed repeatedly while reading {path}."
    )


def _windows_security_sddl(descriptor: bytes | None) -> str | None:
    """Canonicalize the selected descriptor components as SDDL."""

    if descriptor is None:
        return None
    if os.name != "nt":
        raise ConfigurationError("Windows security metadata was supplied off Windows.")
    import ctypes
    from ctypes import wintypes

    requested = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000010
    sddl_revision_1 = 1
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG),
    )
    convert.restype = wintypes.BOOL
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(descriptor)
    rendered = wintypes.LPWSTR()
    length = wintypes.ULONG()
    if not convert(
        ctypes.cast(buffer, ctypes.c_void_p),
        sddl_revision_1,
        requested,
        ctypes.byref(rendered),
        ctypes.byref(length),
    ):
        error = ctypes.get_last_error()
        raise ConfigurationError(
            f"Could not canonicalize a Windows security descriptor: {error}."
        )
    try:
        if not rendered:
            raise ConfigurationError(
                "Windows security descriptor canonicalization returned no text."
            )
        return rendered.value
    finally:
        kernel32.LocalFree(ctypes.cast(rendered, ctypes.c_void_p))


def _windows_security_signature(path: Path) -> str | None:
    return _windows_security_sddl(_windows_security_descriptor(path))


def _set_windows_security_descriptor(path: Path, descriptor: bytes | None) -> None:
    """Apply and canonically verify access-control metadata on one staged file."""

    if descriptor is None:
        return
    if os.name != "nt":
        raise ConfigurationError("Windows security metadata was supplied off Windows.")
    import ctypes
    from ctypes import wintypes

    requested = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000010
    expected = _windows_security_sddl(descriptor)
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    set_file_security = advapi.SetFileSecurityW
    set_file_security.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    set_file_security.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(descriptor)
    if not set_file_security(
        str(path), requested, ctypes.cast(buffer, ctypes.c_void_p)
    ):
        error = ctypes.get_last_error()
        raise ConfigurationError(
            f"Could not apply the Windows security descriptor for {path}: {error}."
        )
    if _windows_security_signature(path) != expected:
        raise ConfigurationError(
            f"Windows security descriptor verification failed for {path}."
        )


def _metadata_signature(path: Path) -> tuple[Any, ...]:
    current = path.stat(follow_symlinks=False)
    return (
        stat.S_IMODE(current.st_mode),
        current.st_uid,
        current.st_gid,
        getattr(current, "st_flags", None),
        current.st_mtime_ns,
        _xattr_snapshot(path),
        _acl_snapshot(path),
        _windows_security_signature(path),
    )


def _metadata_digest(path: Path) -> str:
    def canonical(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        if isinstance(value, dict):
            return {
                str(key): canonical(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    encoded = json.dumps(
        canonical(_metadata_signature(path)),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stage_existing_file(
    path: Path,
    content: str,
    staged_path: Path | None = None,
) -> Path:
    """Clone a live file's security metadata, then stage complete new bytes."""
    staged = stage_text(path, "", 0o600, staged_path)
    try:
        windows_security = _windows_security_descriptor(path)
        cp = Path("/bin/cp")
        if os.name == "posix" and cp.is_file() and os.access(cp, os.X_OK):
            try:
                completed = subprocess.run(
                    [str(cp), "-a", str(path), str(staged)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=METADATA_COPY_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
                raise ConfigurationError(
                    f"Could not clone metadata for {path}: {exc}"
                ) from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise ConfigurationError(
                    f"Could not clone metadata for {path}: {detail or 'cp failed'}"
                )
        else:  # pragma: no cover - non-POSIX fallback
            shutil.copy2(path, staged, follow_symlinks=False)

        staged_identity = _path_identity(staged)
        if staged_identity is None:
            raise ConfigurationError(f"Metadata clone disappeared: {staged}")
        _write_staged_content(staged, content, staged_identity)

        source_stat = path.stat(follow_symlinks=False)
        if hasattr(os, "chown"):
            os.chown(
                staged,
                source_stat.st_uid,
                source_stat.st_gid,
                follow_symlinks=False,
            )
        shutil.copystat(path, staged, follow_symlinks=False)
        _set_windows_security_descriptor(staged, windows_security)
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        if _metadata_signature(staged) != _metadata_signature(path):
            raise ConfigurationError(
                f"Could not preserve all supported metadata while staging {path}."
            )
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _link_original_tombstone(
    destination: Path,
    tombstone: Path,
    expected_identity: tuple[int, int],
    placeholder_identity: tuple[int, int],
) -> None:
    if tombstone.is_symlink() or _path_identity(tombstone) != placeholder_identity:
        raise ConfigurationError(f"Update tombstone changed before use: {tombstone}.")
    tombstone.unlink()
    os.link(destination, tombstone, follow_symlinks=False)
    destination_stat = destination.stat(follow_symlinks=False)
    tombstone_stat = tombstone.stat(follow_symlinks=False)
    if (
        (destination_stat.st_dev, destination_stat.st_ino) != expected_identity
        or (tombstone_stat.st_dev, tombstone_stat.st_ino) != expected_identity
        or destination_stat.st_nlink != 2
        or tombstone_stat.st_nlink != 2
    ):
        raise ConfigurationError(
            f"A hard-link race was detected while staging the update for {destination}."
        )
    _fsync_directory(destination.parent)


def _replace_staged_atomically(
    staged: Path,
    destination: Path,
    tombstone: Path,
    staged_identity: tuple[int, int],
    original_identity: tuple[int, int],
    expected_metadata: tuple[Any, ...],
    expected_content_sha256: str,
) -> None:
    staged_stat = staged.stat(follow_symlinks=False)
    if (
        staged.is_symlink()
        or (staged_stat.st_dev, staged_stat.st_ino) != staged_identity
        or staged_stat.st_nlink != 1
    ):
        raise ConfigurationError(f"Unsafe staged replacement for {destination}.")
    destination_stat = destination.stat(follow_symlinks=False)
    if (
        destination.is_symlink()
        or (destination_stat.st_dev, destination_stat.st_ino) != original_identity
        or destination_stat.st_nlink != 2
        or _metadata_signature(destination) != expected_metadata
        or _sha256_file(destination) != expected_content_sha256
        or _metadata_signature(staged) != expected_metadata
    ):
        raise ConfigurationError(
            f"Destination metadata changed before publication: {destination}."
        )
    os.replace(staged, destination)
    _fsync_directory(destination.parent)
    destination_stat = destination.stat(follow_symlinks=False)
    tombstone_identity = _path_identity(tombstone)
    if (
        destination.is_symlink()
        or (destination_stat.st_dev, destination_stat.st_ino) != staged_identity
        or destination_stat.st_nlink != 1
    ):
        raise ConfigurationError(
            f"Atomic replacement verification failed for {destination}."
        )
    if tombstone_identity != original_identity or tombstone.is_symlink():
        raise ConfigurationError(
            f"Original inode changed during publication: {destination}."
        )
    tombstone_stat = tombstone.stat(follow_symlinks=False)
    if (
        tombstone_stat.st_nlink != 1
        or _metadata_signature(tombstone) != expected_metadata
        or _sha256_file(tombstone) != expected_content_sha256
    ):
        raise ConfigurationError(
            f"Original content or metadata changed during publication: {destination}."
        )


def _move_original_to_tombstone(
    destination: Path,
    tombstone: Path,
    original_identity: tuple[int, int],
    placeholder_identity: tuple[int, int],
    expected_metadata: tuple[Any, ...],
    expected_content_sha256: str,
) -> None:
    destination_stat = destination.stat(follow_symlinks=False)
    if (
        destination.is_symlink()
        or (destination_stat.st_dev, destination_stat.st_ino) != original_identity
        or destination_stat.st_nlink != 1
        or _metadata_signature(destination) != expected_metadata
        or _sha256_file(destination) != expected_content_sha256
        or tombstone.is_symlink()
        or _path_identity(tombstone) != placeholder_identity
    ):
        raise ConfigurationError(
            f"Destination changed before atomic deletion: {destination}."
        )
    os.replace(destination, tombstone)
    _fsync_directory(destination.parent)
    tombstone_stat = tombstone.stat(follow_symlinks=False)
    if (
        tombstone.is_symlink()
        or not stat.S_ISREG(tombstone_stat.st_mode)
        or (tombstone_stat.st_dev, tombstone_stat.st_ino) != original_identity
        or tombstone_stat.st_nlink != 1
        or _metadata_signature(tombstone) != expected_metadata
        or _sha256_file(tombstone) != expected_content_sha256
    ):
        raise ConfigurationError(
            f"Deletion tombstone could not be verified: {tombstone}."
        )


def _restore_original_from_tombstone(
    destination: Path,
    tombstone: Path | None,
    original_identity: tuple[int, int],
    installed_identity: tuple[int, int] | None,
) -> None:
    destination_identity = _path_identity(destination)
    tombstone_identity = (
        _path_identity(tombstone) if isinstance(tombstone, Path) else None
    )
    if destination_identity == original_identity:
        destination_stat = destination.stat(follow_symlinks=False)
        expected_links = 2 if tombstone_identity == original_identity else 1
        if (
            destination.is_symlink()
            or not stat.S_ISREG(destination_stat.st_mode)
            or destination_stat.st_nlink != expected_links
        ):
            raise ConfigurationError(
                f"Original inode link topology changed for {destination}."
            )
    if tombstone_identity == original_identity:
        tombstone_stat = tombstone.stat(follow_symlinks=False)
        expected_links = 2 if destination_identity == original_identity else 1
        if (
            tombstone.is_symlink()
            or not stat.S_ISREG(tombstone_stat.st_mode)
            or tombstone_stat.st_nlink != expected_links
        ):
            raise ConfigurationError(
                f"Original tombstone link topology changed for {destination}."
            )
        if destination_identity == original_identity:
            tombstone.unlink()
        elif destination_identity in {None, installed_identity}:
            os.replace(tombstone, destination)
        else:
            raise ConfigurationError(
                f"Cannot restore {destination}; the destination is occupied."
            )
    elif destination_identity != original_identity:
        raise ConfigurationError(
            f"Original inode is unavailable for {destination}."
        )
    destination_stat = destination.stat(follow_symlinks=False)
    if (
        destination.is_symlink()
        or not stat.S_ISREG(destination_stat.st_mode)
        or (destination_stat.st_dev, destination_stat.st_ino) != original_identity
        or destination_stat.st_nlink != 1
    ):
        raise ConfigurationError(f"Original inode restoration failed for {destination}.")


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":  # Windows has no portable directory-fsync interface.
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _transaction_directory_lock(root: Path):
    """Serialize configurator recovery and publication without a lock file."""
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
                    "Another Codex-Orchestration configuration transaction is active; "
                    "wait for it to finish and retry."
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
            None, False, f"Local\\CodexOrchestration-{name_hash}"
        )
        if not mutex:
            raise ConfigurationError("Could not create the Windows transaction mutex.")
        wait_result = kernel32.WaitForSingleObject(mutex, 0)
        if wait_result not in {0x00000000, 0x00000080}:
            kernel32.CloseHandle(mutex)
            raise ConfigurationError(
                "Another Codex-Orchestration configuration transaction is active; "
                "wait for it to finish and retry."
            )
        try:
            yield
        finally:
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
        return
    raise ConfigurationError(f"Unsupported transaction-locking platform: {os.name}.")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if path.is_symlink():
        raise ConfigurationError(f"Refusing symlinked recovery path {path}.")
    if not path.exists():
        return None
    if not path.is_file():
        raise ConfigurationError(f"Recovery path is not a regular file: {path}.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _identity_json(identity: tuple[int, int] | None) -> list[int] | None:
    return list(identity) if identity is not None else None


def _identity_from_json(value: Any, label: str) -> tuple[int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, int) or item < 0 for item in value)
    ):
        raise ConfigurationError(f"Invalid transaction identity for {label}.")
    return value[0], value[1]


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ConfigurationError(
            f"Transaction path {path} is outside transaction root {root}."
        ) from exc


def _path_from_journal(root: Path, value: Any, label: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigurationError(f"Invalid transaction path for {label}.")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ConfigurationError(f"Unsafe transaction path for {label}: {value!r}.")
    result = root / relative
    _relative_to_root(result, root)
    ensure_safe_managed_path(result, root)
    return result


def _journal_paths(root: Path) -> tuple[Path, Path]:
    journal = root / TRANSACTION_JOURNAL
    return journal, journal.with_name(journal.name + ".new")


def _write_transaction_journal(root: Path, payload: dict[str, Any]) -> None:
    journal, pending = _journal_paths(root)
    if journal.is_symlink():
        raise ConfigurationError(f"Refusing symlinked transaction journal {journal}.")
    if pending.exists() or pending.is_symlink():
        raise ConfigurationError(
            f"Pending transaction-journal file already exists: {pending}."
        )
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    staged = stage_text(journal, serialized, 0o600, pending)
    try:
        os.replace(staged, journal)
        _fsync_directory(root)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _remove_transaction_journal(root: Path) -> None:
    journal, pending = _journal_paths(root)
    for candidate in (pending, journal):
        if candidate.is_symlink():
            raise ConfigurationError(
                f"Refusing symlinked transaction-journal path {candidate}."
            )
        candidate.unlink(missing_ok=True)
    _fsync_directory(root)


def _durable_transaction_phase(root: Path, transaction_id: str) -> str:
    """Read the durable journal phase before deciding whether rollback is legal."""
    journal, _ = _journal_paths(root)
    if journal.is_symlink() or not journal.is_file():
        raise ConfigurationError(
            f"Cannot determine the durable transaction phase from {journal}."
        )
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot determine the durable transaction phase: {exc}"
        ) from exc
    durable_id, phase, _ = _validate_transaction_payload(root, payload)
    if durable_id != transaction_id:
        raise ConfigurationError(
            "The durable transaction journal belongs to a different transaction."
        )
    return phase


def _validate_transaction_payload(
    root: Path,
    payload: Any,
) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or set(payload) != {
        "marker",
        "transaction_id",
        "phase",
        "entries",
    }:
        raise ConfigurationError("Transaction journal has an unknown schema.")
    if payload.get("marker") != TRANSACTION_MARKER:
        raise ConfigurationError("Transaction journal ownership marker is invalid.")
    transaction_id = payload.get("transaction_id")
    if not isinstance(transaction_id, str) or not TRANSACTION_ID_RE.fullmatch(
        transaction_id
    ):
        raise ConfigurationError("Transaction journal ID is invalid.")
    phase = payload.get("phase")
    if phase not in {"preparing", "prepared", "committed"}:
        raise ConfigurationError("Transaction journal phase is invalid.")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConfigurationError("Transaction journal entries are invalid.")

    required = {
        "destination",
        "existed",
        "delete",
        "old_sha256",
        "new_sha256",
        "original_identity",
        "staged_new",
        "staged_new_identity",
        "staged_old",
        "staged_old_identity",
        "staged_old_metadata_sha256",
        "tombstone",
        "tombstone_placeholder_identity",
        "installed_identity",
        "installed_metadata_sha256",
    }
    seen_destinations: set[Path] = set()
    seen_temporaries: set[Path] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != required:
            raise ConfigurationError(
                f"Transaction journal entry {index} has an unknown schema."
            )
        destination = _path_from_journal(
            root, entry["destination"], f"entry {index} destination"
        )
        if (
            destination is None
            or destination in seen_destinations
            or destination in _journal_paths(root)
        ):
            raise ConfigurationError("Transaction destinations are missing or duplicated.")
        seen_destinations.add(destination)
        if not isinstance(entry["existed"], bool) or not isinstance(
            entry["delete"], bool
        ):
            raise ConfigurationError(f"Transaction flags are invalid at entry {index}.")
        for key in ("old_sha256", "new_sha256"):
            value = entry[key]
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ConfigurationError(
                    f"Transaction digest {key} is invalid at entry {index}."
                )
        metadata_digests: dict[str, str | None] = {}
        for key in (
            "installed_metadata_sha256",
            "staged_old_metadata_sha256",
        ):
            value = entry[key]
            if value is not None and (
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
            ):
                raise ConfigurationError(
                    f"Transaction metadata digest {key} is invalid at entry {index}."
                )
            metadata_digests[key] = value
        installed_metadata = metadata_digests["installed_metadata_sha256"]
        staged_old_metadata = metadata_digests["staged_old_metadata_sha256"]
        for key in (
            "original_identity",
            "staged_new_identity",
            "staged_old_identity",
            "tombstone_placeholder_identity",
            "installed_identity",
        ):
            _identity_from_json(entry[key], f"entry {index} {key}")
        temporary_base = f".codex-orchestration-txn-{transaction_id}-{index}"
        expected_temporaries = {
            "staged_new": (
                destination.parent / f"{temporary_base}-new"
                if not entry["delete"]
                else None
            ),
            "staged_old": (
                destination.parent / f"{temporary_base}-old"
                if entry["existed"]
                else None
            ),
            "tombstone": (
                destination.parent / f"{temporary_base}-tombstone"
                if entry["existed"]
                else None
            ),
        }
        for key, expected_temporary in expected_temporaries.items():
            candidate = _path_from_journal(root, entry[key], f"entry {index} {key}")
            if candidate != expected_temporary or (
                candidate is not None
                and (candidate in seen_temporaries or candidate in seen_destinations)
            ):
                raise ConfigurationError(
                    f"Transaction temporary path is invalid for {key}: {candidate}."
                )
            if candidate is not None:
                seen_temporaries.add(candidate)
        original = _identity_from_json(
            entry["original_identity"], f"entry {index} original"
        )
        installed = _identity_from_json(
            entry["installed_identity"], f"entry {index} installed"
        )
        staged_new_identity = _identity_from_json(
            entry["staged_new_identity"], f"entry {index} staged new"
        )
        staged_old_identity = _identity_from_json(
            entry["staged_old_identity"], f"entry {index} staged old"
        )
        tombstone_placeholder_identity = _identity_from_json(
            entry["tombstone_placeholder_identity"],
            f"entry {index} tombstone placeholder",
        )
        if entry["delete"]:
            if (
                not entry["existed"]
                or entry["new_sha256"] != _sha256_text("")
                or entry["staged_new"] is not None
                or staged_new_identity is not None
                or installed is not None
                or installed_metadata is not None
            ):
                raise ConfigurationError(f"Deletion entry {index} is inconsistent.")
        elif entry["staged_new"] is None:
            raise ConfigurationError(f"Write entry {index} lacks staged new data.")
        if entry["existed"]:
            if (
                original is None
                or entry["staged_old"] is None
                or entry["tombstone"] is None
            ):
                raise ConfigurationError(f"Existing entry {index} is inconsistent.")
        elif any(
            value is not None
            for value in (
                original,
                entry["staged_old"],
                entry["tombstone"],
                staged_old_identity,
                staged_old_metadata,
                tombstone_placeholder_identity,
            )
        ):
            raise ConfigurationError(f"New entry {index} is inconsistent.")
        if not entry["existed"] and entry["old_sha256"] != _sha256_text(""):
            raise ConfigurationError(f"New entry {index} has unexpected old data.")
        if phase == "preparing" and any(
            value is not None
            for value in (
                staged_new_identity,
                staged_old_identity,
                tombstone_placeholder_identity,
                installed,
                installed_metadata,
                staged_old_metadata,
            )
        ):
            raise ConfigurationError(
                f"Preparing entry {index} contains publication state."
            )
        if phase in {"prepared", "committed"}:
            if not entry["delete"] and (
                installed is None or staged_new_identity != installed
            ):
                raise ConfigurationError(
                    f"Prepared write entry {index} lacks an installed identity."
                )
            if not entry["delete"] and installed_metadata is None:
                raise ConfigurationError(
                    f"Prepared write entry {index} lacks a metadata digest."
                )
            if entry["existed"] and staged_old_identity is None:
                raise ConfigurationError(
                    f"Prepared existing entry {index} lacks a recovery identity."
                )
            if entry["existed"] and staged_old_metadata is None:
                raise ConfigurationError(
                    f"Prepared existing entry {index} lacks a recovery metadata digest."
                )
            if entry["existed"] and tombstone_placeholder_identity is None:
                raise ConfigurationError(
                    f"Prepared existing entry {index} lacks a tombstone identity."
                )
    if seen_destinations & seen_temporaries:
        raise ConfigurationError("Transaction destinations overlap temporary paths.")
    return transaction_id, phase, entries


def _cleanup_journal_temporaries(
    root: Path,
    transaction_id: str,
    entries: list[dict[str, Any]],
    allow_unrecorded: bool = False,
) -> None:
    touched: set[Path] = set()
    prefix = f".codex-orchestration-txn-{transaction_id}-"
    for entry in entries:
        for key in ("staged_new", "staged_old", "tombstone"):
            candidate = _path_from_journal(root, entry[key], key)
            if candidate is None or not candidate.exists():
                if candidate is not None and candidate.is_symlink():
                    raise ConfigurationError(
                        f"Refusing symlinked transaction temporary {candidate}."
                    )
                continue
            if candidate.is_symlink() or not candidate.name.startswith(prefix):
                raise ConfigurationError(
                    f"Refusing unsafe transaction temporary {candidate}."
                )
            identity = _path_identity(candidate)
            allowed_for_path = {
                value
                for value in (
                    _identity_from_json(
                        entry[
                            "staged_new_identity"
                            if key == "staged_new"
                            else "staged_old_identity"
                            if key == "staged_old"
                            else "tombstone_placeholder_identity"
                        ],
                        key,
                    ),
                    (
                        _identity_from_json(entry["original_identity"], key)
                        if key == "tombstone"
                        else None
                    ),
                )
                if value is not None
            }
            if not allow_unrecorded and identity not in allowed_for_path:
                raise ConfigurationError(
                    f"Transaction temporary identity changed: {candidate}."
                )
            candidate.unlink()
            touched.add(candidate.parent)
    for directory in sorted(touched):
        _fsync_directory(directory)


def recover_incomplete_transaction(root: Path) -> bool:
    """Recover a prior interrupted transaction to old or fully committed state."""
    root = root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ConfigurationError(f"Unsafe transaction recovery root {root}.")
    journal, pending = _journal_paths(root)
    if not journal.exists() and not journal.is_symlink():
        if pending.is_symlink():
            raise ConfigurationError(
                f"Refusing symlinked pending transaction journal {pending}."
            )
        if pending.exists():
            # Publication cannot start before the journal rename. A lone pending
            # file is therefore safe to discard after an interrupted journal write.
            pending.unlink()
            _fsync_directory(root)
        return False
    if journal.is_symlink() or not journal.is_file():
        raise ConfigurationError(f"Unsafe transaction journal {journal}.")
    if pending.is_symlink():
        raise ConfigurationError(f"Unsafe pending transaction journal {pending}.")
    if pending.exists():
        pending.unlink()
        _fsync_directory(root)
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Transaction journal is unreadable: {exc}") from exc
    transaction_id, phase, entries = _validate_transaction_payload(root, payload)

    if phase == "preparing":
        for index, entry in enumerate(entries):
            destination = _path_from_journal(
                root, entry["destination"], f"entry {index} destination"
            )
            original = _identity_from_json(
                entry["original_identity"], f"entry {index} original"
            )
            if entry["existed"]:
                if (
                    destination is None
                    or _path_identity(destination) != original
                    or _sha256_file(destination) != entry["old_sha256"]
                ):
                    raise ConfigurationError(
                        f"Preparing transaction destination changed: {destination}."
                    )
            elif destination is None or destination.exists() or destination.is_symlink():
                raise ConfigurationError(
                    f"Preparing transaction destination appeared: {destination}."
                )
    elif phase == "prepared":
        recovery_directories: set[Path] = set()
        for index, entry in reversed(list(enumerate(entries))):
            destination = _path_from_journal(
                root, entry["destination"], f"entry {index} destination"
            )
            if destination is None:
                raise ConfigurationError("Transaction destination is missing.")
            installed = _identity_from_json(
                entry["installed_identity"], f"entry {index} installed"
            )
            destination_identity = _path_identity(destination)
            if destination_identity == installed and installed is not None:
                if (
                    _sha256_file(destination) != entry["new_sha256"]
                    or _metadata_digest(destination)
                    != entry["installed_metadata_sha256"]
                ):
                    raise ConfigurationError(
                        f"Installed recovery destination was modified: {destination}."
                    )
            if entry["existed"]:
                original = _identity_from_json(
                    entry["original_identity"], f"entry {index} original"
                )
                staged_old_identity = _identity_from_json(
                    entry["staged_old_identity"],
                    f"entry {index} staged old identity",
                )
                if original is None:
                    raise ConfigurationError("Existing transaction entry lacks identity.")
                if destination_identity == staged_old_identity:
                    destination_stat = destination.stat(follow_symlinks=False)
                    tombstone = _path_from_journal(
                        root, entry["tombstone"], f"entry {index} tombstone"
                    )
                    if (
                        destination.is_symlink()
                        or not stat.S_ISREG(destination_stat.st_mode)
                        or destination_stat.st_nlink != 1
                        or _sha256_file(destination) != entry["old_sha256"]
                        or _metadata_digest(destination)
                        != entry["staged_old_metadata_sha256"]
                        or (
                            tombstone is not None
                            and (tombstone.exists() or tombstone.is_symlink())
                        )
                    ):
                        raise ConfigurationError(
                            f"Recovered staged destination was modified: {destination}."
                        )
                    recovery_directories.add(destination.parent)
                    continue
                if (
                    destination_identity == original
                    and _sha256_file(destination) != entry["old_sha256"]
                ):
                    raise ConfigurationError(
                        f"Original recovery destination was modified: {destination}."
                    )
                if destination_identity is None and not entry["delete"]:
                    raise ConfigurationError(
                        f"Update recovery destination disappeared: {destination}."
                    )
                tombstone = _path_from_journal(
                    root, entry["tombstone"], f"entry {index} tombstone"
                )
                used_staged_backup = False
                try:
                    _restore_original_from_tombstone(
                        destination, tombstone, original, installed
                    )
                except ConfigurationError as tombstone_error:
                    staged_old = _path_from_journal(
                        root, entry["staged_old"], f"entry {index} staged old"
                    )
                    staged_old_stat = (
                        staged_old.stat(follow_symlinks=False)
                        if staged_old is not None and staged_old.exists()
                        else None
                    )
                    if (
                        (
                            tombstone is not None
                            and (tombstone.exists() or tombstone.is_symlink())
                        )
                        or (
                            entry["delete"]
                            and destination_identity is not None
                        )
                        or (
                            not entry["delete"]
                            and (
                                installed is None
                                or destination_identity != installed
                            )
                        )
                    ):
                        raise tombstone_error
                    if (
                        staged_old is None
                        or staged_old.is_symlink()
                        or staged_old_stat is None
                        or not stat.S_ISREG(staged_old_stat.st_mode)
                        or staged_old_stat.st_nlink != 1
                        or _path_identity(staged_old) != staged_old_identity
                    ):
                        raise ConfigurationError(
                            f"Staged recovery copy is unsafe: {staged_old}."
                        ) from tombstone_error
                    if (
                        _sha256_file(staged_old) != entry["old_sha256"]
                        or _metadata_digest(staged_old)
                        != entry["staged_old_metadata_sha256"]
                    ):
                        raise ConfigurationError(
                            f"Staged recovery copy was modified: {staged_old}."
                        ) from tombstone_error
                    os.replace(staged_old, destination)
                    used_staged_backup = True
                destination_stat = destination.stat(follow_symlinks=False)
                if (
                    destination.is_symlink()
                    or not stat.S_ISREG(destination_stat.st_mode)
                    or destination_stat.st_nlink != 1
                    or _sha256_file(destination) != entry["old_sha256"]
                    or (
                        used_staged_backup
                        and _metadata_digest(destination)
                        != entry["staged_old_metadata_sha256"]
                    )
                ):
                    raise ConfigurationError(
                        f"Recovered destination does not match: {destination}."
                    )
                recovery_directories.add(destination.parent)
            else:
                if destination_identity == installed:
                    destination.unlink()
                    recovery_directories.add(destination.parent)
                elif destination.exists() or destination.is_symlink():
                    raise ConfigurationError(
                        f"Refusing to remove changed recovery destination {destination}."
                    )
        for directory in sorted(recovery_directories):
            _fsync_directory(directory)
    else:  # committed
        # The committed marker is written only after every destination and parent
        # directory was verified and fsynced. Cleanup must not overwrite or reject
        # legitimate user changes that happened after that commit point.
        pass

    _cleanup_journal_temporaries(
        root,
        transaction_id,
        entries,
        allow_unrecorded=phase == "preparing",
    )
    _remove_transaction_journal(root)
    print(
        "Recovered an interrupted Codex-Orchestration configuration transaction.",
        file=sys.stderr,
    )
    return True


def apply_changes_transactionally(
    changes: list[tuple[Path, str, str]],
    transaction_root: Path | None = None,
) -> None:
    active = [change for change in changes if change[1] != change[2]]
    if not active:
        return
    paths = [path.expanduser().absolute() for path, _, _ in active]
    if len(paths) != len(set(paths)):
        raise ConfigurationError("Transactional change set contains a duplicate path")
    root = (
        transaction_root.expanduser().absolute()
        if transaction_root is not None
        else Path(os.path.commonpath([str(path.parent) for path in paths]))
    )
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ConfigurationError(f"Refusing unsafe transaction root {root}.")
    with _transaction_directory_lock(root):
        _apply_changes_transactionally_locked(changes, transaction_root=root)


def _apply_changes_transactionally_locked(
    changes: list[tuple[Path, str, str]],
    transaction_root: Path | None = None,
) -> None:
    """Apply a recoverable multi-file transaction without truncating live files."""
    active = [change for change in changes if change[1] != change[2]]
    if not active:
        return
    paths = [path.expanduser().absolute() for path, _, _ in active]
    if len(paths) != len(set(paths)):
        raise ConfigurationError("Transactional change set contains a duplicate path")
    root = (
        transaction_root.expanduser().absolute()
        if transaction_root is not None
        else Path(os.path.commonpath([str(path.parent) for path in paths]))
    )
    for path in paths:
        _relative_to_root(path, root)

    prepared: list[dict[str, Any]] = []
    staged_paths: set[Path] = set()
    created_dirs: set[Path] = set()
    attempted: list[dict[str, Any]] = []
    committed = False
    preserve_staged = False
    journal_active = False
    journal_phase: str | None = None
    transaction_id = secrets.token_hex(12)
    payload: dict[str, Any] | None = None

    try:
        for directory in [root, *(path.parent for path in paths)]:
            missing: list[Path] = []
            cursor = directory
            while not cursor.exists():
                missing.append(cursor)
                cursor = cursor.parent
            directory.mkdir(parents=True, exist_ok=True)
            created_dirs.update(missing)
            if directory.is_symlink() or not directory.is_dir():
                raise ConfigurationError(
                    f"Refusing unsafe transaction directory {directory}."
                )
        recover_incomplete_transaction(root)

        for index, ((original_path, expected, new), path) in enumerate(
            zip(active, paths)
        ):
            if path.is_symlink():
                raise ConfigurationError(f"Refusing to replace symlinked path {path}.")
            existed = path.exists()
            current = read_text(path)
            if current != expected:
                raise ConfigurationError(
                    f"Configuration changed while preparing the update: {path}. "
                    "No files were modified; preview again."
                )
            stat_result = path.stat() if existed else None
            if stat_result is not None and stat_result.st_nlink != 1:
                raise ConfigurationError(
                    f"Refusing hard-linked managed configuration file {path}."
                )
            prefix = f".codex-orchestration-txn-{transaction_id}-{index}"
            item = {
                "path": path,
                "old": expected,
                "new": new,
                "existed": existed,
                "mode": stat.S_IMODE(stat_result.st_mode) if existed else 0o600,
                "identity": (
                    (stat_result.st_dev, stat_result.st_ino)
                    if stat_result is not None
                    else None
                ),
                "source_metadata": None,
                "staged_new": path.parent / f"{prefix}-new" if new else None,
                "staged_old": path.parent / f"{prefix}-old" if existed else None,
                "staged_new_identity": None,
                "staged_old_identity": None,
                "staged_old_metadata_sha256": None,
                "tombstone": (
                    path.parent / f"{prefix}-tombstone" if existed else None
                ),
                "tombstone_placeholder_identity": None,
                "installed_identity": None,
                "installed_metadata_sha256": None,
            }
            for candidate in (
                item["staged_new"],
                item["staged_old"],
                item["tombstone"],
            ):
                if isinstance(candidate, Path) and (
                    candidate.exists() or candidate.is_symlink()
                ):
                    raise ConfigurationError(
                        f"Transaction temporary already exists: {candidate}."
                    )
            prepared.append(item)

        payload = {
            "marker": TRANSACTION_MARKER,
            "transaction_id": transaction_id,
            "phase": "preparing",
            "entries": [],
        }
        for item in prepared:
            payload["entries"].append(
                {
                    "destination": _relative_to_root(item["path"], root),
                    "existed": item["existed"],
                    "delete": not bool(item["new"]),
                    "old_sha256": _sha256_text(item["old"]),
                    "new_sha256": _sha256_text(item["new"]),
                    "original_identity": _identity_json(item["identity"]),
                    "staged_new": (
                        _relative_to_root(item["staged_new"], root)
                        if isinstance(item["staged_new"], Path)
                        else None
                    ),
                    "staged_new_identity": None,
                    "staged_old": (
                        _relative_to_root(item["staged_old"], root)
                        if isinstance(item["staged_old"], Path)
                        else None
                    ),
                    "staged_old_identity": None,
                    "staged_old_metadata_sha256": None,
                    "tombstone": (
                        _relative_to_root(item["tombstone"], root)
                        if isinstance(item["tombstone"], Path)
                        else None
                    ),
                    "tombstone_placeholder_identity": None,
                    "installed_identity": None,
                    "installed_metadata_sha256": None,
                }
            )
        _write_transaction_journal(root, payload)
        journal_active = True
        journal_phase = "preparing"

        for item in prepared:
            if isinstance(item["staged_new"], Path):
                item["staged_new"] = (
                    stage_existing_file(
                        item["path"], item["new"], item["staged_new"]
                    )
                    if item["existed"]
                    else stage_text(
                        item["path"],
                        item["new"],
                        item["mode"],
                        item["staged_new"],
                    )
                )
                staged_paths.add(item["staged_new"])
                item["staged_new_identity"] = _path_identity(item["staged_new"])
                item["installed_identity"] = item["staged_new_identity"]
                item["installed_metadata_sha256"] = _metadata_digest(
                    item["staged_new"]
                )
            if isinstance(item["staged_old"], Path):
                item["staged_old"] = stage_existing_file(
                    item["path"], item["old"], item["staged_old"]
                )
                staged_paths.add(item["staged_old"])
                item["staged_old_identity"] = _path_identity(item["staged_old"])
                item["staged_old_metadata_sha256"] = _metadata_digest(
                    item["staged_old"]
                )
            if isinstance(item["tombstone"], Path):
                item["tombstone"] = stage_text(
                    item["path"], "", item["mode"], item["tombstone"]
                )
                staged_paths.add(item["tombstone"])
                item["tombstone_placeholder_identity"] = _path_identity(
                    item["tombstone"]
                )
            if item["existed"]:
                item["source_metadata"] = _metadata_signature(item["path"])
                for candidate in (item["staged_new"], item["staged_old"]):
                    if isinstance(candidate, Path) and _metadata_signature(
                        candidate
                    ) != item["source_metadata"]:
                        raise ConfigurationError(
                            f"Source metadata changed while staging {item['path']}."
                        )
                if (
                    _path_identity(item["path"]) != item["identity"]
                    or _sha256_file(item["path"]) != _sha256_text(item["old"])
                ):
                    raise ConfigurationError(
                        f"Source changed while staging {item['path']}."
                    )

        for entry, item in zip(payload["entries"], prepared):
            entry["staged_new_identity"] = _identity_json(
                item["staged_new_identity"]
            )
            entry["staged_old_identity"] = _identity_json(
                item["staged_old_identity"]
            )
            entry["staged_old_metadata_sha256"] = item[
                "staged_old_metadata_sha256"
            ]
            entry["tombstone_placeholder_identity"] = _identity_json(
                item["tombstone_placeholder_identity"]
            )
            entry["installed_identity"] = _identity_json(item["installed_identity"])
            entry["installed_metadata_sha256"] = item[
                "installed_metadata_sha256"
            ]
        for directory in sorted({path.parent for path in staged_paths}):
            _fsync_directory(directory)
        payload["phase"] = "prepared"
        _write_transaction_journal(root, payload)
        journal_phase = "prepared"

        for item in prepared:
            attempted.append(item)
            if item["path"].is_symlink() or read_text(item["path"]) != item["old"]:
                raise ConfigurationError(f"Destination changed during apply: {item['path']}.")
            if item["existed"]:
                current = item["path"].stat(follow_symlinks=False)
                if (
                    (current.st_dev, current.st_ino) != item["identity"]
                    or current.st_nlink != 1
                    or _metadata_signature(item["path"])
                    != item["source_metadata"]
                ):
                    raise ConfigurationError(
                        f"Destination metadata changed during apply: {item['path']}."
                    )
            if item["new"]:
                if item["existed"]:
                    _link_original_tombstone(
                        item["path"],
                        item["tombstone"],
                        item["identity"],
                        item["tombstone_placeholder_identity"],
                    )
                    _replace_staged_atomically(
                        item["staged_new"],
                        item["path"],
                        item["tombstone"],
                        item["staged_new_identity"],
                        item["identity"],
                        item["source_metadata"],
                        _sha256_text(item["old"]),
                    )
                else:
                    os.link(
                        item["staged_new"],
                        item["path"],
                        follow_symlinks=False,
                    )
                    _fsync_directory(item["path"].parent)
                    if _path_identity(item["path"]) != item["staged_new_identity"]:
                        raise ConfigurationError(
                            f"New destination identity could not be verified: {item['path']}."
                        )
                    item["staged_new"].unlink()
                    _fsync_directory(item["path"].parent)
                staged_paths.discard(item["staged_new"])
            else:
                _move_original_to_tombstone(
                    item["path"],
                    item["tombstone"],
                    item["identity"],
                    item["tombstone_placeholder_identity"],
                    item["source_metadata"],
                    _sha256_text(item["old"]),
                )

        for item in prepared:
            if item["new"]:
                current = item["path"].stat(follow_symlinks=False)
                if item["path"].is_symlink() or (
                    (current.st_dev, current.st_ino) != item["installed_identity"]
                    or current.st_nlink != 1
                ):
                    raise ConfigurationError(
                        f"Final destination identity check failed for {item['path']}."
                    )
                if item["existed"] and _metadata_signature(
                    item["path"]
                ) != item["source_metadata"]:
                    raise ConfigurationError(
                        f"Final metadata check failed for {item['path']}."
                    )
            elif item["path"].exists() or item["path"].is_symlink():
                raise ConfigurationError(
                    f"Deleted destination reappeared during apply: {item['path']}."
                )
            if read_text(item["path"]) != item["new"]:
                raise ConfigurationError(f"Final readback failed for {item['path']}.")
            _fsync_directory(item["path"].parent)

        payload["phase"] = "committed"
        _write_transaction_journal(root, payload)
        journal_phase = "committed"
        committed = True

        cleanup_errors: list[str] = []
        for item in prepared:
            tombstone = item.get("tombstone")
            if not isinstance(tombstone, Path):
                continue
            try:
                if _path_identity(tombstone) != item["identity"]:
                    raise ConfigurationError(
                        f"Committed tombstone identity changed: {tombstone}."
                    )
                tombstone.unlink()
                _fsync_directory(tombstone.parent)
                staged_paths.discard(tombstone)
            except (ConfigurationError, OSError) as cleanup_exc:
                cleanup_errors.append(f"{tombstone}: {cleanup_exc}")
        if cleanup_errors:
            preserve_staged = True
            raise ConfigurationError(
                "Configuration changes were applied, but recovery cleanup failed; "
                "the transaction journal was kept for the next run: "
                + "; ".join(cleanup_errors)
            )
        for staged in list(staged_paths):
            staged.unlink(missing_ok=True)
            _fsync_directory(staged.parent)
            staged_paths.discard(staged)
        _remove_transaction_journal(root)
        journal_active = False
    except BaseException as exc:
        if not committed and journal_active:
            try:
                journal_phase = _durable_transaction_phase(root, transaction_id)
            except ConfigurationError as phase_exc:
                preserve_staged = True
                raise ConfigurationError(
                    "Transactional apply stopped, but the durable commit phase could "
                    "not be verified. No rollback or cleanup was attempted; keep the "
                    f"journal for recovery: {phase_exc}"
                ) from exc
            if journal_phase == "committed":
                committed = True
        if committed:
            preserve_staged = journal_active
            raise
        rollback_errors: list[str] = []
        if journal_phase == "prepared":
            for item in reversed(attempted):
                try:
                    destination_identity = _path_identity(item["path"])
                    if (
                        item["installed_identity"] is not None
                        and destination_identity == item["installed_identity"]
                        and (
                            _sha256_file(item["path"])
                            != _sha256_text(item["new"])
                            or _metadata_digest(item["path"])
                            != item["installed_metadata_sha256"]
                        )
                    ):
                        raise ConfigurationError(
                            f"Installed destination changed during rollback: {item['path']}."
                        )
                    if item["existed"]:
                        _restore_original_from_tombstone(
                            item["path"],
                            item["tombstone"],
                            item["identity"],
                            item["installed_identity"],
                        )
                    else:
                        if destination_identity == item["installed_identity"]:
                            item["path"].unlink()
                        elif item["path"].exists() or item["path"].is_symlink():
                            raise ConfigurationError(
                                "Refusing to remove an unrecognized file at "
                                f"{item['path']}."
                            )
                    _fsync_directory(item["path"].parent)
                except BaseException as rollback_exc:
                    rollback_errors.append(f"{item['path']}: {rollback_exc}")
        if rollback_errors:
            preserve_staged = True
            detail = "; ".join(rollback_errors)
            journal, _ = _journal_paths(root)
            raise ConfigurationError(
                f"Transactional apply failed ({exc}) and rollback was incomplete: "
                f"{detail}; recovery journal kept at {journal}"
            ) from exc
        try:
            for staged in list(staged_paths):
                staged.unlink(missing_ok=True)
                _fsync_directory(staged.parent)
                staged_paths.discard(staged)
            if journal_active:
                _remove_transaction_journal(root)
                journal_active = False
            for directory in sorted(
                created_dirs,
                key=lambda value: len(value.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        except BaseException as cleanup_exc:
            preserve_staged = journal_active
            raise ConfigurationError(
                f"Rollback restored destinations but cleanup failed: {cleanup_exc}"
            ) from exc
        if isinstance(exc, (ConfigurationError, OSError)):
            raise ConfigurationError(
                f"Transactional apply failed; committed files were restored: {exc}"
            ) from exc
        raise
    finally:
        if not preserve_staged:
            for staged in list(staged_paths):
                staged.unlink(missing_ok=True)


def backup_change(path: Path, content: str, base: Path) -> tuple[Path, str, str]:
    backup = path.with_name(path.name + ".bak.codex-orchestration")
    ensure_safe_managed_path(backup, base)
    existing = read_text(backup)
    if backup.exists() and existing != content:
        raise ConfigurationError(
            f"Backup destination {backup} already contains different data. Move it "
            "before retrying migration."
        )
    return backup, existing, content


def routing_tuple(parsed: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        parsed.get("model"),
        parsed.get("model_reasoning_effort"),
        parsed.get("model_provider"),
    )


def main() -> int:
    args = parse_args()
    try:
        for label, value in (
            ("--executor-model", args.executor_model),
            ("--executor-effort", args.executor_effort),
            ("--executor-provider", args.executor_provider),
            ("--advisor-model", args.advisor_model),
            ("--advisor-effort", args.advisor_effort),
            ("--advisor-provider", args.advisor_provider),
            ("--codex-bin", args.codex_bin),
        ):
            if value is not None and (not value or value != value.strip()):
                raise ConfigurationError(
                    f"{label} must be a non-empty value without surrounding whitespace."
                )
        if args.remove_saved_roles and (
            args.executor_provider
            or args.executor_effort
            or args.advisor_model
            or args.remove_advisor
            or args.advisor_effort
            or args.advisor_provider
            or args.migrate_legacy
        ):
            raise ConfigurationError(
                "--remove-saved-roles cannot be combined with model, advisor, "
                "provider, or legacy-migration options."
            )
        if args.scope == "project" and args.codex_home is not None:
            raise ConfigurationError(
                "--codex-home is personal-scope only. In project scope, set the "
                "actual CODEX_HOME environment instead of bypassing collision checks."
            )
        if args.personal_route_names and args.scope != "personal":
            raise ConfigurationError(
                "--personal-route-names requires --scope personal."
            )
        if args.personal_route_names and args.migrate_legacy:
            raise ConfigurationError(
                "--personal-route-names cannot be combined with --migrate-legacy; "
                "migrate fixed legacy roles separately before creating the scoped route."
            )
        if args.remove_advisor and (args.advisor_effort or args.advisor_provider):
            raise ConfigurationError(
                "--remove-advisor cannot be combined with advisor effort or provider flags."
            )
        if not args.advisor_model and (args.advisor_effort or args.advisor_provider):
            raise ConfigurationError(
                "--advisor-effort and --advisor-provider require --advisor-model."
            )
        advisor_action = (
            "remove_all"
            if args.remove_saved_roles
            else "configure"
            if args.advisor_model
            else "remove"
            if args.remove_advisor
            else "preserve"
        )
        executor_effort = args.executor_effort or "auto"
        advisor_effort = args.advisor_effort or "auto"

        if args.scope == "project" and (args.executor_provider or args.advisor_provider):
            raise ConfigurationError(
                "Project-scoped custom agents cannot select machine-local model providers. "
                "Omit provider flags or use explicitly approved personal scope."
            )

        project_root = args.root.expanduser().resolve()
        if not project_root.is_dir():
            raise ConfigurationError(
                f"Project root does not exist or is not a directory: {project_root}"
            )
        project_base = project_root / ".codex"
        personal_base = (
            args.codex_home
            or (Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else None)
            or Path.home() / ".codex"
        ).expanduser().absolute()
        select_agent_identities(personal_base, args.personal_route_names)
        base = project_base if args.scope == "project" else personal_base

        config_path = base / "config.toml"
        agents_dir = base / "agents"
        executor_path = agents_dir / EXECUTOR_FILENAME
        advisor_path = agents_dir / ADVISOR_FILENAME
        legacy_executor_path = agents_dir / LEGACY_EXECUTOR_LAYER
        legacy_advisor_path = agents_dir / LEGACY_ADVISOR_LAYER
        managed_paths = {
            executor_path: "executor",
            advisor_path: "advisor",
        }
        if args.personal_route_names:
            print(f"Executor agent name: {EXECUTOR_NAME}")
            print(f"Advisor agent name: {ADVISOR_NAME}")
        for path in (
            config_path,
            agents_dir,
            executor_path,
            advisor_path,
            legacy_executor_path,
            legacy_advisor_path,
        ):
            ensure_safe_managed_path(path, base)

        journal, pending_journal = _journal_paths(base)
        if any(
            candidate.exists() or candidate.is_symlink()
            for candidate in (journal, pending_journal)
        ):
            if not args.apply:
                raise ConfigurationError(
                    "An interrupted configuration transaction was detected. Re-run "
                    "with --apply to recover it before requesting a new preview."
                )
            with _transaction_directory_lock(base):
                recover_incomplete_transaction(base)

        old_executor = read_text(executor_path)
        managed_executor = validate_managed_agent(old_executor, executor_path, "executor")
        if executor_path.exists() and not managed_executor:
            raise ConfigurationError(
                f"Refusing to replace unmanaged custom agent {executor_path}."
            )
        old_advisor = read_text(advisor_path)
        managed_advisor = False
        if advisor_path.exists():
            managed_advisor = validate_managed_agent(old_advisor, advisor_path, "advisor")
            if not managed_advisor:
                action_label = "remove" if advisor_action == "remove_all" else advisor_action
                raise ConfigurationError(
                    f"Refusing to {action_label} unmanaged custom agent {advisor_path}."
                )

        if args.remove_saved_roles:
            changes = [
                (executor_path, old_executor, ""),
                (advisor_path, old_advisor, ""),
            ]
            changed = False
            for path, old, new in changes:
                diff = unified_diff(path, old, new)
                if diff:
                    changed = True
                    print(diff, end="" if diff.endswith("\n") else "\n")
            if not changed:
                print("No managed saved roles exist in this scope.")
            if not args.apply:
                print("Dry run only. Re-run with --apply after reviewing the diff.")
                return 0
            apply_changes_transactionally(changes, transaction_root=base)
            print(
                "Managed Codex-Orchestration roles were removed. Start a new Codex "
                "task so the old loaded agents are no longer present."
            )
            return 0

        old_config = read_text(config_path)
        parsed_config = parse_toml(old_config, "Existing Codex config")
        if args.scope == "personal":
            validate_provider(args.executor_provider, parsed_config)
            validate_provider(args.advisor_provider, parsed_config)

        scan_name_conflicts(agents_dir, managed_paths)
        other_base = personal_base if args.scope == "project" else project_base
        if other_base != base:
            other_agents_dir = other_base / "agents"
            ensure_safe_managed_path(other_agents_dir, other_base)
            scan_name_conflicts(other_agents_dir, {})

        legacy_routes, legacy_orphans = inspect_legacy_routes(
            old_config, parsed_config, base
        )
        config_owned = legacy_config_marker(old_config) is not None
        v1_agents = discover_v1_agents(
            agents_dir,
            {
                executor_path,
                advisor_path,
                legacy_executor_path,
                legacy_advisor_path,
            },
            config_owned,
        )
        legacy_detected = bool(
            config_owned or legacy_routes or legacy_orphans or v1_agents
        )
        if legacy_detected and not args.migrate_legacy:
            raise ConfigurationError(
                "Managed output from a previous release was detected. Preview again "
                "with --migrate-legacy; migration preserves root model and agents.max_* settings."
            )

        warnings: list[str] = []
        catalogs: dict[str | None, dict[str, dict[str, Any]]] = {}
        seats = [
            ("Executor", args.executor_model, executor_effort, args.executor_provider)
        ]
        if args.advisor_model:
            seats.append(("Advisor", args.advisor_model, advisor_effort, args.advisor_provider))
        try:
            source = catalog_source(
                args.codex_bin,
                cwd=project_root,
                codex_home=personal_base,
            )
        except ConfigurationError as exc:
            if not args.confirm_unlisted_models:
                raise
            source = (
                f"unavailable for --codex-bin={args.codex_bin}; model IDs and "
                "efforts require external host confirmation"
            )
            warnings.append(f"Catalog source unavailable: {exc}")
        print(f"Catalog source: {source}")
        provider_labels = sorted({provider or "active" for _, _, _, provider in seats})
        print(f"Catalog provider selection: {', '.join(provider_labels)}")
        for label, model, effort, provider in seats:
            if provider not in catalogs:
                try:
                    catalogs[provider] = load_catalog(
                        args.codex_bin,
                        provider,
                        cwd=project_root,
                        codex_home=personal_base,
                    )
                except ConfigurationError:
                    if not args.confirm_unlisted_models:
                        raise
                    catalogs[provider] = {}
                    warnings.append(
                        f"Could not inspect provider {provider or 'active'}; exact IDs "
                        "require external confirmation."
                    )
            warning = validate_model(
                label, model, effort, catalogs[provider], args.confirm_unlisted_models
            )
            if warning:
                warnings.append(warning)

        resolved_executor_effort = resolve_role_effort(
            executor_effort,
            "Executor",
            args.executor_model,
            catalogs[args.executor_provider],
        )
        if executor_effort == "auto":
            warnings.append(
                f"Executor effort 'auto' resolved to {resolved_executor_effort!r}."
            )
        resolved_advisor_effort: str | None = None
        if args.advisor_model:
            resolved_advisor_effort = resolve_role_effort(
                advisor_effort,
                "Advisor",
                args.advisor_model,
                catalogs[args.advisor_provider],
            )
            if advisor_effort == "auto":
                warnings.append(
                    f"Advisor effort 'auto' resolved to {resolved_advisor_effort!r}."
                )

        new_executor = build_agent_file(
            "executor",
            args.executor_model,
            resolved_executor_effort,
            args.executor_provider,
        )
        changes: list[tuple[Path, str, str]] = [
            (executor_path, old_executor, new_executor)
        ]

        if advisor_action == "configure":
            new_advisor = build_agent_file(
                "advisor",
                args.advisor_model,
                resolved_advisor_effort,
                args.advisor_provider,
            )
            changes.append((advisor_path, old_advisor, new_advisor))
        elif advisor_action == "remove":
            if old_advisor and not managed_advisor:
                raise ConfigurationError(
                    f"Refusing to remove unmanaged custom agent {advisor_path}."
                )
            changes.append((advisor_path, old_advisor, ""))
        elif "advisor" in legacy_routes:
            legacy_routing = legacy_routes["advisor"].routing
            legacy_provider = legacy_routing.get("model_provider")
            if args.scope == "project" and legacy_provider:
                raise ConfigurationError(
                    "A legacy project advisor contains a provider override that cannot "
                    "be migrated safely. Reconfigure it in personal scope."
                )
            if args.scope == "personal":
                validate_provider(legacy_provider, parsed_config)
            legacy_effort = legacy_routing.get("model_reasoning_effort")
            if not legacy_effort:
                raise ConfigurationError(
                    "Legacy advisor effort is missing; provide --advisor-model and an "
                    "explicit --advisor-effort to choose the migrated route."
                )
            converted_advisor = build_agent_file(
                "advisor",
                legacy_routing["model"],
                legacy_effort,
                legacy_provider,
            )
            if old_advisor:
                if routing_tuple(parse_toml(old_advisor, "Existing advisor")) != routing_tuple(
                    parse_toml(converted_advisor, "Converted advisor")
                ):
                    raise ConfigurationError(
                        "Managed standalone and legacy advisors disagree. Choose an "
                        "explicit --advisor-model or --remove-advisor."
                    )
            else:
                changes.append((advisor_path, old_advisor, converted_advisor))
            warnings.append("Legacy advisor route will be converted to a standalone agent.")
        elif old_advisor:
            warnings.append(
                "Existing advisor was left unchanged; use --remove-advisor to remove it."
            )

        legacy_sources: list[tuple[Path, str]] = []
        if args.migrate_legacy and legacy_detected:
            roles_to_remove = {
                role for role, route in legacy_routes.items() if route.has_config_section
            }
            new_config = remove_legacy_tables_and_markers(old_config, roles_to_remove)
            if new_config != old_config:
                changes.append((config_path, old_config, new_config))
                legacy_sources.append((config_path, old_config))
            for route in [*legacy_routes.values(), *legacy_orphans]:
                changes.append((route.layer_path, route.layer_text, ""))
                legacy_sources.append((route.layer_path, route.layer_text))
            for path, content in v1_agents:
                changes.append((path, content, ""))
                legacy_sources.append((path, content))
            warnings.append(
                "Legacy root model/provider/effort and agents.max_* settings were "
                "preserved unchanged; review them manually if desired."
            )

        backup_changes: list[tuple[Path, str, str]] = []
        if args.migrate_legacy:
            seen_sources: set[Path] = set()
            for path, content in legacy_sources:
                if path in seen_sources or not content:
                    continue
                seen_sources.add(path)
                backup_changes.append(backup_change(path, content, base))

        all_changes = [*backup_changes, *changes]
        backup_paths = {path for path, _, _ in backup_changes}
        for path, old, new in all_changes:
            if old == new:
                continue
            if path in backup_paths:
                print(f"Will create protected migration backup: {path} (contents redacted)")
                continue
            if path == config_path:
                print(
                    f"Will remove proven legacy ownership data from {path}; "
                    "unrelated config and diff context are redacted"
                )
                continue
            diff = unified_diff(path, old, new)
            if diff:
                print(diff, end="" if diff.endswith("\n") else "\n")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)

        if not args.apply:
            print("Dry run only. Re-run with --apply after reviewing the diff.")
            return 0

        apply_changes_transactionally(all_changes, transaction_root=base)
        parse_toml(read_text(executor_path), "Written executor custom agent")
        if advisor_action == "configure" or (
            advisor_action == "preserve" and advisor_path.exists()
        ):
            parse_toml(read_text(advisor_path), "Written advisor custom agent")
        print(
            "Standalone custom-agent configuration is valid. Start a new Codex task "
            "to load it. The current task model remains the root orchestrator."
        )
        return 0
    except (ConfigurationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
