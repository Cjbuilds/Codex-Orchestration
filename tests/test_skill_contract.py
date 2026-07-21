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
README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
REFERENCE = (SKILL_ROOT / "references" / "providers-and-models.md").read_text(
    encoding="utf-8"
)
EXTERNAL_REFERENCE = (SKILL_ROOT / "references" / "external-models.md").read_text(
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
        self.assertIn("/codex-orchestration repair", SKILL)
        self.assertIn("/codex-orchestration disable", SKILL)
        self.assertIn("/codex-orchestration --update", SKILL)
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
        self.assertIn("`designer:` configures only Designer", SKILL)
        self.assertIn("never reinterpret", SKILL)
        self.assertIn("Fable Planner uses `create_plan` and `revise_plan`", SKILL)
        self.assertIn("Fable Advisor uses `review_plan`", SKILL)
        self.assertIn("Advisor: none", SKILL)

    def test_ready_role_selection_uses_concise_activation_confirmation(self) -> None:
        expected = """```text
Planner — Fable 5 high: Activated
Designer — Kimi K3: Activated
Executor — GPT-5.6 Sol high: Activated
```"""

        self.assertIn(expected, SKILL)
        self.assertIn("one plain line per explicitly supplied model-bearing seat", SKILL)
        self.assertIn("preserve the user's seat order", SKILL)
        self.assertIn("Do not print omitted, `none`, or implicit-root seats", SKILL)
        self.assertIn("Use `Activated` only after that exact route is locally ready", SKILL)
        self.assertIn("lifecycle state and next action instead of `Activated`", SKILL)
        self.assertIn(
            "activation confirmation preserves the supplied `Fable 5` label", SKILL
        )

    def test_designer_is_optional_bounded_and_first_class(self) -> None:
        self.assertIn("omitted designer means `designer: none`", SKILL)
        self.assertIn("--designer-model", SKILL)
        self.assertIn("--designer-effort", SKILL)
        self.assertNotIn("--designer-agent", SKILL)
        self.assertIn("Persistent Designer accepts only a direct same-provider model", SKILL)
        self.assertIn("task-local External Model role named `designer`", SKILL)
        self.assertIn("Designer may edit only explicitly delegated design artifacts", SKILL)
        self.assertIn("never revises the canonical plan", SKILL)
        self.assertIn("Designer may use the same model as another seat", SKILL)
        self.assertIn("Designer: none", SKILL)
        self.assertIn('"designer"', ROUTING_STATE)

    def test_bare_external_designer_label_enters_secure_role_lifecycle(self) -> None:
        self.assertIn("Designer: Kimi K3", SKILL)
        self.assertIn("explicit External Model seat assignment", SKILL)
        self.assertIn("Explicit External Model seat labels are the exception", SKILL)
        self.assertIn("never pass it to `--designer-model`", SKILL)
        self.assertIn("inspect `external status` first", SKILL)
        self.assertIn("role `designer`", SKILL)
        self.assertIn("preview and apply `connect`", SKILL)
        self.assertIn("RESTART_REQUIRED", SKILL)
        self.assertIn("then use\n  sealed `invoke`", SKILL)
        self.assertIn("Never execute an External Model role with `agents.spawn_agent`", SKILL)
        self.assertIn("`resolve` remains a read-only diagnostic", SKILL)
        self.assertNotIn("delegate only to the returned agent name", SKILL)
        self.assertIn("do not report the requested external seat as unavailable", SKILL)
        self.assertIn("never authorizes Gate 0 billing", SKILL)
        self.assertIn("Never overwrite, repair, disconnect, or substitute", SKILL)
        self.assertIn("preserve the original task", SKILL)
        self.assertIn("must not be persisted in native routing state", SKILL)
        self.assertIn("report its exact lifecycle state and next action, not unavailable", SKILL)
        self.assertIn("only External Model seat labels", SKILL)
        self.assertIn("also supplies any native seat", SKILL)
        self.assertIn("collect a missing Executor", SKILL)
        self.assertIn("never modifies or removes a pre-existing provider entry", SKILL)
        self.assertIn("never modifies, replaces, or removes a pre-existing provider entry", EXTERNAL_REFERENCE)

    def test_natural_kimi_availability_question_uses_read_only_external_status(self) -> None:
        self.assertIn("is Kimi available to use as Designer?", SKILL)
        self.assertIn("Implicit invocation is discovery, not mutation authority", SKILL)
        self.assertIn("run `external status`", SKILL)
        self.assertIn(
            "Never infer External Model availability from the currently exposed MCP or subagent",
            SKILL,
        )
        self.assertIn("A visible Fable tool is not an exhaustive provider", SKILL)
        self.assertIn("`supported`", SKILL)
        self.assertIn("`configured`", SKILL)
        self.assertIn("`callable now`", SKILL)
        self.assertIn("`locally ready`", SKILL)
        self.assertIn("read-only `resolve` succeeds", SKILL)
        self.assertIn("a sealed `invoke` has successfully attested", SKILL)
        self.assertIn("Read-only discovery must report this as unconfirmed", SKILL)
        self.assertIn("never authorizes configuration, credentials, or spend", README)

    def test_plugin_update_is_canonical_non_destructive_and_restart_bound(self) -> None:
        self.assertIn("## Update the plugin", SKILL)
        self.assertIn("canonical Git marketplace", SKILL)
        self.assertIn("Never run `plugin remove`", SKILL)
        self.assertIn("Do not\nwrap these commands in a custom downloader", SKILL)
        self.assertIn("inspect/touch routing, chats, or sessions", SKILL)
        self.assertIn("restart Codex", SKILL)
        self.assertIn("Desktop and start a new task", SKILL)
        self.assertIn("codex plugin list --json", SKILL)
        self.assertIn("codex plugin marketplace upgrade", SKILL)
        self.assertIn("codex plugin add", SKILL)
        self.assertNotIn("codex plugin remove", SKILL)

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

    def test_routing_repair_is_narrow_and_restart_aware(self) -> None:
        self.assertIn("--repair --apply", SKILL)
        self.assertIn("both live hints to retain the plugin\nownership marker", SKILL)
        self.assertIn("Fable launcher", SKILL)
        self.assertIn("leaves the original\nrestore snapshot", SKILL)
        self.assertIn("preserves\na concurrent config or saved-state edit", SKILL)
        self.assertIn("fully quit and reopen Codex", SKILL)
        self.assertIn("Do not\nrequest re-authentication", SKILL)
        self.assertIn("def _repair(", NATIVE_SCRIPT)
        self.assertIn("reload_user_config=True", NATIVE_SCRIPT)
        self.assertIn("newer edit was preserved", NATIVE_SCRIPT)
        self.assertIn("pre-repair", NATIVE_SCRIPT)
        self.assertIn("managed hints were restored", NATIVE_SCRIPT)
        self.assertIn("never reads or changes credentials", REFERENCE)

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
        self.assertIn("Never create an unreviewed provider definition", SKILL)
        self.assertIn("Never create an unreviewed provider definition", REFERENCE)
        self.assertIn("Responses wire protocol", REFERENCE)
        self.assertIn("Anthropic Messages", REFERENCE)
        self.assertIn("--personal-route-names", SKILL)
        self.assertIn("verify_agent_routes", NATIVE_SCRIPT)
        self.assertIn("same-name project role", SKILL)

    def test_external_models_are_nonpicker_nonsecret_and_fail_closed(self) -> None:
        missing_auth_message = (
            "External provider authentication is required. Do not paste the API key "
            "into this chat. Run the displayed enrollment command in a trusted local "
            "terminal; its hidden local prompt stores the key in your operating-system "
            "credential store. Tell me when that command succeeds."
        )
        self.assertIn("## External Model roles", SKILL)
        self.assertIn("Desktop model picker", SKILL)
        self.assertIn("Never write top-level", SKILL)
        self.assertIn(missing_auth_message, SKILL)
        self.assertIn("When authentication is missing", SKILL)
        self.assertIn("Never ask the user to paste, upload, dictate", SKILL)
        self.assertIn("Do not run the enrollment command for", SKILL)
        self.assertIn("separate explicit approval for `--acknowledge-billing`", SKILL)
        self.assertIn("one personal provider-pinned custom-agent variant", SKILL)
        self.assertIn("model self-identification", SKILL)
        self.assertIn("CLI_CHANGED", SKILL)
        self.assertIn("RECOVERY_REQUIRED", SKILL)
        self.assertIn("never reads or deletes chats", EXTERNAL_REFERENCE)
        self.assertIn("command-backed auth", EXTERNAL_REFERENCE)
        self.assertIn("moonshotai/kimi-k3", EXTERNAL_REFERENCE)
        self.assertIn("currently documents only `max` reasoning", EXTERNAL_REFERENCE)
        self.assertIn("rejects `xhigh`, `high`, `medium`, `low`", EXTERNAL_REFERENCE)
        self.assertIn("Every installation starts unqualified", EXTERNAL_REFERENCE)

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
