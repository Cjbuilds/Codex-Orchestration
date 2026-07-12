#!/usr/bin/env python3
"""Validate release metadata before tagging Codex-Orchestration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
CHANGELOG_VERSION_RE = re.compile(r"^## ([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\s+—\s+(.+)$", re.MULTILINE)


class ReleaseCheckError(RuntimeError):
    pass


def run_check(root: Path, require_tag: bool) -> str:
    manifest_path = root / "plugins/codex-orchestration/.codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ReleaseCheckError(f"manifest version is not semantic: {version!r}")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = CHANGELOG_VERSION_RE.search(changelog)
    if match is None:
        raise ReleaseCheckError("changelog has no version heading")
    if match.group(1) != version:
        raise ReleaseCheckError(
            f"manifest version {version} does not match latest changelog {match.group(1)}"
        )

    lifecycle = (root / "tests/plugin_lifecycle_smoke.py").read_text(encoding="utf-8")
    if f'NEW_VERSION = "{version}"' not in lifecycle:
        raise ReleaseCheckError("lifecycle NEW_VERSION does not match the manifest")

    if require_tag:
        result = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise ReleaseCheckError(f"could not inspect release tags: {result.stderr.strip()}")
        expected = f"v{version}"
        if expected not in result.stdout.splitlines():
            raise ReleaseCheckError(f"HEAD is not tagged with {expected}")
        if match.group(2).strip().lower() == "unreleased":
            raise ReleaseCheckError("tagged release still says Unreleased in CHANGELOG.md")

    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-tag", action="store_true")
    args = parser.parse_args()
    try:
        version = run_check(args.repo_root.resolve(), args.require_tag)
    except (OSError, json.JSONDecodeError, ReleaseCheckError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Release metadata is consistent for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
