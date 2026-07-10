from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
)
SKILL = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
REFERENCE = (SKILL_ROOT / "references" / "providers-and-models.md").read_text(
    encoding="utf-8"
)


class SkillContractTests(unittest.TestCase):
    def test_invocation_authorizes_but_never_forces_delegation(self) -> None:
        self.assertIn("authorizes Codex to use its native subagents", SKILL)
        self.assertIn("does not require a spawn", SKILL)
        self.assertIn("explicit `no subagents` instruction", SKILL)

    def test_missing_values_require_a_fresh_explicit_invocation(self) -> None:
        self.assertIn("explicit-only and may not reload from a bare reply", SKILL)
        self.assertIn(
            "<exact-skill-label> executor=<model>@<effort-or-auto>", SKILL
        )
        self.assertIn("<original task>", SKILL)
        self.assertIn("Reuse the exact label that invoked the skill", SKILL)
        self.assertIn("placeholders only for missing seats", SKILL)
        self.assertIn("every understood modifier", SKILL)
        self.assertIn("save for this project", SKILL)

    def test_task_local_auto_is_omitted_not_forwarded(self) -> None:
        self.assertIn("omit the reasoning-effort override", SKILL)
        self.assertIn("Do not pass the literal value `auto`", SKILL)
        self.assertIn("resolve `auto` to a concrete", REFERENCE)
        self.assertIn("effective effort is host-dependent", SKILL)
        self.assertIn("may inherit the root session's effort", REFERENCE)

    def test_task_local_advisor_is_not_claimed_as_sandboxed(self) -> None:
        self.assertIn("review-only by instruction", SKILL)
        self.assertIn("not guaranteed read-only", SKILL)
        self.assertIn("live parent permission overrides", SKILL)
        self.assertIn('sandbox_mode = "read-only"', SKILL)

    def test_advisor_failure_is_not_an_approval_signal(self) -> None:
        self.assertIn("root-side `advisor unavailable`", SKILL)
        self.assertNotIn("ADVISOR_BLOCKED", SKILL)
        self.assertIn("never as approval", SKILL)
        self.assertIn("Supplying an advisor makes its review required", SKILL)

    def test_simple_work_skips_unnecessary_review_and_delegation(self) -> None:
        self.assertIn("Skip it for simple work", SKILL)
        self.assertIn("After Codex independently decides delegation will help", SKILL)

    def test_goal_state_remains_user_owned(self) -> None:
        self.assertIn("Do not create or change Goal state", SKILL)
        self.assertIn("user already started a Goal", SKILL)
        self.assertIn("does not create, start, pause, or modify a Goal", REFERENCE)

    def test_cross_provider_first_use_requires_saved_agent_and_new_task(self) -> None:
        self.assertIn("A first-use inline request cannot switch providers", REFERENCE)
        self.assertIn("existing authenticated Codex-compatible provider", REFERENCE)
        self.assertIn("a new task that loads the agent", REFERENCE)
        self.assertIn("Never ask the user to paste an API key", REFERENCE)

    def test_prompt_preference_is_not_reported_as_exact_routing(self) -> None:
        self.assertIn("unverified prompt preference", SKILL)
        self.assertIn("Do not report a model as used before Codex accepts", SKILL)
        self.assertIn("requested child model was not used", SKILL)
        self.assertIn("Child prose alone is diagnostic, not routing proof", SKILL)
        self.assertIn("ordinary executor choice is a routing preference", SKILL)
        self.assertIn("explicitly requires that executor route", SKILL)

    def test_saved_scope_is_complete_and_persists_advisor_none(self) -> None:
        self.assertIn("complete saved team", SKILL)
        self.assertIn("missing namespaced advisor means saved `advisor: none`", SKILL)
        self.assertIn("Do not merge in a personal advisor", SKILL)
        self.assertIn("Treat each scope as a complete team record", REFERENCE)
        self.assertIn("Fall back to a personal team only when no managed project", REFERENCE)

    def test_saved_role_removal_does_not_require_seat_values(self) -> None:
        self.assertIn("configuration-control requests", SKILL)
        self.assertIn("do not require executor/advisor values", SKILL)
        self.assertIn("resolves a cross-scope name collision", SKILL)

    def test_saved_roles_do_not_touch_root_or_global_agent_limits(self) -> None:
        self.assertIn("must not modify the root model", SKILL)
        self.assertIn("`.codex/config.toml`", SKILL)
        self.assertIn("global agent limits", SKILL)
        self.assertIn("trusted workspace or repository root", SKILL)
        self.assertIn("load only in a new task", SKILL)

    def test_persistence_contract_covers_crash_recovery_and_windows(self) -> None:
        self.assertIn("journals multi-file writes", SKILL)
        self.assertIn("without recording config contents or credentials", SKILL)
        self.assertIn("approved `--apply` recovers", SKILL)
        self.assertIn("custom NTFS security descriptors", SKILL)
        self.assertIn("Python 3.11+", SKILL)
        self.assertIn("--remove-saved-roles", SKILL)
        self.assertIn("Legacy migration always requires the user to approve", SKILL)


if __name__ == "__main__":
    unittest.main()
