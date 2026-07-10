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
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        marketplace = json.loads(
            (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["name"], "codex-orchestration")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["version"], "0.3.0")
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

    def test_explicit_invocation_metadata_is_consistent(self) -> None:
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("$codex-orchestration", metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("/codex-orchestration executor:", readme)
        self.assertIn("$codex-orchestration:codex-orchestration executor:", readme)
        self.assertIn("advisor: none", readme)
        self.assertIn(
            "codex plugin add codex-orchestration@codex-orchestration",
            readme,
        )

    def test_starter_prompts_fit_codex_limits(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertGreaterEqual(len(prompts), 1)
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertTrue(prompt.strip())
            self.assertLessEqual(len(prompt), 128, prompt)

        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        prompt_line = next(
            line for line in metadata.splitlines() if "default_prompt:" in line
        )
        yaml_prompt = prompt_line.split(":", 1)[1].strip().strip('"')
        self.assertIn("$codex-orchestration", yaml_prompt)
        self.assertLessEqual(len(yaml_prompt), 128)

    def test_ci_runs_the_real_plugin_lifecycle_smoke(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        smoke = REPO_ROOT / "tests" / "plugin_lifecycle_smoke.py"

        self.assertTrue(smoke.is_file())
        self.assertIn("python tests/plugin_lifecycle_smoke.py", workflow)
        self.assertIn("@openai/codex@0.142.5", workflow)
        smoke_text = smoke.read_text(encoding="utf-8")
        self.assertIn('OLD_VERSION = "0.2.0"', smoke_text)
        self.assertIn('assert_equal(current_version, "0.3.0"', smoke_text)
        self.assertIn("configure_orchestration.py", smoke_text)
        self.assertIn('"marketplace",\n                    "upgrade"', smoke_text)
        self.assertIn("ThreadingHTTPServer", smoke_text)

    def test_current_session_model_is_the_only_orchestrator(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("current Codex task model as orchestrator", skill)
        self.assertIn("The current task model remains the root", skill)
        self.assertIn(
            "model you selected when you started the Codex task is already the orchestrator",
            readme,
        )
        self.assertNotIn("--orchestrator-model", skill)
        self.assertNotIn("--orchestrator-model", readme)

    def test_advisor_protocol_is_bounded_and_root_only(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("PLAN_APPROVED", skill)
        self.assertIn("PLAN_REVISE", skill)
        self.assertNotIn("ADVISOR_BLOCKED", skill)
        self.assertIn("Report only to the root", skill)
        self.assertIn("one confirmation review", skill)
        self.assertIn("advisor unavailable", skill)

    def test_readme_uses_plain_codex_language_and_simple_flow(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("ORCHESTRATOR (already selected)", readme)
        self.assertIn("ADVISOR checks plan", readme)
        self.assertIn("EXECUTOR receives", readme)
        self.assertIn("verifies and answers you", readme)
        self.assertIn("unverified prompt preference", readme)
        self.assertIn("requested child model was not used", readme)
        self.assertIn("Send that full line with your choices and task", readme)
        self.assertNotIn("Native Codex", readme)
        self.assertNotIn("CURRENT SESSION MODEL", readme)
        self.assertIn("missing project advisor file means saved `advisor: none`", readme)
        self.assertIn("remove saved roles for this project", readme)
        self.assertIn("Python 3.11+", readme)

    def test_cross_provider_copy_names_the_real_protocol_boundary(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("config-file/config-advanced#custom-model-providers", readme)
        self.assertIn("Responses wire protocol", readme)
        self.assertIn("Anthropic Messages", readme)
        self.assertIn("Amazon Bedrock", readme)

    def test_savings_copy_distinguishes_allowance_from_raw_tokens(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("illustrative reduction", readme)
        self.assertIn("64%", readme)
        self.assertIn("It does not remove 65% of the raw tokens", readme)
        self.assertIn("subagent workflows use more total tokens", readme)
        self.assertNotIn("64.6%", readme)
        self.assertNotIn("61.2%", readme)

    def test_docs_use_namespaced_standalone_agents(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (
            SKILL_ROOT / "references" / "providers-and-models.md"
        ).read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        public_docs = "\n".join((skill, reference, readme))

        self.assertIn("codex_orchestration_executor", public_docs)
        self.assertIn("codex_orchestration_advisor", public_docs)
        self.assertIn("codex-orchestration-executor.toml", public_docs)
        self.assertIn("codex-orchestration-advisor.toml", public_docs)
        self.assertNotIn("agents/executor-model.toml", public_docs)
        self.assertNotIn("agents/advisor-model.toml", public_docs)
        self.assertNotIn("[agents.executor]", public_docs)
        self.assertNotIn("[agents.advisor]", public_docs)

    def test_update_and_uninstall_keep_residual_state_explicit(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("does not guess what your root settings were", readme)
        self.assertIn("complete current choices", readme)
        self.assertIn("migrate legacy", readme)
        self.assertIn("review those manually", readme)
        self.assertIn("Uninstalling the plugin itself does not silently delete", readme)
        self.assertIn("migration backups are intentionally never", readme)


if __name__ == "__main__":
    unittest.main()
