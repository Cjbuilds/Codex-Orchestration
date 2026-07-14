from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-orchestration"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "codex-orchestration"


class PackagingTests(unittest.TestCase):
    def test_plugin_marketplace_and_skill_names_are_aligned(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["name"], "codex-orchestration")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["version"], "0.5.0")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertRegex(
            manifest["version"],
            r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$",
        )
        self.assertEqual(marketplace["name"], "codex-orchestration")
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "codex-orchestration")
        self.assertEqual(entry["source"]["path"], "./plugins/codex-orchestration")
        self.assertRegex(skill, r"(?m)^name: codex-orchestration$")

    def test_native_and_custom_configurators_are_packaged(self) -> None:
        native = SKILL_ROOT / "scripts" / "configure_native_routing.py"
        custom = SKILL_ROOT / "scripts" / "configure_orchestration.py"
        routing_state = SKILL_ROOT / "scripts" / "routing_state.py"
        self.assertTrue(native.is_file())
        self.assertTrue(custom.is_file())
        self.assertTrue(routing_state.is_file())
        self.assertIn("config/batchWrite", native.read_text(encoding="utf-8"))
        self.assertIn("validate_routing_state", routing_state.read_text(encoding="utf-8"))
        self.assertIn("Standalone custom agent", custom.read_text(encoding="utf-8"))

    def test_fable_mcp_is_packaged_and_disabled_until_selected(self) -> None:
        mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        servers = mcp["mcpServers"]
        self.assertEqual(
            set(servers),
            {
                "fable-advisor-python3",
                "fable-advisor-python",
                "fable-advisor-py",
            },
        )
        for server in servers.values():
            self.assertFalse(server["enabled"])
            self.assertEqual(server["cwd"], ".")
            self.assertIn("fable_advisor_mcp.py", server["args"][-1])
        self.assertTrue((SKILL_ROOT / "scripts" / "fable_advisor_mcp.py").is_file())

    def test_explicit_invocation_metadata_is_consistent(self) -> None:
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("$codex-orchestration", metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("/codex-orchestration setup executor:", readme)
        self.assertIn("GPT-5.6 Luna Extra High", readme)
        self.assertIn("/codex-orchestration create project role:", readme)
        self.assertIn("/codex-orchestration status", readme)
        self.assertIn("/codex-orchestration disable", readme)
        self.assertIn("codex plugin add codex-orchestration@codex-orchestration", readme)

    def test_starter_prompts_fit_codex_limits(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertGreaterEqual(len(prompts), 1)
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertTrue(prompt.strip())
            self.assertLessEqual(len(prompt), 128, prompt)

        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        prompt_line = next(
            line for line in metadata.splitlines() if "default_prompt:" in line
        )
        yaml_prompt = prompt_line.split(":", 1)[1].strip().strip('"')
        self.assertTrue(yaml_prompt.startswith("Use $codex-orchestration"))
        self.assertLessEqual(len(yaml_prompt), 128)

    def test_ci_runs_dual_version_plugin_lifecycle(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        smoke = REPO_ROOT / "tests" / "plugin_lifecycle_smoke.py"

        self.assertTrue(smoke.is_file())
        self.assertIn("python tests/plugin_lifecycle_smoke.py", workflow)
        self.assertIn("@openai/codex@0.142.5", workflow)
        self.assertIn("@openai/codex@0.144.1", workflow)
        smoke_text = smoke.read_text(encoding="utf-8")
        self.assertIn('OLD_VERSION = "0.3.0"', smoke_text)
        self.assertIn('NEW_VERSION = "0.5.0"', smoke_text)
        self.assertIn("configure_native_routing.py", smoke_text)
        self.assertIn("configure_orchestration.py", smoke_text)
        self.assertIn("fable_advisor_mcp.py", smoke_text)
        self.assertIn('"method": "initialize"', smoke_text)
        self.assertIn('"method": "tools/list"', smoke_text)
        self.assertIn('"marketplace",\n                    "upgrade"', smoke_text)

    def test_current_session_model_is_the_only_orchestrator(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("already the orchestrator", skill)
        self.assertIn("The current task model remains the root", skill)
        self.assertIn("model selected for the Codex task remains in charge", readme)
        self.assertIn("CODEX COORDINATES THE WORK", readme)
        self.assertIn("Codex remains the root orchestrator", readme)
        self.assertNotIn("--orchestrator-model", skill + readme)

    def test_readme_explains_policy_guided_route_without_overpromising(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        routing_state = (SKILL_ROOT / "scripts" / "routing_state.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("Other providers must already be configured and authenticated", readme)
        self.assertIn("never creates credentials or bypasses permissions", readme)
        self.assertIn("Codex decides when delegation or parallel work is useful", readme)
        self.assertIn("Fable 5 is the bundled cross-provider exception", readme)
        self.assertIn('ROUTING_TOOL_NAMESPACE = "agents"', routing_state)

    def test_ascii_and_role_copy_are_plain_and_root_centered(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("CODEX COORDINATES THE WORK", readme)
        self.assertIn("PLANNER CREATES THE FIRST PLAN", readme)
        self.assertIn("ADVISOR REVIEWS IT", readme)
        self.assertIn("EXECUTORS IMPLEMENT IT", readme)
        self.assertIn("CODEX TESTS & DELIVERS", readme)
        self.assertNotIn("SOL IS THE ORCHESTRATOR", readme)

    def test_readme_leads_with_the_product_before_installation(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        what = readme.index("## What is it?")
        diagram = readme.index("## How it works")
        value = readme.index("## Why use it?")
        install = readme.index("## Install")
        self.assertLess(what, diagram)
        self.assertLess(diagram, value)
        self.assertLess(value, install)

    def test_advisor_protocol_is_bounded_and_root_only(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("PLAN_APPROVED", skill)
        self.assertIn("PLAN_REVISE", skill)
        self.assertIn("report only to the root", skill)
        self.assertIn("Never exceed five total Advisor reviews", skill)
        self.assertIn("compact cumulative findings ledger", skill)
        self.assertNotIn("at most one confirmation pass", skill)
        self.assertIn("it never counts as approval", skill)
        self.assertIn("Current MCP requests do not carry caller identity", skill)
        self.assertIn("caller isolation is instruction-enforced", skill)

    def test_cross_provider_copy_names_the_real_protocol_boundary(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Models already available through Codex", readme)
        self.assertIn("an existing authenticated, compatible provider", readme)
        self.assertIn("do not need to add an Anthropic API key to Codex", readme)
        self.assertIn("`.codex/agents/`", readme)
        self.assertIn("`~/.codex/agents/`", readme)
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("explicit exact helper allowlist", skill)
        self.assertIn("unknown additional or missing primary model", skill)

    def test_speed_and_limit_copy_is_clear_and_qualified(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("up to 2x faster on suitable tasks", readme)
        self.assertIn("limits about 40% less often", readme)
        self.assertIn("speed and limit figures are targets, not guarantees", readme)

    def test_fable_is_the_primary_quick_start(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## Quick start", readme)
        self.assertIn(
            "planner: Claude Fable 5 High",
            readme,
        )
        self.assertIn("Fable defaults to **High**", readme)
        self.assertIn("**Low**, **Medium**, **High**, **XHigh**, or **Max**", readme)
        self.assertIn("**Ultra** is accepted as an alias for Max", readme)
        self.assertIn("advisor: GPT-5.6 Sol High", readme)

    def test_update_and_uninstall_remove_managed_state_explicitly(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("/codex-orchestration status", readme)
        self.assertIn("`disable` restores the routing values", readme)
        self.assertIn("does not delete user-owned custom roles", readme)
        self.assertIn("Review and remove any user-owned custom roles separately", readme)


if __name__ == "__main__":
    unittest.main()
