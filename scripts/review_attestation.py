#!/usr/bin/env python3
"""Validate a pull request's SHA-bound review attestation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


START_MARKER = "<!-- codex-review-attestation:start -->"
END_MARKER = "<!-- codex-review-attestation:end -->"
EXPECTED_REPOSITORY = "Cjbuilds/Codex-Orchestration"
EXPECTED_BASE = "main"
EXACT_SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
MAX_EVENT_BYTES = 1_000_000
MAX_GIT_OUTPUT_BYTES = 1_000_000
PLACEHOLDERS = {"", "not-required", "todo", "replace-me", "n/a", "none"}
FIELDS = {
    "schema",
    "risk_tier",
    "repository",
    "base_branch",
    "reviewed_head_sha",
    "reviewer_identity",
    "reviewer_route",
    "threat_model",
    "negative_test_evidence",
    "findings_disposition",
}
SECURITY_PATHS = {
    "AGENTS.md",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    "requirements-dev.txt",
    "plugins/codex-orchestration/.codex-plugin/plugin.json",
    "plugins/codex-orchestration/.mcp.json",
    "scripts/install_hooks.py",
    "scripts/merge_ready_pr.py",
    "scripts/preflight.py",
    "scripts/release_check.py",
    "scripts/review_attestation.py",
}
SECURITY_PREFIXES = (
    ".github/workflows/",
    ".githooks/",
    "plugins/codex-orchestration/skills/",
    "plugins/codex-orchestration/skills/codex-orchestration/scripts/",
)
DOC_PATHS = {
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LICENSE.md",
    "README.md",
    "RELEASE.md",
}
DOC_SUFFIXES = {".md", ".rst", ".txt"}


class AttestationError(RuntimeError):
    """The event, changed-path set, or attestation is unsafe or malformed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError(f"attestation contains duplicate key {key!r}")
        result[key] = value
    return result


def _read_event(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AttestationError("GitHub event path must be a regular file")
        if info.st_size > MAX_EVENT_BYTES:
            raise AttestationError("GitHub event payload is too large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AttestationError("GitHub event payload is missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"could not read a valid GitHub event payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise AttestationError("GitHub event payload must be an object")
    return payload


def _git_changed_paths(root: Path, base_sha: str, head_sha: str) -> list[str]:
    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--diff-filter=ACDMRTUXB",
                f"{base_sha}...{head_sha}",
                "--",
            ],
            cwd=root,
            capture_output=True,
            text=False,
            timeout=30,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AttestationError(f"could not inspect changed paths: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr[:4000].decode("utf-8", errors="replace").strip()
        raise AttestationError(f"could not inspect changed paths: {detail or 'git failed'}")
    if len(completed.stdout) > MAX_GIT_OUTPUT_BYTES:
        raise AttestationError("changed-path output is too large")
    try:
        paths = completed.stdout.decode("utf-8").split("\x00")
    except UnicodeDecodeError as exc:
        raise AttestationError("changed paths are not valid UTF-8") from exc
    result = [path for path in paths if path]
    if not result:
        raise AttestationError("pull request contains no changed paths")
    return result


def classify_risk(paths: list[str]) -> str:
    if not paths:
        raise AttestationError("cannot classify an empty change")
    if any(
        path in SECURITY_PATHS or path.startswith(SECURITY_PREFIXES)
        for path in paths
    ):
        return "security-state"
    if all(
        path in DOC_PATHS
        or (path.startswith("docs/") and Path(path).suffix.lower() in DOC_SUFFIXES)
        for path in paths
    ):
        return "docs"
    return "behavior"


def _required_string(value: Any, field: str, *, allow_not_required: bool) -> str:
    if not isinstance(value, str) or len(value) > 10_000:
        raise AttestationError(f"{field} must be a bounded string")
    normalized = value.strip().lower()
    if not allow_not_required and (normalized in PLACEHOLDERS or "<" in value):
        raise AttestationError(f"{field} still contains a placeholder")
    return value


def _meaningful_string(value: Any, field: str) -> str:
    text = _required_string(value, field, allow_not_required=False).strip()
    if len(text) < 12 or text.lower() in {"placeholder", "tests passed", "all good"}:
        raise AttestationError(f"{field} must contain specific evidence")
    return text


def _validate_threat_model(value: Any, *, required: bool) -> None:
    if not required:
        _required_string(value, "threat_model", allow_not_required=True)
        return
    if not isinstance(value, dict) or set(value) != {
        "assets",
        "threats",
        "mitigations",
    }:
        raise AttestationError(
            "security-state threat_model requires assets, threats, and mitigations"
        )
    for category in ("assets", "threats", "mitigations"):
        entries = value[category]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
            raise AttestationError(f"threat_model {category} must be a bounded list")
        for entry in entries:
            _meaningful_string(entry, f"threat_model {category} item")


def _validate_test_evidence(value: Any, *, tier: str) -> None:
    if not isinstance(value, list) or len(value) > 50:
        raise AttestationError("negative_test_evidence must be a bounded array")
    categories: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"category", "evidence"}:
            raise AttestationError(
                "each test-evidence item requires category and evidence"
            )
        category = item["category"]
        if category not in {"regression", "negative", "malformed"}:
            raise AttestationError("test-evidence category is not supported")
        _meaningful_string(item["evidence"], "test-evidence detail")
        categories.add(category)
    if tier in {"behavior", "security-state"} and not value:
        raise AttestationError(f"{tier} changes require test evidence")
    if tier == "security-state" and not {"negative", "malformed"}.issubset(categories):
        raise AttestationError(
            "security-state changes require separate negative and malformed evidence"
        )


def parse_attestation(body: str) -> dict[str, Any]:
    if len(body) > 200_000:
        raise AttestationError("pull request body is too large")
    if body.count(START_MARKER) != 1 or body.count(END_MARKER) != 1:
        raise AttestationError("pull request body must contain exactly one attestation block")
    before, remainder = body.split(START_MARKER, 1)
    block, after = remainder.split(END_MARKER, 1)
    del before, after
    try:
        value = json.loads(block, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, AttestationError) as exc:
        if isinstance(exc, AttestationError):
            raise
        raise AttestationError(f"attestation is not strict JSON: {exc}") from exc
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise AttestationError("attestation fields do not match schema 1")
    return value


def validate_pull_request_event(
    event: dict[str, Any],
    *,
    expected_base: str,
    expected_head: str,
    changed_paths: list[str],
) -> str | None:
    pull_request = event.get("pull_request")
    if pull_request is None:
        return None
    if not isinstance(pull_request, dict):
        raise AttestationError("pull_request event value must be an object")
    repository = event.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != EXPECTED_REPOSITORY:
        raise AttestationError("event repository does not match the protected repository")
    head = pull_request.get("head")
    base = pull_request.get("base")
    if not isinstance(head, dict) or head.get("sha") != expected_head:
        raise AttestationError("event head SHA does not match the strict quality input")
    if (
        not isinstance(base, dict)
        or base.get("ref") != EXPECTED_BASE
        or base.get("sha") != expected_base
    ):
        raise AttestationError("event base branch is not main")
    body = pull_request.get("body")
    if not isinstance(body, str):
        raise AttestationError("pull request body is missing")

    value = parse_attestation(body)
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise AttestationError("attestation schema must be the integer 1")
    if value["repository"] != EXPECTED_REPOSITORY:
        raise AttestationError("attestation repository does not match")
    if value["base_branch"] != EXPECTED_BASE:
        raise AttestationError("attestation base branch is not main")
    if value["reviewed_head_sha"] != expected_head:
        raise AttestationError("attestation reviewed SHA is stale or incorrect")

    required_tier = classify_risk(changed_paths)
    if value["risk_tier"] != required_tier:
        raise AttestationError(
            f"attestation risk tier {value['risk_tier']!r} must be {required_tier!r}"
        )
    _validate_test_evidence(value["negative_test_evidence"], tier=required_tier)

    needs_review = required_tier in {"behavior", "security-state"}
    _required_string(
        value["reviewer_identity"],
        "reviewer_identity",
        allow_not_required=not needs_review,
    )
    _required_string(
        value["reviewer_route"],
        "reviewer_route",
        allow_not_required=not needs_review,
    )
    _required_string(
        value["findings_disposition"],
        "findings_disposition",
        allow_not_required=not needs_review,
    )
    _validate_threat_model(
        value["threat_model"], required=required_tier == "security-state"
    )
    return required_tier


def validate_event_file(
    event_path: Path, *, repo_root: Path, base_sha: str, head_sha: str
) -> str | None:
    if not EXACT_SHA_RE.fullmatch(base_sha) or not EXACT_SHA_RE.fullmatch(head_sha):
        raise AttestationError("review validation requires exact lowercase commit SHAs")
    event = _read_event(event_path)
    if event.get("pull_request") is None:
        return None
    paths = _git_changed_paths(repo_root, base_sha, head_sha)
    return validate_pull_request_event(
        event,
        expected_base=base_sha,
        expected_head=head_sha,
        changed_paths=paths,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    try:
        tier = validate_event_file(
            args.event_path.absolute(),
            repo_root=args.repo_root.resolve(),
            base_sha=args.base_sha,
            head_sha=args.head_sha,
        )
    except AttestationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if tier is None:
        print("Review attestation is not required for this non-PR event.")
    else:
        print(f"Review attestation is current and valid for risk tier {tier}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
