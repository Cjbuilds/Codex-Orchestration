#!/usr/bin/env python3
"""Deterministic local and CI preflight gates for Codex-Orchestration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT_CHARS = 12_000
ACTIVE_ENV = "CODEX_ORCHESTRATION_PREFLIGHT_ACTIVE"
CI_TARGETS = {"quality", "test", "lifecycle", "legacy", "portability"}
LOCAL_TARGETS = {"quick", "full"}
ALL_TARGETS = LOCAL_TARGETS | CI_TARGETS
EXACT_SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class CheckResult(NamedTuple):
    name: str
    status: str
    detail: str = ""


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"


def run_command(
    name: str,
    arguments: list[str],
    *,
    root: Path,
    timeout: int,
    env: dict[str, str] | None = None,
    zero_tests_fail: bool = False,
) -> CheckResult:
    """Run one bounded argv-only subprocess and normalize its result."""
    try:
        completed = subprocess.run(
            arguments,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except OSError as exc:
        return CheckResult(name, "FAIL", f"could not start {arguments!r}: {exc}")
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            part
            for part in (
                exc.stdout if isinstance(exc.stdout, str) else "",
                exc.stderr if isinstance(exc.stderr, str) else "",
            )
            if part
        )
        detail = f"timed out after {timeout}s"
        if output:
            detail += f": {_clip(output)}"
        return CheckResult(name, "FAIL", detail)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    clipped = _clip(output)
    if completed.returncode != 0:
        return CheckResult(
            name,
            "FAIL",
            f"exit {completed.returncode}" + (f": {clipped}" if clipped else ""),
        )
    if zero_tests_fail and re.search(r"\bRan\s+0\s+tests?\b", output):
        return CheckResult(name, "FAIL", "test command discovered zero tests")
    return CheckResult(name, "PASS", clipped)


def _python(*arguments: str) -> list[str]:
    return [sys.executable, *arguments]


def compile_check(root: Path) -> CheckResult:
    return run_command(
        "compile",
        _python("-m", "compileall", "-q", "plugins", "tests", "scripts"),
        root=root,
        timeout=90,
    )


def ruff_check(root: Path, *, ci: bool) -> CheckResult:
    result = run_command(
        "ruff",
        ["ruff", "check", "plugins", "tests", "scripts"],
        root=root,
        timeout=120,
    )
    if not ci and result.status == "FAIL" and "could not start" in result.detail:
        return CheckResult("ruff", "SKIP", "Ruff is not installed locally")
    return result


def unittest_check(
    root: Path,
    name: str,
    modules: list[str] | None = None,
    *,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> CheckResult:
    arguments = _python("-m", "unittest")
    if modules is None:
        arguments.extend(["discover", "-s", "tests", "-v"])
    else:
        arguments.extend(["-v", *modules])
    return run_command(
        name,
        arguments,
        root=root,
        timeout=timeout,
        env=env,
        zero_tests_fail=True,
    )


def release_check(
    root: Path, *, base_sha: str, head_sha: str | None, require_exact: bool = False
) -> CheckResult:
    arguments = _python(
        "scripts/release_check.py", "--repo-root", str(root), "--base-sha", base_sha
    )
    if head_sha is not None:
        arguments.extend(["--head-sha", head_sha])
    if require_exact:
        arguments.append("--require-exact-shas")
    return run_command("release-identity", arguments, root=root, timeout=60)


def attestation_check(
    root: Path, *, event_path: Path, base_sha: str, head_sha: str
) -> CheckResult:
    return run_command(
        "review-attestation",
        _python(
            "scripts/review_attestation.py",
            "--event-path",
            str(event_path),
            "--repo-root",
            str(root),
            "--base-sha",
            base_sha,
            "--head-sha",
            head_sha,
        ),
        root=root,
        timeout=60,
    )


def quick_checks(root: Path, *, base_sha: str, head_sha: str | None) -> list[CheckResult]:
    results = [
        run_command(
            "git-diff-check", ["git", "diff", "--check", "HEAD"], root=root, timeout=30
        ),
        compile_check(root),
        ruff_check(root, ci=False),
    ]
    modules = [
        "tests.test_packaging",
        "tests.test_skill_contract",
        "tests.test_routing_state",
        "tests.test_release_check",
        "tests.test_review_attestation",
    ]
    if os.environ.get(ACTIVE_ENV) != "1":
        modules.append("tests.test_preflight")
    environment = os.environ.copy()
    environment[ACTIVE_ENV] = "1"
    results.append(
        unittest_check(root, "focused-tests", modules, timeout=600, env=environment)
    )
    results.append(release_check(root, base_sha=base_sha, head_sha=head_sha))
    results.append(
        CheckResult(
            "hosted-required-checks",
            "SKIP",
            "Python 3.11/3.13, Windows portability, and CodeQL remain hosted-only",
        )
    )
    return results


def _codex_available(root: Path) -> CheckResult:
    return run_command("codex-available", ["codex", "--version"], root=root, timeout=15)


def full_local_checks(
    root: Path, *, base_sha: str, head_sha: str | None
) -> list[CheckResult]:
    results = quick_checks(root, base_sha=base_sha, head_sha=head_sha)
    results.append(unittest_check(root, "full-tests", timeout=900))
    codex = _codex_available(root)
    if codex.status == "FAIL" and "could not start" in codex.detail:
        results.append(
            CheckResult("lifecycle", "SKIP", "local codex executable is unavailable")
        )
    elif codex.status == "FAIL":
        results.append(CheckResult("lifecycle", "FAIL", codex.detail))
    else:
        results.append(
            run_command(
                "lifecycle",
                _python("tests/plugin_lifecycle_smoke.py"),
                root=root,
                timeout=1200,
            )
        )
    return results


def ci_checks(
    target: str,
    root: Path,
    *,
    base_sha: str | None,
    head_sha: str | None,
    event_path: Path | None = None,
) -> list[CheckResult]:
    if target == "quality":
        if not base_sha or not head_sha:
            return [
                CheckResult(
                    "release-identity",
                    "FAIL",
                    "quality --ci requires exact --base-sha and --head-sha",
                )
            ]
        if not EXACT_SHA_RE.fullmatch(base_sha) or not EXACT_SHA_RE.fullmatch(head_sha):
            return [
                CheckResult(
                    "release-identity",
                    "FAIL",
                    "quality --ci requires immutable lowercase commit SHAs",
                )
            ]
        if event_path is None:
            return [
                CheckResult(
                    "review-attestation",
                    "FAIL",
                    "quality --ci requires --event-path",
                )
            ]
        return [
            ruff_check(root, ci=True),
            release_check(
                root,
                base_sha=base_sha,
                head_sha=head_sha,
                require_exact=True,
            ),
            attestation_check(
                root,
                event_path=event_path,
                base_sha=base_sha,
                head_sha=head_sha,
            ),
        ]
    if target == "test":
        return [compile_check(root), unittest_check(root, "full-tests", timeout=900)]
    if target == "lifecycle":
        return [
            run_command(
                "lifecycle",
                _python("tests/plugin_lifecycle_smoke.py"),
                root=root,
                timeout=1200,
            )
        ]
    if target == "legacy":
        command = [
            "codex",
            "-c",
            'features.multi_agent_v2.multi_agent_mode_hint_text="PROBE"',
            "features",
            "list",
        ]
        probe = run_command("legacy-policy-probe", command, root=root, timeout=60)
        if probe.status == "PASS":
            return [
                CheckResult(
                    "legacy-policy-probe",
                    "FAIL",
                    "old Codex unexpectedly accepted the structured policy probe",
                )
            ]
        expected = (
            probe.detail.startswith("exit 1:")
            and "data did not match any variant of untagged enum FeatureToml"
            in probe.detail
            and "features.multi_agent_v2" in probe.detail
        )
        if expected:
            return [
                CheckResult(
                    "legacy-policy-probe",
                    "PASS",
                    "old Codex rejected the structured policy field",
                )
            ]
        return [
            CheckResult(
                "legacy-policy-probe",
                "FAIL",
                f"old Codex failed for an unexpected reason: {probe.detail}",
            )
        ]
    if target == "portability":
        results = [
            compile_check(root),
            unittest_check(
                root,
                "portability-tests",
                [
                    "tests.test_inspect_models",
                    "tests.test_packaging",
                    "tests.test_skill_contract",
                ],
                timeout=600,
            ),
        ]
        if os.name == "nt":
            results.append(
                unittest_check(
                    root,
                    "windows-fail-closed",
                    [
                        "tests.test_configure_orchestration.ConfigureOrchestrationTests."
                        "test_windows_existing_file_update_fails_closed"
                    ],
                    timeout=300,
                )
            )
        return results
    return [CheckResult("target", "FAIL", f"unknown CI target: {target}")]


def report(results: list[CheckResult], *, ci: bool) -> int:
    for result in results:
        print(f"{result.status}: {result.name}")
        if result.detail and result.status != "PASS":
            print(_clip(result.detail), file=sys.stderr if result.status == "FAIL" else sys.stdout)
    if any(result.status == "FAIL" for result in results):
        print("FAIL: one or more preflight gates failed.", file=sys.stderr)
        return 1
    if ci and any(result.status == "SKIP" for result in results):
        print("FAIL: CI gates may not skip prerequisites.", file=sys.stderr)
        return 1
    if any(result.status == "SKIP" for result in results):
        print("PARTIAL: available local gates passed; one or more prerequisites were skipped.")
        return 0
    if not results:
        print("FAIL: preflight selected no checks.", file=sys.stderr)
        return 1
    print("PASS: all requested preflight gates passed.")
    return 0


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(arguments)
    if args.target not in ALL_TARGETS:
        parser.error(f"unknown target: {args.target}")
    if args.target in CI_TARGETS and not args.ci:
        parser.error(f"target {args.target} requires --ci")
    if args.target in LOCAL_TARGETS and args.ci:
        parser.error(f"target {args.target} does not accept --ci")
    if args.target != "quality" and (
        args.base_sha or args.head_sha or args.event_path
    ) and args.target in CI_TARGETS:
        parser.error("CI refs and event paths are only accepted by the quality target")
    if args.head_sha and not args.base_sha:
        parser.error("--head-sha requires --base-sha")
    return args


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    root = args.repo_root.resolve()
    if args.target == "quick":
        results = quick_checks(
            root, base_sha=args.base_sha or "origin/main", head_sha=args.head_sha
        )
    elif args.target == "full":
        results = full_local_checks(
            root, base_sha=args.base_sha or "origin/main", head_sha=args.head_sha
        )
    else:
        results = ci_checks(
            args.target,
            root,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            event_path=args.event_path,
        )
    return report(results, ci=args.ci)


if __name__ == "__main__":
    raise SystemExit(main())
