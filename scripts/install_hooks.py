#!/usr/bin/env python3
"""Safely preview or enable this repository's versioned Git hooks."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


HOOKS_PATH = ".githooks"
GIT_TIMEOUT_SECONDS = 10


class HookInstallError(RuntimeError):
    """Raised when repository-local hooks cannot be configured safely."""


def _git(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HookInstallError(f"could not run git: {exc}") from exc


def _rollback_after_failed_verification(root: Path, failure: HookInstallError) -> None:
    try:
        rollback = _git(
            ["config", "--local", "--unset-all", "core.hooksPath"], root
        )
    except HookInstallError as rollback_error:
        raise HookInstallError(
            f"{failure}; rollback also failed: {rollback_error}"
        ) from rollback_error
    if rollback.returncode != 0:
        detail = rollback.stderr.strip() or f"git config exited {rollback.returncode}"
        raise HookInstallError(f"{failure}; rollback also failed: {detail}") from failure
    try:
        verify = _git(["config", "--local", "--get", "core.hooksPath"], root)
    except HookInstallError as rollback_error:
        raise HookInstallError(
            f"{failure}; could not verify rollback: {rollback_error}"
        ) from rollback_error
    if verify.returncode != 1 or verify.stdout.strip():
        detail = verify.stderr.strip() or "core.hooksPath is still configured"
        raise HookInstallError(f"{failure}; rollback verification failed: {detail}")
    raise failure


def configure_hooks(repository: Path, *, apply: bool) -> str:
    """Inspect and optionally set the repository-local ``core.hooksPath``."""
    top_level = _git(["rev-parse", "--show-toplevel"], repository)
    if top_level.returncode != 0:
        detail = top_level.stderr.strip() or "not a Git repository"
        raise HookInstallError(f"could not locate repository: {detail}")
    root = Path(top_level.stdout.strip())

    current_result = _git(["config", "--local", "--get", "core.hooksPath"], root)
    if current_result.returncode not in (0, 1):
        detail = current_result.stderr.strip() or "git config failed"
        raise HookInstallError(f"could not inspect local core.hooksPath: {detail}")
    current = current_result.stdout.strip() if current_result.returncode == 0 else ""

    effective_result = _git(["config", "--get", "core.hooksPath"], root)
    if effective_result.returncode not in (0, 1):
        detail = effective_result.stderr.strip() or "git config failed"
        raise HookInstallError(f"could not inspect effective core.hooksPath: {detail}")
    effective = (
        effective_result.stdout.strip() if effective_result.returncode == 0 else ""
    )

    if current:
        if current == HOOKS_PATH and effective == HOOKS_PATH:
            return f"Repository already uses core.hooksPath={HOOKS_PATH}."
        if current == HOOKS_PATH:
            raise HookInstallError(
                "refusing mismatched core.hooksPath: local is .githooks but "
                f"effective value is {effective!r}"
            )
        raise HookInstallError(
            f"refusing to replace existing local core.hooksPath={current!r}"
        )
    if effective:
        if effective == HOOKS_PATH:
            return f"Repository already uses effective core.hooksPath={HOOKS_PATH}."
        raise HookInstallError(
            f"refusing to replace existing effective core.hooksPath={effective!r}"
        )
    if not apply:
        return (
            f"Preview: would set repository-local core.hooksPath={HOOKS_PATH}. "
            "Re-run with --apply to enable the hooks."
        )

    update = _git(["config", "--local", "core.hooksPath", HOOKS_PATH], root)
    if update.returncode != 0:
        detail = update.stderr.strip() or "git config failed"
        raise HookInstallError(f"could not set local core.hooksPath: {detail}")
    try:
        verify = _git(["config", "--local", "--get", "core.hooksPath"], root)
        effective_verify = _git(["config", "--get", "core.hooksPath"], root)
        if (
            verify.returncode != 0
            or verify.stdout.strip() != HOOKS_PATH
            or effective_verify.returncode != 0
            or effective_verify.stdout.strip() != HOOKS_PATH
        ):
            raise HookInstallError("could not verify local core.hooksPath")
    except HookInstallError as exc:
        _rollback_after_failed_verification(root, exc)
    return f"Set repository-local core.hooksPath={HOOKS_PATH}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="set repository-local core.hooksPath after safety checks",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        message = configure_hooks(args.repo.resolve(), apply=args.apply)
    except HookInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
