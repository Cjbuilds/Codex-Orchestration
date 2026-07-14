from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "review_attestation.py"
SPEC = importlib.util.spec_from_file_location("review_attestation", SCRIPT)
assert SPEC and SPEC.loader
ATTESTATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ATTESTATION)
HEAD = "a" * 40
BASE = "b" * 40


def body(**updates: object) -> str:
    value: dict[str, object] = {
        "schema": 1,
        "risk_tier": "security-state",
        "repository": "Cjbuilds/Codex-Orchestration",
        "base_branch": "main",
        "reviewed_head_sha": HEAD,
        "reviewer_identity": "Independent Reviewer",
        "reviewer_route": "sol-high",
        "threat_model": {
            "assets": ["Exact reviewed commits and protected repository state"],
            "threats": ["Untrusted pull request metadata could bypass review gates"],
            "mitigations": ["Strict schema and immutable SHA validation fail closed"],
        },
        "negative_test_evidence": [
            {
                "category": "negative",
                "evidence": "test rejects a stale reviewed head SHA",
            },
            {
                "category": "malformed",
                "evidence": "test rejects malformed and duplicate JSON blocks",
            },
        ],
        "findings_disposition": "all material findings resolved",
    }
    value.update(updates)
    return (
        "summary\n"
        + ATTESTATION.START_MARKER
        + "\n"
        + json.dumps(value)
        + "\n"
        + ATTESTATION.END_MARKER
    )


def event(pr_body: str, *, head: str = HEAD) -> dict[str, object]:
    return {
        "repository": {"full_name": "Cjbuilds/Codex-Orchestration"},
        "pull_request": {
            "body": pr_body,
            "head": {"sha": head},
            "base": {"ref": "main", "sha": BASE},
        },
    }


class ReviewAttestationTests(unittest.TestCase):
    def test_valid_security_attestation_is_bound_to_head(self) -> None:
        tier = ATTESTATION.validate_pull_request_event(
            event(body()),
            expected_base=BASE,
            expected_head=HEAD,
            changed_paths=["scripts/preflight.py"],
        )
        self.assertEqual(tier, "security-state")

    def test_non_pr_event_needs_no_attestation(self) -> None:
        self.assertIsNone(
            ATTESTATION.validate_pull_request_event(
                {"repository": {"full_name": "Cjbuilds/Codex-Orchestration"}},
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )
        )

    def test_missing_malformed_and_duplicate_blocks_fail(self) -> None:
        duplicate = body() + "\n" + body()
        malformed = ATTESTATION.START_MARKER + "\n{\n" + ATTESTATION.END_MARKER
        for value in ("no block", duplicate, malformed):
            with self.subTest(value=value[:20]):
                with self.assertRaises(ATTESTATION.AttestationError):
                    ATTESTATION.validate_pull_request_event(
                        event(value),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=["scripts/preflight.py"],
                    )

    def test_duplicate_json_key_fails(self) -> None:
        block = body().replace('"schema": 1,', '"schema": 1, "schema": 1,')
        with self.assertRaisesRegex(ATTESTATION.AttestationError, "duplicate key"):
            ATTESTATION.validate_pull_request_event(
                event(block),
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )

    def test_stale_attestation_or_event_head_fails(self) -> None:
        with self.assertRaisesRegex(ATTESTATION.AttestationError, "reviewed SHA"):
            ATTESTATION.validate_pull_request_event(
                event(body(reviewed_head_sha="b" * 40)),
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )
        with self.assertRaisesRegex(ATTESTATION.AttestationError, "event head SHA"):
            ATTESTATION.validate_pull_request_event(
                event(body(), head="b" * 40),
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )

    def test_docs_tier_cannot_hide_behavior_or_security_changes(self) -> None:
        docs = body(
            risk_tier="docs",
            reviewer_identity="not-required",
            reviewer_route="not-required",
            threat_model="not-required",
            negative_test_evidence=[],
            findings_disposition="not-required",
        )
        for changed in (["scripts/preflight.py"], ["src/behavior.py"]):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ATTESTATION.AttestationError, "risk tier"):
                    ATTESTATION.validate_pull_request_event(
                        event(docs),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=changed,
                    )

    def test_docs_tier_allows_explicit_not_required_review(self) -> None:
        docs = body(
            risk_tier="docs",
            reviewer_identity="not-required",
            reviewer_route="not-required",
            threat_model="not-required",
            negative_test_evidence=[],
            findings_disposition="not-required",
        )
        tier = ATTESTATION.validate_pull_request_event(
            event(docs),
            expected_base=BASE,
            expected_head=HEAD,
            changed_paths=["README.md"],
        )
        self.assertEqual(tier, "docs")

    def test_behavior_and_security_tiers_reject_placeholders(self) -> None:
        for field in ("reviewer_identity", "reviewer_route", "findings_disposition"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ATTESTATION.AttestationError, "placeholder"):
                    ATTESTATION.validate_pull_request_event(
                        event(body(**{field: "not-required"})),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=["scripts/preflight.py"],
                    )
        with self.assertRaisesRegex(ATTESTATION.AttestationError, "test evidence"):
            ATTESTATION.validate_pull_request_event(
                event(body(negative_test_evidence=[])),
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )
        for threat_model in (
            "not-required",
            {"assets": ["x"], "threats": ["x"], "mitigations": ["x"]},
            {"assets": ["Only assets are described clearly enough"]},
        ):
            with self.subTest(threat_model=threat_model):
                with self.assertRaisesRegex(
                    ATTESTATION.AttestationError, "threat_model"
                ):
                    ATTESTATION.validate_pull_request_event(
                        event(body(threat_model=threat_model)),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=["scripts/preflight.py"],
                    )

    def test_security_evidence_requires_negative_and_malformed_categories(self) -> None:
        for evidence in (
            [{"category": "negative", "evidence": "negative path was rejected"}],
            [{"category": "malformed", "evidence": "malformed path was rejected"}],
            [{"category": "negative", "evidence": "x"}],
            ["tests passed"],
        ):
            with self.subTest(evidence=evidence):
                with self.assertRaises(ATTESTATION.AttestationError):
                    ATTESTATION.validate_pull_request_event(
                        event(body(negative_test_evidence=evidence)),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=["scripts/preflight.py"],
                    )

    def test_exact_schema_and_repository_are_required(self) -> None:
        for updates in (
            {"schema": True},
            {"repository": "someone/else"},
            {"base_branch": "develop"},
        ):
            with self.subTest(updates=updates):
                with self.assertRaises(ATTESTATION.AttestationError):
                    ATTESTATION.validate_pull_request_event(
                        event(body(**updates)),
                        expected_base=BASE,
                        expected_head=HEAD,
                        changed_paths=["scripts/preflight.py"],
                    )

    def test_event_base_sha_is_bound_to_quality_input(self) -> None:
        value = event(body())
        value["pull_request"]["base"]["sha"] = "c" * 40  # type: ignore[index]
        with self.assertRaisesRegex(ATTESTATION.AttestationError, "base branch"):
            ATTESTATION.validate_pull_request_event(
                value,
                expected_base=BASE,
                expected_head=HEAD,
                changed_paths=["scripts/preflight.py"],
            )

    def test_security_source_rename_cannot_reduce_risk(self) -> None:
        self.assertEqual(
            ATTESTATION.classify_risk(
                ["scripts/preflight.py", "archive/preflight-old.py"]
            ),
            "security-state",
        )

    def test_docs_allowlist_cannot_hide_agent_dependency_or_plugin_changes(self) -> None:
        for path, expected in (
            ("AGENTS.md", "security-state"),
            ("requirements-dev.txt", "security-state"),
            (
                "plugins/codex-orchestration/skills/codex-orchestration/SKILL.md",
                "security-state",
            ),
            ("CHANGELOG.md", "behavior"),
        ):
            with self.subTest(path=path):
                self.assertEqual(ATTESTATION.classify_risk([path]), expected)

    def test_explicit_public_docs_remain_docs(self) -> None:
        self.assertEqual(
            ATTESTATION.classify_risk(["README.md", "docs/usage.md"]), "docs"
        )


if __name__ == "__main__":
    unittest.main()
