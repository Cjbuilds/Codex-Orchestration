#!/usr/bin/env python3
"""Print a compact view of the model catalog exposed by Codex."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROVIDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the model catalog exposed by the installed Codex CLI."
    )
    parser.add_argument("--provider", help="Optional configured Codex provider ID.")
    parser.add_argument("--bundled", action="store_true", help="Skip catalog refresh.")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON.")
    parser.add_argument("--codex-bin", default="codex", help="Codex executable name or path.")
    return parser.parse_args()


def resolve_executable(codex_bin: str) -> str:
    executable = shutil.which(codex_bin) if "/" not in codex_bin else codex_bin
    if not executable:
        raise RuntimeError(f"Codex executable not found: {codex_bin}")
    path = Path(executable).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"Codex executable is not a regular executable file: {path}")
    return str(path.resolve())


def inspect_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return "unknown"
    value = completed.stdout.strip() or completed.stderr.strip()
    return value if completed.returncode == 0 and value else "unknown"


def load_catalog(
    codex_bin: str, provider: str | None, bundled: bool
) -> tuple[dict[str, Any], str, str]:
    executable = resolve_executable(codex_bin)

    command = [executable, "debug", "models"]
    if bundled:
        command.append("--bundled")
    if provider:
        if not PROVIDER_RE.fullmatch(provider):
            raise RuntimeError(f"Invalid provider ID: {provider!r}")
        command.extend(["-c", f'model_provider="{provider}"'])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex model inspection timed out after 30 seconds") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("Codex model inspection returned invalid UTF-8") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run Codex model inspection: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Codex model inspection failed: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex returned invalid model JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Codex model response root is not an object")
    if not isinstance(payload.get("models"), list):
        raise RuntimeError("Codex model response does not contain a models array")
    for index, model in enumerate(payload["models"]):
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str):
            raise RuntimeError(
                f"Codex model response has an invalid model entry at index {index}"
            )
        levels = model.get("supported_reasoning_levels")
        if levels is not None and not isinstance(levels, list):
            raise RuntimeError(
                f"Codex model response has invalid reasoning levels at index {index}"
            )
        for level_index, level in enumerate(levels or []):
            if (
                not isinstance(level, dict)
                or not isinstance(level.get("effort"), str)
            ):
                raise RuntimeError(
                    "Codex model response has an invalid reasoning level at "
                    f"model index {index}, level index {level_index}"
                )
    return payload, executable, inspect_version(executable)


def compact_model(model: dict[str, Any]) -> dict[str, Any]:
    raw_levels = model.get("supported_reasoning_levels")
    levels = raw_levels if isinstance(raw_levels, list) else []
    efforts = [
        item["effort"]
        for item in levels
        if isinstance(item, dict) and isinstance(item.get("effort"), str)
    ]
    return {
        "id": model.get("slug"),
        "display_name": model.get("display_name"),
        "description": model.get("description"),
        "default_effort": model.get("default_reasoning_level"),
        "supported_efforts": [effort for effort in efforts if effort],
        "visibility": model.get("visibility"),
    }


def print_table(models: list[dict[str, Any]]) -> None:
    rows = []
    for model in models:
        rows.append(
            (
                str(model.get("id") or ""),
                ",".join(model.get("supported_efforts") or []) or "default only",
                str(model.get("description") or ""),
            )
        )
    id_width = max([len("MODEL ID"), *(len(row[0]) for row in rows)])
    effort_width = max([len("EFFORTS"), *(len(row[1]) for row in rows)])
    print(f"{'MODEL ID':<{id_width}}  {'EFFORTS':<{effort_width}}  DESCRIPTION")
    for model_id, efforts, description in rows:
        print(f"{model_id:<{id_width}}  {efforts:<{effort_width}}  {description}")


def main() -> int:
    args = parse_args()
    try:
        payload, executable, version = load_catalog(
            args.codex_bin, args.provider, args.bundled
        )
        models = [compact_model(model) for model in payload["models"]]
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    catalog_source = (
        "codex debug models --bundled" if args.bundled else "codex debug models"
    )

    if args.json:
        print(
            json.dumps(
                {
                    "codex_binary": executable,
                    "codex_version": version,
                    "catalog_source": catalog_source,
                    "provider": args.provider,
                    "models": models,
                },
                indent=2,
            )
        )
    else:
        print(
            f"Catalog: {version} at {executable} ({catalog_source})",
            file=sys.stderr,
        )
        print_table(models)
        print(
            "\nNote: the active desktop or remote host may expose newer models than this CLI catalog.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
