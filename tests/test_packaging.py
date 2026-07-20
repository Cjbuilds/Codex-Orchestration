from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-orchestration"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "codex-orchestration"


class PackagingTests(unittest.TestCase):
    def test_pull_request_review_attestation_is_strict_json(self) -> None:
        template = (REPO_ROOT / ".github/pull_request_template.md").read_text(
            encoding="utf-8"
        )
        start = "<!-- codex-review-attestation:start -->"
        end = "<!-- codex-review-attestation:end -->"
        self.assertEqual(template.count(start), 1)
        self.assertEqual(template.count(end), 1)
        attestation = json.loads(template.split(start, 1)[1].split(end, 1)[0])

        self.assertEqual(
            set(attestation),
            {
                "schema",
                "risk_tier",
                "repository",
                "base_branch",
                "reviewed_head_sha",
                "reviewer_identity",
                "reviewer_route",
                "threat_model",
                "negative_test_evidence",
                "findings_disposition",
            },
        )
        self.assertEqual(attestation["schema"], 1)
        self.assertIn(attestation["risk_tier"], {"docs", "behavior", "security-state"})
        self.assertEqual(attestation["repository"], "Cjbuilds/Codex-Orchestration")
        self.assertEqual(attestation["base_branch"], "main")
        self.assertRegex(attestation["reviewed_head_sha"], r"^[0-9a-f]{40}$")
        self.assertIsInstance(attestation["negative_test_evidence"], list)
        self.assertNotIn("proof", template.lower().split(start, 1)[1])
        self.assertIn("OpenRouter manifest change must use schema 2", template)
        self.assertIn("tested_head_sha", template)

    def test_versioned_hooks_use_the_preflight_source_of_truth(self) -> None:
        pre_commit = REPO_ROOT / ".githooks/pre-commit"
        pre_push = REPO_ROOT / ".githooks/pre-push"

        self.assertEqual(
            pre_commit.read_text(encoding="utf-8"),
            "#!/bin/sh\nexec python3 scripts/preflight.py quick\n",
        )
        self.assertEqual(
            pre_push.read_text(encoding="utf-8"),
            "#!/bin/sh\nexec python3 scripts/preflight.py full\n",
        )
        for hook in (pre_commit, pre_push):
            index = subprocess.run(
                ["git", "ls-files", "--stage", hook.relative_to(REPO_ROOT).as_posix()],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(index.returncode, 0, index.stderr)
            self.assertTrue(index.stdout.startswith("100755 "), index.stdout)

    def test_workflow_triggers_and_concurrency_are_bounded(self) -> None:
        ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        codeql = (REPO_ROOT / ".github/workflows/codeql.yml").read_text(
            encoding="utf-8"
        )
        for workflow in (ci, codeql):
            trigger_block = workflow.split("permissions:", 1)[0]
            self.assertRegex(trigger_block, r"(?m)^  push:\n    branches: \[main\]$")
            self.assertRegex(trigger_block, r"(?m)^  pull_request:")
            self.assertNotRegex(trigger_block, r"(?m)^    branches:.*feature")
            self.assertIn("concurrency:", workflow)
            self.assertIn("github.event.pull_request.number || github.ref", workflow)
            self.assertIn(
                "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                workflow,
            )
        self.assertIn('cron: "17 4 * * 1"', codeql)
        self.assertIn(
            "types: [opened, synchronize, reopened, edited, ready_for_review]", ci
        )
        self.assertNotIn("ready_for_review", codeql)

    def test_seven_ci_contexts_use_strict_targets_and_codeql_stays_native(self) -> None:
        ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        codeql = (REPO_ROOT / ".github/workflows/codeql.yml").read_text(
            encoding="utf-8"
        )
        preflight = (REPO_ROOT / "scripts/preflight.py").read_text(encoding="utf-8")

        expected_targets = {
            "quality": "quality",
            "test": "test",
            "plugin-lifecycle": "lifecycle",
            "legacy-client-guard": "legacy",
            "portability": "portability",
        }
        for job, target in expected_targets.items():
            match = re.search(
                rf"(?ms)^  {re.escape(job)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:|\Z)",
                ci,
            )
            self.assertIsNotNone(match, job)
            self.assertEqual(
                match.group(1).count(f"scripts/preflight.py {target} --ci"), 1, job
            )

        self.assertIn('python-version: ["3.11", "3.13"]', ci)
        self.assertIn("name: portability (${{ matrix.os }})", ci)
        self.assertIn("os: [macos-latest, windows-latest]", ci)
        for module in (
            "tests.test_external_cli_trust",
            "tests.test_external_configurator",
            "tests.test_external_credentials",
            "tests.test_external_providers",
            "tests.test_external_readiness",
            "tests.test_external_registry",
            "tests.test_external_subscription",
        ):
            self.assertIn(f'"{module}"', preflight)
        self.assertIn("name: analyze (python)", codeql)
        self.assertEqual(codeql.count("github/codeql-action/analyze@"), 1)

    def test_workflow_actions_permissions_and_cli_versions_remain_pinned(self) -> None:
        ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        codeql = (REPO_ROOT / ".github/workflows/codeql.yml").read_text(
            encoding="utf-8"
        )
        checkout = "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
        setup_python = (
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
        )
        setup_node = "actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e"
        codeql_init = (
            "github/codeql-action/init@02c5e83432fe5497fd85b873b6c9f16a8578e1d9"
        )
        codeql_analyze = (
            "github/codeql-action/analyze@02c5e83432fe5497fd85b873b6c9f16a8578e1d9"
        )

        self.assertEqual(ci.count(checkout), 5)
        self.assertEqual(codeql.count(checkout), 1)
        self.assertEqual(ci.count(setup_python), 5)
        self.assertEqual(codeql.count(setup_python), 0)
        self.assertEqual(ci.count(setup_node), 2)
        self.assertEqual(codeql.count(codeql_init), 1)
        self.assertEqual(codeql.count(codeql_analyze), 1)
        self.assertIn("@openai/codex@0.144.1", ci)
        self.assertIn("@openai/codex@0.142.5", ci)
        self.assertRegex(ci, r"(?ms)^permissions:\n  contents: read\n\njobs:")
        self.assertRegex(
            codeql,
            r"(?ms)^permissions:\n  contents: read\n  security-events: write\n\njobs:",
        )

    def test_quality_uses_immutable_comparison_shas_and_no_replaced_inline_checks(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("github.event.before", workflow)
        self.assertIn("github.sha", workflow)
        self.assertIn('--base-sha "$BASE_SHA"', workflow)
        self.assertIn('--head-sha "$HEAD_SHA"', workflow)
        self.assertIn('--event-path "$GITHUB_EVENT_PATH"', workflow)
        self.assertLess(
            workflow.index("Install pinned development tools"),
            workflow.index("scripts/preflight.py quality --ci"),
        )
        for old_command in (
            "python -m ruff check plugins tests scripts",
            "python -m compileall -q plugins tests",
            "python -m unittest discover -s tests -v",
            "python tests/plugin_lifecycle_smoke.py",
            "features.multi_agent_v2.multi_agent_mode_hint_text",
        ):
            self.assertNotIn(old_command, workflow)

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
        self.assertEqual(manifest["version"], "0.8.0")
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
        self.assertFalse((SKILL_ROOT / "scripts" / "update_plugin.py").exists())
        self.assertIn("config/batchWrite", native.read_text(encoding="utf-8"))
        self.assertIn('"--repair"', native.read_text(encoding="utf-8"))
        self.assertIn('"version": "0.8.0"', native.read_text(encoding="utf-8"))
        self.assertIn("validate_routing_state", routing_state.read_text(encoding="utf-8"))
        self.assertIn("Standalone custom agent", custom.read_text(encoding="utf-8"))

    def test_external_model_runtime_and_manifests_are_packaged(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        providers = SKILL_ROOT / "providers"
        required_scripts = {
            "external_auth_helper.py",
            "external_cli_trust.py",
            "external_configurator.py",
            "external_credentials.py",
            "external_providers.py",
            "external_readiness.py",
            "external_registry.py",
            "external_subscription.py",
        }
        for name in required_scripts:
            self.assertTrue((scripts / name).is_file(), name)
        openrouter = json.loads((providers / "openrouter.json").read_text("utf-8"))
        fable = json.loads((providers / "claude-fable.json").read_text("utf-8"))
        self.assertEqual(openrouter["models"].keys(), {"moonshotai/kimi-k3"})
        self.assertEqual(openrouter["version"], 2)
        self.assertFalse(openrouter["experimental"])
        self.assertFalse(openrouter["qualified"])
        self.assertEqual(
            openrouter["models"]["moonshotai/kimi-k3"]["supported_efforts"],
            ["max"],
        )
        self.assertEqual(fable["subscription_adapter"]["module"], "fable_advisor_mcp")
        external_reference = SKILL_ROOT / "references/external-models.md"
        self.assertTrue(external_reference.is_file())

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

    def test_explicit_and_natural_language_invocation_metadata_is_consistent(self) -> None:
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("$codex-orchestration", metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)
        self.assertNotIn("allow_implicit_invocation: false", metadata)
        self.assertIn(
            "Use for natural-language questions or requests about whether Kimi K3",
            skill,
        )
        self.assertIn("available or callable as Designer", skill)
        self.assertIn("is Kimi available to use as Designer?", readme)
        self.assertIn("/codex-orchestration setup executor:", readme)
        self.assertIn("GPT-5.6 Luna Extra High", readme)
        self.assertIn("/codex-orchestration create project role:", readme)
        self.assertIn("/codex-orchestration status", readme)
        self.assertIn("/codex-orchestration disable", readme)
        self.assertIn("/codex-orchestration --update", readme)
        self.assertIn("designer: GPT-5.6", readme)
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
        self.assertIn("python scripts/preflight.py lifecycle --ci", workflow)
        self.assertIn("@openai/codex@0.142.5", workflow)
        self.assertIn("@openai/codex@0.144.1", workflow)
        smoke_text = smoke.read_text(encoding="utf-8")
        self.assertIn('OLD_VERSION = "0.5.0"', smoke_text)
        self.assertIn('NEW_VERSION = "0.8.0"', smoke_text)
        self.assertIn("old Advisor-only cache unexpectedly supports Planner", smoke_text)
        self.assertIn("Upgraded installed skill is missing Planner contract", smoke_text)
        self.assertIn("reused the Advisor-only 0.5.0 cache directory", smoke_text)
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

        self.assertIn("Other unbundled providers must already be configured and authenticated", readme)
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
        self.assertIn("Version **0.6.0 or newer**", readme)
        self.assertIn("`marketplaceSource.sourceType` is `local`", readme)
        self.assertIn("`disable` restores the routing values", readme)
        self.assertIn("does not delete user-owned custom roles", readme)
        self.assertIn("Review and remove any user-owned custom roles separately", readme)


if __name__ == "__main__":
    unittest.main()
