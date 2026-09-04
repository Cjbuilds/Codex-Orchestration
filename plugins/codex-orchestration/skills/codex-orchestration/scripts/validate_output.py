#!/usr/bin/env python3
"""Check a Validator receipt against its packet; never authenticate its author.

Exit 0 means a structurally complete APPROVED receipt, 1 means REVISE, and 2
means invalid input. The root must separately verify the actual invocation,
current artifact revisions, evidence quality, and actionable findings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


MAX_INPUT_BYTES = 1_000_000


class ValidationError(ValueError):
    """A packet or receipt is incomplete, contradictory, or mismatched."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def nonempty(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def object_keys(value: Any, keys: set[str], label: str) -> None:
    require(type(value) is dict and set(value) == keys, f"{label}: wrong fields")


def unique_strings(value: Any, label: str) -> set[str]:
    require(type(value) is list and bool(value), f"{label}: expected a nonempty list")
    require(all(nonempty(item) for item in value), f"{label}: expected nonempty strings")
    require(len(set(value)) == len(value), f"{label}: duplicate entries")
    return set(value)


def validate_output(packet: Any, receipt: Any) -> str:
    """Return the checked decision, or raise ValidationError. No side effects."""
    object_keys(packet, {"batch_id", "artifacts", "criteria"}, "packet")
    require(nonempty(packet["batch_id"]), "packet: empty batch_id")
    artifacts = packet["artifacts"]
    require(type(artifacts) is dict and bool(artifacts), "packet: empty artifacts")
    require(
        all(nonempty(key) and nonempty(value) for key, value in artifacts.items()),
        "packet: artifact IDs and revisions must be nonempty strings",
    )
    criteria = unique_strings(packet["criteria"], "packet criteria")
    object_keys(
        receipt,
        {"decision", "batch_id", "artifacts", "checks", "findings"},
        "receipt",
    )
    decision = receipt["decision"]
    require(
        type(decision) is str
        and decision in {"VALIDATION_APPROVED", "VALIDATION_REVISE"},
        "receipt: unknown decision",
    )
    require(receipt["batch_id"] == packet["batch_id"], "receipt: wrong batch_id")
    require(
        type(receipt["artifacts"]) is dict and receipt["artifacts"] == artifacts,
        "receipt: artifact set or revision mismatch",
    )
    checks = receipt["checks"]
    require(type(checks) is list and bool(checks), "receipt: missing checks")
    seen_criteria: set[str] = set()
    covered_artifacts: set[str] = set()
    failed = False
    for check in checks:
        object_keys(check, {"criterion", "artifacts", "status", "evidence"}, "check")
        criterion = check["criterion"]
        require(nonempty(criterion), "check: invalid criterion")
        require(criterion in criteria, "check: unknown criterion")
        require(criterion not in seen_criteria, "check: duplicate criterion")
        seen_criteria.add(criterion)
        covered = unique_strings(check["artifacts"], "check artifacts")
        require(covered <= set(artifacts), "check: unknown artifact")
        covered_artifacts.update(covered)
        require(
            type(check["status"]) is str and check["status"] in {"pass", "fail"},
            "check: invalid status",
        )
        require(nonempty(check["evidence"]), "check: missing evidence")
        failed = failed or check["status"] == "fail"
    require(seen_criteria == criteria, "receipt: criteria coverage incomplete")
    require(covered_artifacts == set(artifacts), "receipt: artifact coverage incomplete")
    findings = receipt["findings"]
    require(type(findings) is list, "receipt: findings must be a list")
    require(all(nonempty(item) for item in findings), "receipt: empty finding")
    if decision == "VALIDATION_APPROVED":
        require(not failed and not findings, "receipt: approval contradicts checks/findings")
    else:
        require(failed and bool(findings), "receipt: revision needs a failed check and findings")
    return decision


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"JSON: duplicate key {key!r}")
        value[key] = item
    return value


def reject_constant(value: str) -> None:
    raise ValidationError(f"JSON: unsupported constant {value}")


def read_json(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, "JSON: input exceeds one megabyte")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        decision = validate_output(read_json(args.packet), read_json(args.receipt))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"VALIDATION_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(decision)
    return 0 if decision == "VALIDATION_APPROVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
