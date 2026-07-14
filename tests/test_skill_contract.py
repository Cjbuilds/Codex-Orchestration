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
NATIVE_SCRIPT = (
    SKILL_ROOT / "scripts" / "configure_native_routing.py"
).read_text(encoding="utf-8")
ROUTING_STATE = (SKILL_ROOT / "scripts" / "routing_state.py").read_text(
    encoding="utf-8"
)


class SkillContractTests(unittest.TestCase):
    def test_public_controls_are_simple_and_setup_is_persistent(self) -> None:
        self.assertIn("setup executor: GPT-5.6 Luna Extra High", SKILL)
        self.assertIn("setup planner: Claude Fable 5 High", SKILL)
        self.assertIn("/codex-orchestration status", SKILL)
        self.assertIn("/codex-orchestration disable", SKILL)
        self.assertIn("current-task override", SKILL)
        self.assertIn("no longer needs to invoke this skill", SKILL)

    def test_current_task_model_is_the_only_orchestrator(self) -> None:
        self.assertIn("already the orchestrator", SKILL)
        self.assertIn("Never ask the user to configure another one", SKILL)
        self.assertIn("The current task model remains the root", SKILL)
        self.assertIn("never change the root model", SKILL)
        self.assertNotIn("--orchestrator-model", SKILL)

    def test_advisor_omission_means_none(self) -> None:
        self.assertIn("omitted advisor means `advisor: none`", SKILL)
        self.assertIn("Do not ask separate planner or advisor questions", SKILL)
        self.assertIn("Omission persists `advisor: none`", SKILL)
        self.assertIn("Advisor omission means none", NATIVE_SCRIPT)

    def test_planner_omission_means_root(self) -> None:
        self.assertIn("omitted planner means the current root model plans", SKILL)
        self.assertIn("Planner omission persists no Planner route", SKILL)
        self.assertIn("no Planner route is configured, so the root plans", SKILL)

    def test_explicit_seat_labels_cannot_be_reinterpreted(self) -> None:
        self.assertIn("Explicit seat labels are authoritative", SKILL)
        self.assertIn(
            "never reinterpret a supplied `planner:` model as an Advisor", SKILL
        )
        self.assertIn("`planner:` configures only Planner", SKILL)
        self.assertIn("`advisor:` configures only Advisor", SKILL)
        self.assertIn("`executor:` configures only Executor", SKILL)
        self.assertIn("Fable Planner uses `create_plan` and `revise_plan`", SKILL)
        self.assertIn("Fable Advisor uses `review_plan`", SKILL)
        self.assertIn("Advisor: none", SKILL)

    def test_codex_still_decides_when_to_delegate(self) -> None:
        self.assertIn("Codex decides whether a plan helps", SKILL)
        self.assertIn("force a spawn or fixed worker count", SKILL)
        self.assertIn("Keep simple, tightly coupled", SKILL)
        self.assertIn("explicit `no subagents` instruction always wins", SKILL)

    def test_native_policy_manages_only_required_fields(self) -> None:
        for field in (
            "hide_spawn_agent_metadata",
            "tool_namespace",
            "multi_agent_mode_hint_text",
            "usage_hint_text",
        ):
            self.assertIn(field, SKILL)
            self.assertIn(field, NATIVE_SCRIPT)
        self.assertIn('`tool_namespace = "agents"`', SKILL)
        self.assertIn("reserved `collaboration.spawn_agent` schema", SKILL)
        self.assertIn("Do not add `enabled = true`", SKILL)
        self.assertIn('ROUTING_TOOL_NAMESPACE = "agents"', ROUTING_STATE)

    def test_native_config_uses_codex_app_server(self) -> None:
        self.assertIn("Codex App Server's `config/read`", SKILL)
        self.assertIn("`config/batchWrite`", SKILL)
        self.assertIn('"initialize",', NATIVE_SCRIPT)
        self.assertIn('"config/read"', NATIVE_SCRIPT)
        self.assertIn('"config/batchWrite"', NATIVE_SCRIPT)
        self.assertIn('"expectedVersion"', NATIVE_SCRIPT)
        self.assertIn('"reloadUserConfig"', NATIVE_SCRIPT)

    def test_every_different_route_uses_a_non_history_fork(self) -> None:
        self.assertIn('fork_turns = "none"', SKILL)
        self.assertIn("Never use the default `all`", SKILL)
        self.assertIn("Full-history forks inherit the root model", SKILL)
        self.assertIn('fork_turns = "none"', NATIVE_SCRIPT)
        self.assertIn('Never use fork_turns = "all"', NATIVE_SCRIPT)

    def test_root_and_child_boundaries_are_in_the_saved_mode(self) -> None:
        self.assertIn("If you are the root task model", NATIVE_SCRIPT)
        self.assertIn("If you are a spawned child", NATIVE_SCRIPT)
        self.assertIn("never spawn descendants", NATIVE_SCRIPT)
        self.assertIn("Explicit user instructions win", NATIVE_SCRIPT)
        self.assertIn("This policy does not create or change a Goal", NATIVE_SCRIPT)

    def test_mixed_client_compatibility_is_capability_detected(self) -> None:
        self.assertIn("capability-tests the complete four-field preset", SKILL)
        self.assertIn("isolated `CODEX_HOME`", REFERENCE)
        self.assertIn("--allow-incompatible-client", SKILL)
        self.assertIn("Disable must remain available", SKILL)
        self.assertIn("supports_native_policy", NATIVE_SCRIPT)

    def test_setup_is_reversible_and_conflict_safe(self) -> None:
        self.assertIn("pre-setup values", SKILL)
        self.assertIn("--replace-existing-policy", SKILL)
        self.assertIn("Refuse to erase managed fields", SKILL)
        self.assertIn("persistence fails after config apply", REFERENCE)
        self.assertIn("automatic rollback", NATIVE_SCRIPT)
        self.assertIn("rejects a stale user-layer version", REFERENCE)

    def test_cross_provider_requires_existing_provider_and_custom_agent(self) -> None:
        self.assertIn("already authenticated Codex-compatible provider", SKILL)
        self.assertIn("loaded custom agent", SKILL)
        self.assertIn("Never create provider definitions", SKILL)
        self.assertIn("Never create provider definitions", REFERENCE)
        self.assertIn("Responses wire protocol", REFERENCE)
        self.assertIn("Anthropic Messages", REFERENCE)
        self.assertIn("--personal-route-names", SKILL)
        self.assertIn("verify_agent_routes", NATIVE_SCRIPT)
        self.assertIn("same-name project role", SKILL)

    def test_arbitrary_roles_are_native_bounded_and_user_owned(self) -> None:
        self.assertIn("## Create arbitrary custom roles", SKILL)
        self.assertIn("<trusted-project>/.codex/agents/<role-name>.toml", SKILL)
        self.assertIn("`~/.codex/agents/<role-name>.toml`", SKILL)
        self.assertIn("model_provider", SKILL)
        self.assertIn("current task permission mode by default", SKILL)
        self.assertIn("never bypasses the parent task's authority", SKILL)
        self.assertIn("Refuse symlinked paths", SKILL)
        self.assertIn("Arbitrary native roles are user-owned", SKILL)
        self.assertIn("start a new task after creation", SKILL)
        self.assertIn("root orchestrator owns every handoff", SKILL)

    def test_custom_workflows_preserve_goal_ownership(self) -> None:
        self.assertIn("researcher -> reviewer -> writer", SKILL)
        self.assertIn("leave Goal lifecycle and limits under Codex's normal Goal controls", SKILL)
        self.assertIn("does not silently create, pause, resume, or clear it", SKILL)

    def test_fable_is_a_bundled_root_only_planner_or_advisor(self) -> None:
        self.assertIn("advisor: Claude Fable 5 High", SKILL)
        self.assertIn("planner: Claude Fable 5 High", SKILL)
        self.assertIn("Omission or `Auto` means `High`", SKILL)
        self.assertIn("`Ultra` is a user-facing alias", SKILL)
        self.assertIn("--advisor-fable --advisor-effort <normalized-effort>", SKILL)
        self.assertIn("--planner-fable --planner-effort <normalized-effort>", SKILL)
        self.assertIn("built-in cross-provider Planner or Advisor exception", SKILL)
        self.assertIn("All bundled variants are disabled by default", SKILL)
        self.assertIn("first-party Pro or Max account", SKILL)
        self.assertIn("never extracts a token", SKILL)
        self.assertIn("runtime `modelUsage` to contain the pinned `claude-fable-5`", SKILL)
        self.assertIn("explicit exact helper allowlist", SKILL)
        self.assertIn("unknown additional or missing primary model", SKILL)
        self.assertIn("`create_plan`", SKILL)
        self.assertIn("`revise_plan`", SKILL)
        self.assertIn("`review_plan`", SKILL)
        self.assertIn("use the exact name `Claude Fable 5`", SKILL)
        self.assertIn("do not expose or restate Claude account-plan metadata", SKILL)
        self.assertIn("MCP requests do not carry caller identity", SKILL)
        self.assertIn("Never describe the caller boundary as engine-enforced", SKILL)

    def test_direct_routes_are_guarded_to_the_root_provider(self) -> None:
        self.assertIn("Direct model overrides keep the root's provider", SKILL)
        self.assertIn("target model is on the same provider", NATIVE_SCRIPT)
        self.assertIn("require a custom agent that pins model_provider", NATIVE_SCRIPT)

    def test_advisor_is_bounded_root_only_and_failure_is_not_approval(self) -> None:
        self.assertIn("PLAN_APPROVED", SKILL)
        self.assertIn("PLAN_REVISE", SKILL)
        self.assertIn("report only to the root", SKILL)
        self.assertIn("contact Executors", SKILL)
        self.assertIn("it never counts as approval", SKILL)
        self.assertIn("Never exceed five total Advisor reviews", SKILL)
        self.assertIn("NOT_ADVISOR_APPROVED", SKILL)
        self.assertNotIn("at most one confirmation pass", SKILL)

    def test_planner_advisor_loop_is_versioned_and_fail_closed(self) -> None:
        self.assertIn("canonical plan version", SKILL)
        self.assertIn("stable IDs", SKILL)
        self.assertIn("compact cumulative findings ledger", SKILL)
        self.assertIn("Reject stale source versions", SKILL)
        self.assertIn("same direct model ID", SKILL)
        self.assertIn("For both persistent setup and task-local overrides", SKILL)
        self.assertIn("explicitly made that seat best-effort", SKILL)
        self.assertIn("An unavailable Executor may leave work with the root", SKILL)
        self.assertIn("Planner and Advisor never contact one another directly", SKILL)

    def test_route_reporting_is_truthful(self) -> None:
        self.assertIn("native policy installed", SKILL)
        self.assertIn("route accepted", SKILL)
        self.assertIn("used and confirmed", SKILL)
        self.assertIn("only when the client explicitly exposes", SKILL)
        self.assertIn("inherited root — requested child model was not used", SKILL)
        self.assertIn("Child prose claiming a model name is not proof", SKILL)
        self.assertIn("Never report a prompt preference", SKILL)

    def test_goal_permissions_and_limits_remain_codex_owned(self) -> None:
        self.assertIn("create or change Goal state", SKILL)
        self.assertIn("weaken approvals or permissions", SKILL)
        self.assertIn("global agent limits", SKILL)
        self.assertIn("does not create, start, pause, clear, or alter a Goal", REFERENCE)

    def test_savings_claim_is_credits_not_raw_tokens(self) -> None:
        self.assertIn("model-weighted credit calculation", SKILL)
        self.assertIn("about 64% fewer credits", SKILL)
        self.assertIn("Never call that 65% fewer raw tokens", SKILL)
        self.assertIn("Fast service tier", SKILL)
        self.assertIn("five-hour", REFERENCE)
        self.assertIn("weekly limits", REFERENCE)


if __name__ == "__main__":
    unittest.main()
