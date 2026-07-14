from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("preflight", SCRIPT)
assert SPEC and SPEC.loader
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class PreflightTests(unittest.TestCase):
    def test_command_uses_argv_without_shell_and_has_timeout(self) -> None:
        completed = subprocess.CompletedProcess(["tool"], 0, "ok", "")
        with mock.patch.object(PREFLIGHT.subprocess, "run", return_value=completed) as run:
            result = PREFLIGHT.run_command(
                "example", ["tool", "literal argument"], root=REPO_ROOT, timeout=7
            )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(run.call_args.args[0], ["tool", "literal argument"])
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_missing_subprocess_fails_closed(self) -> None:
        with mock.patch.object(
            PREFLIGHT.subprocess, "run", side_effect=FileNotFoundError("missing")
        ):
            result = PREFLIGHT.run_command(
                "missing", ["absent-tool"], root=REPO_ROOT, timeout=3
            )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("could not start", result.detail)

    def test_subprocess_timeout_fails_closed(self) -> None:
        with mock.patch.object(
            PREFLIGHT.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["slow"], 3),
        ):
            result = PREFLIGHT.run_command(
                "timeout", ["slow"], root=REPO_ROOT, timeout=3
            )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("timed out after 3s", result.detail)

    def test_zero_discovered_tests_fail_closed(self) -> None:
        completed = subprocess.CompletedProcess(
            ["python", "-m", "unittest"], 0, "", "Ran 0 tests in 0.000s\nOK\n"
        )
        with mock.patch.object(PREFLIGHT.subprocess, "run", return_value=completed):
            result = PREFLIGHT.run_command(
                "tests",
                ["python", "-m", "unittest"],
                root=REPO_ROOT,
                timeout=3,
                zero_tests_fail=True,
            )
        self.assertEqual(result.status, "FAIL")
        self.assertIn("zero tests", result.detail)

    def test_unknown_target_returns_argument_error(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            PREFLIGHT.parse_args(["unknown"])
        self.assertEqual(raised.exception.code, 2)

    def test_ci_target_requires_ci_flag(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            PREFLIGHT.parse_args(["quality", "--base-sha", "a", "--head-sha", "b"])
        self.assertEqual(raised.exception.code, 2)

    def test_quality_ci_requires_both_exact_refs(self) -> None:
        results = PREFLIGHT.ci_checks(
            "quality", REPO_ROOT, base_sha="base", head_sha=None
        )
        self.assertEqual(results[0].status, "FAIL")
        self.assertIn("requires exact", results[0].detail)

    def test_quality_ci_rejects_moving_refs(self) -> None:
        results = PREFLIGHT.ci_checks(
            "quality", REPO_ROOT, base_sha="origin/main", head_sha="HEAD"
        )
        self.assertEqual(results[0].status, "FAIL")
        self.assertIn("immutable lowercase commit SHAs", results[0].detail)

    def test_quality_ci_requires_github_event_path(self) -> None:
        results = PREFLIGHT.ci_checks(
            "quality",
            REPO_ROOT,
            base_sha="a" * 40,
            head_sha="b" * 40,
        )
        self.assertEqual(results[0].status, "FAIL")
        self.assertIn("requires --event-path", results[0].detail)

    def test_quality_ci_runs_attestation_with_exact_inputs(self) -> None:
        event_path = Path("/trusted/github-event.json")
        with (
            mock.patch.object(
                PREFLIGHT,
                "ruff_check",
                return_value=PREFLIGHT.CheckResult("ruff", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "release_check",
                return_value=PREFLIGHT.CheckResult("release", "PASS"),
            ) as release,
            mock.patch.object(
                PREFLIGHT,
                "attestation_check",
                return_value=PREFLIGHT.CheckResult("attestation", "PASS"),
            ) as attestation,
        ):
            results = PREFLIGHT.ci_checks(
                "quality",
                REPO_ROOT,
                base_sha="a" * 40,
                head_sha="b" * 40,
                event_path=event_path,
            )
        self.assertTrue(all(result.status == "PASS" for result in results))
        self.assertTrue(release.call_args.kwargs["require_exact"])
        self.assertEqual(attestation.call_args.kwargs["event_path"], event_path)

    def test_diff_hygiene_includes_staged_changes(self) -> None:
        with (
            mock.patch.object(
                PREFLIGHT,
                "run_command",
                return_value=PREFLIGHT.CheckResult("x", "PASS"),
            ) as run,
            mock.patch.object(
                PREFLIGHT,
                "compile_check",
                return_value=PREFLIGHT.CheckResult("compile", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "ruff_check",
                return_value=PREFLIGHT.CheckResult("ruff", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "unittest_check",
                return_value=PREFLIGHT.CheckResult("tests", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "release_check",
                return_value=PREFLIGHT.CheckResult("release", "PASS"),
            ),
        ):
            PREFLIGHT.quick_checks(REPO_ROOT, base_sha="base", head_sha=None)
        self.assertEqual(
            run.call_args_list[0].args[1], ["git", "diff", "--check", "HEAD"]
        )

    def test_local_skip_reports_partial_never_all_passed(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = PREFLIGHT.report(
                [
                    PREFLIGHT.CheckResult("compile", "PASS"),
                    PREFLIGHT.CheckResult("ruff", "SKIP", "not installed"),
                ],
                ci=False,
            )
        self.assertEqual(code, 0)
        self.assertIn("PARTIAL", stdout.getvalue())
        self.assertNotIn("all requested preflight gates passed", stdout.getvalue())

    def test_quick_always_discloses_hosted_only_coverage(self) -> None:
        with (
            mock.patch.object(
                PREFLIGHT,
                "run_command",
                return_value=PREFLIGHT.CheckResult("x", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "compile_check",
                return_value=PREFLIGHT.CheckResult("compile", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "ruff_check",
                return_value=PREFLIGHT.CheckResult("ruff", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "unittest_check",
                return_value=PREFLIGHT.CheckResult("tests", "PASS"),
            ),
            mock.patch.object(
                PREFLIGHT,
                "release_check",
                return_value=PREFLIGHT.CheckResult("release", "PASS"),
            ),
        ):
            results = PREFLIGHT.quick_checks(
                REPO_ROOT, base_sha="base", head_sha=None
            )
        hosted = [result for result in results if result.name == "hosted-required-checks"]
        self.assertEqual(len(hosted), 1)
        self.assertEqual(hosted[0].status, "SKIP")

    def test_ci_skip_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = PREFLIGHT.report(
                [PREFLIGHT.CheckResult("tool", "SKIP", "missing")], ci=True
            )
        self.assertEqual(code, 1)
        self.assertIn("may not skip", stderr.getvalue())

    def test_legacy_guard_accepts_only_the_pinned_rejection(self) -> None:
        expected = PREFLIGHT.CheckResult(
            "legacy-policy-probe",
            "FAIL",
            "exit 1: Error: data did not match any variant of untagged enum "
            "FeatureToml\nin `features.multi_agent_v2`",
        )
        with mock.patch.object(PREFLIGHT, "run_command", return_value=expected):
            results = PREFLIGHT.ci_checks(
                "legacy", REPO_ROOT, base_sha=None, head_sha=None
            )
        self.assertEqual(results[0].status, "PASS")

    def test_legacy_guard_rejects_unrelated_failures_and_kills(self) -> None:
        for detail in ("exit 2: corrupted config", "exit -9: killed"):
            with self.subTest(detail=detail):
                failure = PREFLIGHT.CheckResult(
                    "legacy-policy-probe", "FAIL", detail
                )
                with mock.patch.object(
                    PREFLIGHT, "run_command", return_value=failure
                ):
                    results = PREFLIGHT.ci_checks(
                        "legacy", REPO_ROOT, base_sha=None, head_sha=None
                    )
                self.assertEqual(results[0].status, "FAIL")
                self.assertIn("unexpected reason", results[0].detail)

    def test_nested_focused_suite_omits_preflight_module(self) -> None:
        captured: list[list[str]] = []

        def fake_unittest(
            root: Path,
            name: str,
            modules: list[str] | None = None,
            **_kwargs: object,
        ) -> PREFLIGHT.CheckResult:
            captured.append(modules or [])
            return PREFLIGHT.CheckResult(name, "PASS")

        with (
            mock.patch.dict(PREFLIGHT.os.environ, {PREFLIGHT.ACTIVE_ENV: "1"}),
            mock.patch.object(PREFLIGHT, "run_command", return_value=PREFLIGHT.CheckResult("x", "PASS")),
            mock.patch.object(PREFLIGHT, "compile_check", return_value=PREFLIGHT.CheckResult("compile", "PASS")),
            mock.patch.object(PREFLIGHT, "ruff_check", return_value=PREFLIGHT.CheckResult("ruff", "PASS")),
            mock.patch.object(PREFLIGHT, "unittest_check", side_effect=fake_unittest),
            mock.patch.object(PREFLIGHT, "release_check", return_value=PREFLIGHT.CheckResult("release", "PASS")),
        ):
            PREFLIGHT.quick_checks(REPO_ROOT, base_sha="base", head_sha=None)
        self.assertNotIn("tests.test_preflight", captured[0])


if __name__ == "__main__":
    unittest.main()
