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
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertRegex(manifest["version"], r"^0\.5\.0(?:\+codex\.\d+)?$")
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
        self.assertTrue(native.is_file())
        self.assertTrue(custom.is_file())
        self.assertIn("config/batchWrite", native.read_text(encoding="utf-8"))
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
        self.assertIn("$codex-orchestration:codex-orchestration", readme)
        self.assertIn("/codex-orchestration status", readme)
        self.assertIn("/codex-orchestration disable", readme)
        self.assertIn("advisor: none", readme)
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
        self.assertIn('"marketplace",\n                    "upgrade"', smoke_text)

    def test_current_session_model_is_the_only_orchestrator(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("already the orchestrator", skill)
        self.assertIn("The current task model remains the root", skill)
        self.assertIn("model you select when you start a Codex task is already", readme)
        self.assertIn("SOL IS THE ORCHESTRATOR", readme)
        self.assertNotIn("--orchestrator-model", skill + readme)

    def test_readme_explains_config_only_route_without_overpromising(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        native = (SKILL_ROOT / "scripts" / "configure_native_routing.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("Is config alone enough?", readme)
        self.assertIn("Neither says “use Luna.”", readme)
        self.assertIn("no global `executor_model = ...`", readme)
        self.assertIn("Same-provider config routing is strong tool-level guidance", readme)
        self.assertIn('sets `tool_namespace = "agents"`', readme)
        self.assertIn("reserved `collaboration.spawn_agent` schema", readme)
        self.assertIn("does **not** force `enabled = true`", readme)
        self.assertIn('ROUTING_TOOL_NAMESPACE = "agents"', native)

    def test_ascii_and_role_copy_are_plain_and_root_centered(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("SOL IS THE ORCHESTRATOR", readme)
        self.assertIn("ADVISOR checks the plan", readme)
        self.assertIn("LUNA / TERRA executor(s)", readme)
        self.assertIn("SOL REVIEWS AND INTEGRATES", readme)
        self.assertIn("Advisor: the second opinion", readme)
        self.assertIn("Executor: the builder", readme)
        self.assertNotIn("Native Codex", readme)
        self.assertNotIn("CURRENT SESSION MODEL", readme)

    def test_advisor_protocol_is_bounded_and_root_only(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("PLAN_APPROVED", skill)
        self.assertIn("PLAN_REVISE", skill)
        self.assertIn("report only to the root", skill)
        self.assertIn("at most one confirmation pass", skill)
        self.assertIn("`advisor unavailable`, never approval", skill)

    def test_cross_provider_copy_names_the_real_protocol_boundary(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("config-file/config-advanced#custom-model-providers", readme)
        self.assertIn("Responses wire protocol", readme)
        self.assertIn("Anthropic Messages", readme)
        self.assertIn("Amazon Bedrock", readme)
        self.assertIn("configured and tested", readme)
        self.assertIn("<personal-id>.toml", readme)
        self.assertIn("same-name project collision", readme)

    def test_savings_copy_distinguishes_credits_from_raw_tokens(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("illustrative reduction", readme)
        self.assertIn("64%", readme)
        self.assertIn("cannot promise 65% fewer raw tokens", readme)
        self.assertIn("model-weighted Codex credits", readme)
        self.assertIn("Multi-agent work can use more raw tokens", readme)
        self.assertIn("shared five-hour", readme)
        self.assertIn("weekly allowances", readme)

    def test_update_and_uninstall_remove_managed_state_explicitly(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("/codex-orchestration status", readme)
        self.assertIn("First disable the persistent policy", readme)
        self.assertIn("restores the values that existed before setup", readme)
        self.assertIn("does not silently delete a policy", readme)
        self.assertIn("versions 0.1–0.3", readme)


if __name__ == "__main__":
    unittest.main()
