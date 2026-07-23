from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
    / "configure_native_routing.py"
)
sys.path.insert(0, str(SCRIPT.parent))

SPEC = importlib.util.spec_from_file_location("configure_native_routing", SCRIPT)
assert SPEC and SPEC.loader
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("codex-cli 0.144.1")
    raise SystemExit(0)

if "features" in sys.argv and "list" in sys.argv:
    if (
        os.environ.get("FAKE_CODEX_INCOMPATIBLE") == "1"
        or Path(sys.argv[0]).name.startswith("old-")
    ):
        print("unknown multi_agent_mode_hint_text", file=sys.stderr)
        raise SystemExit(1)
    print("multi_agent_v2 under-development false")
    raise SystemExit(0)

if sys.argv[1:] == ["plugin", "list", "--json"]:
    home = Path(os.environ["CODEX_HOME"]).resolve()
    inventory_file = home / ".fake-plugin-inventory.json"
    if inventory_file.exists():
        print(inventory_file.read_text(encoding="utf-8"))
    else:
        plugin_id = os.environ["FAKE_PLUGIN_ID"]
        marketplace = plugin_id.split("@", 1)[1]
        source_root = os.environ["FAKE_PLUGIN_ROOT"]
        print(json.dumps({
            "installed": [{
                "pluginId": plugin_id,
                "name": "codex-orchestration",
                "marketplaceName": marketplace,
                "version": "0.9.2",
                "installed": True,
                "enabled": True,
                "source": {"source": "local", "path": source_root},
                "marketplaceSource": {
                    "sourceType": "local",
                    "source": str(Path(source_root).parent.parent),
                },
            }],
            "available": [],
        }))
    raise SystemExit(0)

if "app-server" not in sys.argv:
    raise SystemExit(2)

home = Path(os.environ["CODEX_HOME"]).resolve()
home.mkdir(parents=True, exist_ok=True)
store = home / ".fake-user-config.json"
effective_store = home / ".fake-effective-config.json"
version_file = home / ".fake-version"
mutate_after_write = home / ".fake-mutate-after-write"
mutate_namespace_after_write = home / ".fake-mutate-namespace-after-write"
mutate_feature_after_write = home / ".fake-mutate-feature-after-write"
mutate_state_after_write = home / ".fake-mutate-state-after-write"
reformat_state_after_write = home / ".fake-reformat-state-after-write"
ok_overridden = home / ".fake-ok-overridden"
overridden_returned = home / ".fake-overridden-returned"
fail_overridden_rollback = home / ".fake-fail-overridden-rollback"

def read_config():
    if store.exists():
        return json.loads(store.read_text(encoding="utf-8"))
    return {
        "features": {"multi_agent_v2": {"max_concurrent_threads_per_session": 5}},
        "unrelated": {"keep": True},
    }

def version():
    return int(version_file.read_text()) if version_file.exists() else 0

def set_path(root, path, value):
    parts = []
    current_part = []
    quoted = False
    escaped = False
    for character in path:
        if escaped:
            current_part.append(character)
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "." and not quoted:
            parts.append("".join(current_part))
            current_part = []
        else:
            current_part.append(character)
    parts.append("".join(current_part))
    current = root
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    if value is None:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = value

models = [
    {
        "id": "gpt-5.6-sol",
        "model": "gpt-5.6-sol",
        "supportedReasoningEfforts": [
            {"reasoningEffort": value, "description": value}
            for value in ("low", "medium", "high", "xhigh", "max", "ultra")
        ],
        "defaultReasoningEffort": "xhigh",
    },
    {
        "id": "gpt-5.6-terra",
        "model": "gpt-5.6-terra",
        "supportedReasoningEfforts": [
            {"reasoningEffort": value, "description": value}
            for value in ("low", "medium", "high", "xhigh", "max", "ultra")
        ],
        "defaultReasoningEffort": "high",
    },
    {
        "id": "gpt-5.6-luna",
        "model": "gpt-5.6-luna",
        "supportedReasoningEfforts": [
            {"reasoningEffort": value, "description": value}
            for value in ("low", "medium", "high", "xhigh", "max")
        ],
        "defaultReasoningEffort": "high",
    },
]

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "userAgent": "fake-codex",
            "codexHome": str(home),
            "platformFamily": "unix",
            "platformOs": "test",
        }
    elif method == "config/read":
        config = read_config()
        effective = (
            json.loads(effective_store.read_text(encoding="utf-8"))
            if effective_store.exists()
            else config
        )
        result = {
            "config": effective,
            "origins": {},
            "layers": [
                {
                    "name": {
                        "type": "user",
                        "file": str(home / "config.toml"),
                        "profile": None,
                    },
                    "version": f"sha256:v{version()}",
                    "config": config,
                    "disabledReason": None,
                }
            ],
        }
    elif method == "model/list":
        result = {"data": models, "nextCursor": None}
    elif method == "config/batchWrite":
        params = message["params"]
        expected = params.get("expectedVersion")
        current_version = f"sha256:v{version()}"
        if fail_overridden_rollback.exists() and overridden_returned.exists():
            print(json.dumps({
                "id": request_id,
                "error": {
                    "code": -32600,
                    "message": "Forced rollback failure",
                    "data": {"config_write_error_code": "configVersionConflict"},
                },
            }), flush=True)
            continue
        if expected is not None and expected != current_version:
            print(json.dumps({
                "id": request_id,
                "error": {
                    "code": -32600,
                    "message": "Configuration was modified",
                    "data": {"config_write_error_code": "configVersionConflict"},
                },
            }), flush=True)
            continue
        config = read_config()
        for edit in params["edits"]:
            set_path(config, edit["keyPath"], edit.get("value"))
        if mutate_after_write.exists():
            set_path(
                config,
                "features.multi_agent_v2.usage_hint_text",
                "CONCURRENT USER EDIT",
            )
            mutate_after_write.unlink()
        if mutate_namespace_after_write.exists():
            set_path(
                config,
                "features.multi_agent_v2.tool_namespace",
                "collaboration",
            )
            mutate_namespace_after_write.unlink()
        if mutate_feature_after_write.exists():
            set_path(
                config,
                "features.multi_agent_v2.max_concurrent_threads_per_session",
                9,
            )
            mutate_feature_after_write.unlink()
        store.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        if mutate_state_after_write.exists():
            state_path = home / ".codex-orchestration-routing.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["previous"]["usage"] = {
                "known": True,
                "present": True,
                "value": "CONCURRENT STATE EDIT",
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            mutate_state_after_write.unlink()
        if reformat_state_after_write.exists():
            state_path = home / ".codex-orchestration-routing.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            reformat_state_after_write.unlink()
        new_version = version() + 1
        version_file.write_text(str(new_version), encoding="utf-8")
        status = "ok"
        if ok_overridden.exists() and not overridden_returned.exists():
            overridden_returned.touch()
            status = "okOverridden"
        result = {
            "status": status,
            "version": f"sha256:v{new_version}",
            "filePath": str(home / "config.toml"),
            "overriddenMetadata": None,
        }
    else:
        print(json.dumps({
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }), flush=True)
        continue
    print(json.dumps({"id": request_id, "result": result}), flush=True)
'''


class NativeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.codex = self.root / "fake-codex"
        self.codex.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        self.codex.chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.claude = self.bin / "claude"
        self.claude.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                if sys.argv[1:] == ["auth", "status"]:
                    print(json.dumps({
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "apiProvider": "firstParty",
                        "subscriptionType": "max",
                    }))
                    raise SystemExit(0)
                if sys.argv[1:] == ["--help"]:
                    print(
                        "--model --effort <level> Effort level "
                        "(low, medium, high, xhigh, max) "
                        "--safe-mode --prompt-suggestions"
                    )
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        self.claude.chmod(0o755)
        self.kimi = self.bin / "kimi"
        self.kimi.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                if sys.argv[1:] == ["--version"]:
                    print("0.27.0")
                    raise SystemExit(0)
                if sys.argv[1:] == ["provider", "list", "--json"]:
                    print(json.dumps({
                        "providers": {
                            "managed:kimi-code": {
                                "type": "kimi",
                                "apiKey": "",
                                "oauth": {"storage": "file", "key": "oauth/kimi-code"},
                            }
                        },
                        "models": {
                            "kimi-code/k3": {
                                "provider": "managed:kimi-code",
                                "model": "k3",
                                "defaultEffort": "high",
                                "supportEfforts": ["low", "high", "max"],
                            }
                        },
                    }))
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        self.kimi.chmod(0o755)
        self.acpx = self.bin / "acpx"
        self.acpx.write_text(
            "#!/usr/bin/env python3\nimport sys\nprint('0.12.0') if sys.argv[1:] == ['--version'] else sys.exit(2)\n",
            encoding="utf-8",
        )
        self.acpx.chmod(0o755)
        self.source_plugin_root = SCRIPT.resolve().parents[3]
        self.plugin_id = "codex-orchestration@test-marketplace"
        self._original_executing_plugin_root = NATIVE.EXECUTING_PLUGIN_ROOT
        self.activate_plugin_cache(self.plugin_id)

    def tearDown(self) -> None:
        NATIVE.EXECUTING_PLUGIN_ROOT = self._original_executing_plugin_root
        self.temp.cleanup()

    def activate_plugin_cache(self, plugin_id: str) -> None:
        marketplace = plugin_id.split("@", 1)[1]
        plugin_root = (
            self.home
            / "plugins"
            / "cache"
            / marketplace
            / "codex-orchestration"
            / "0.9.2"
        )
        if not plugin_root.exists():
            shutil.copytree(
                self.source_plugin_root,
                plugin_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        self.plugin_id = plugin_id
        self.plugin_root = plugin_root
        self.installed_script = (
            plugin_root
            / "skills"
            / "codex-orchestration"
            / "scripts"
            / "configure_native_routing.py"
        )
        NATIVE.EXECUTING_PLUGIN_ROOT = plugin_root

    def run_script(
        self,
        *arguments: str,
        check: bool = True,
        allow_incompatible: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        compatibility = ["--allow-incompatible-client"] if allow_incompatible else []
        env = self.fake_env()
        result = subprocess.run(
            [
                sys.executable,
                str(self.installed_script),
                "--codex-bin",
                str(self.codex),
                "--codex-home",
                str(self.home),
                *compatibility,
                *arguments,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
            env=env,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def fake_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_PLUGIN_ROOT"] = str(self.source_plugin_root)
        env["FAKE_PLUGIN_ID"] = self.plugin_id
        return env

    @staticmethod
    def plugin_inventory_record(
        plugin_id: str,
        source_root: Path,
        *,
        enabled: bool,
    ) -> dict[str, object]:
        marketplace = plugin_id.split("@", 1)[1]
        return {
            "pluginId": plugin_id,
            "name": "codex-orchestration",
            "marketplaceName": marketplace,
            "version": "0.9.2",
            "installed": True,
            "enabled": enabled,
            "source": {"source": "local", "path": str(source_root)},
            "marketplaceSource": {
                "sourceType": "local",
                "source": str(source_root.parent),
            },
        }

    def write_plugin_inventory(self, *records: dict[str, object]) -> None:
        (self.home / ".fake-plugin-inventory.json").write_text(
            json.dumps({"installed": list(records), "available": []}),
            encoding="utf-8",
        )

    def read_fake_config(self) -> dict[str, object]:
        return json.loads(
            (self.home / ".fake-user-config.json").read_text(encoding="utf-8")
        )

    def valid_state(self, model: str = "gpt-5.6-luna") -> dict[str, object]:
        state, _, _ = NATIVE._prepare_setup_state(
            {},
            None,
            self.plugin_id,
            f"{NATIVE.MANAGED_MARKER}\nmode",
            f"{NATIVE.MANAGED_MARKER}\nusage",
            {"kind": "model", "model": model, "effort": "high"},
            None,
            None,
            None,
            self.home / "config.toml",
            False,
        )
        return state

    def run_main_with_identity_failure(
        self, phase: str, *arguments: str
    ) -> tuple[int, str, str]:
        real_assert = NATIVE.plugin_identity.PluginIdentityGuard.assert_unchanged

        def assert_unchanged(
            guard: NATIVE.plugin_identity.PluginIdentityGuard, current_phase: str
        ) -> None:
            if current_phase == phase:
                raise NATIVE.plugin_identity.IdentityDriftError(
                    f"forced drift before {phase}"
                )
            real_assert(guard, current_phase)

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            *arguments,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
            mock.patch.object(
                NATIVE.plugin_identity.PluginIdentityGuard,
                "assert_unchanged",
                new=assert_unchanged,
            ),
        ):
            result = NATIVE.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def assert_effective_state_rollback_interrupt_pairing(
        self, *, update: bool, after_state_commit: bool
    ) -> None:
        base_config = {
            "features": {
                "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
            },
            "unrelated": {"keep": True},
        }
        if update:
            self.run_script(
                "--executor-model",
                "gpt-5.6-luna",
                "--executor-effort",
                "high",
                "--apply",
            )
            prior_config = self.read_fake_config()
            prior_state_bytes = (self.home / NATIVE.STATE_FILENAME).read_bytes()
            target_model = "gpt-5.6-terra"
            primitive_name = "_write_state"
        else:
            prior_config = base_config
            prior_state_bytes = None
            target_model = "gpt-5.6-luna"
            primitive_name = "_remove_state"
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(prior_config), encoding="utf-8"
        )

        real_primitive = getattr(NATIVE, primitive_name)
        calls = 0

        def interrupt_state_rollback(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if update and calls == 1:
                return real_primitive(*args, **kwargs)
            if after_state_commit:
                real_primitive(*args, **kwargs)
            raise KeyboardInterrupt("forced effective rollback interrupt")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            target_model,
            "--executor-effort",
            "high",
            "--apply",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                NATIVE, primitive_name, side_effect=interrupt_state_rollback
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", io.StringIO()),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            with self.assertRaisesRegex(
                KeyboardInterrupt, "forced effective rollback interrupt"
            ):
                NATIVE.main()

        state_path = self.home / NATIVE.STATE_FILENAME
        if after_state_commit:
            self.assertEqual(self.read_fake_config(), prior_config)
            if prior_state_bytes is None:
                self.assertFalse(state_path.exists())
            else:
                self.assertEqual(state_path.read_bytes(), prior_state_bytes)
        else:
            state = NATIVE._read_state(state_path)
            self.assertIsNotNone(state)
            self.assertEqual(state["executor"]["model"], target_model)
            self.assertTrue(
                NATIVE._managed_matches(
                    state,
                    NATIVE._current_values(self.read_fake_config(), self.plugin_id),
                )
            )

    def write_personal_agent(self, name: str, *, managed: bool = False) -> Path:
        agents = self.home / "agents"
        agents.mkdir(exist_ok=True)
        path = agents / f"{name.replace('_', '-')}.toml"
        content = "\n".join(
                (
                    f'name = "{name}"',
                    'description = "Test custom route"',
                    'model = "gpt-5.6-luna"',
                    'model_reasoning_effort = "high"',
                    'developer_instructions = "Stay bounded and report to the root."',
                    "",
                )
            )
        if managed:
            content = f"{NATIVE.CUSTOM_AGENT_MANAGED_MARKER}\n{content}"
        path.write_text(content, encoding="utf-8")
        return path

    def test_policy_keeps_root_authority_and_pins_fork_none(self) -> None:
        executor = {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"}
        planner = {"kind": "model", "model": "gpt-5.6-sol", "effort": "high"}
        advisor = {"kind": "model", "model": "gpt-5.6-terra", "effort": "high"}
        designer = {"kind": "model", "model": "gpt-5.6-luna", "effort": "high"}
        mode, usage = NATIVE.build_policy(executor, planner, advisor, designer)

        self.assertIn("root task model, you are the orchestrator", mode)
        self.assertIn("Codex still decides whether a plan or subagent helps", mode)
        self.assertIn("never spawn descendants", mode)
        self.assertIn("Explicit user instructions win", mode)
        self.assertIn("Persistent and task-local Planner and Advisor routes", mode)
        self.assertIn("at most five total Advisor reviews", mode)
        self.assertIn("PLAN_APPROVED ends review early", mode)
        self.assertIn("round-five PLAN_REVISE halts before Executor", mode)
        self.assertIn("NOT_ADVISOR_APPROVED", mode)
        self.assertIn("Planner failure permits the root to take over", mode)
        self.assertIn("stale plan version", mode)
        self.assertIn("invalid or incomplete ledger", mode)
        self.assertIn("There is no Finalizer seat", mode)
        self.assertIn("configured Designer", mode)
        self.assertIn("design artifacts", mode)
        self.assertIn("or release Executor", mode)
        self.assertIn("cannot contact each other", mode)
        self.assertIn("cannot contact each other, Designer, or Executors", mode)
        self.assertLess(
            mode.index("configured Planner drafts"),
            mode.index("fresh self-contained review call"),
        )
        self.assertLess(
            mode.index("fresh self-contained review call"),
            mode.index("On PLAN_REVISE"),
        )
        self.assertLess(
            mode.index("On PLAN_REVISE"),
            mode.index("When executor delegation"),
        )
        self.assertIn('model = "gpt-5.6-luna"', usage)
        self.assertIn('reasoning_effort = "xhigh"', usage)
        self.assertIn('model = "gpt-5.6-sol"', usage)
        self.assertIn("For delegated design work", usage)
        self.assertGreaterEqual(usage.count('fork_turns = "none"'), 4)
        self.assertIn('Never use fork_turns = "all"', usage)
        self.assertIn("task-local Planner and Advisor must still be distinct", usage)
        self.assertIn("same direct model ID", usage)
        self.assertIn("Fable in both seats", usage)
        self.assertIn("If you are a spawned child, do not call this tool", usage)
        self.assertNotIn("tool_namespace", mode + usage)
        self.assertNotIn("enabled = true", mode + usage)

    def test_policy_routes_qwen_advisor_through_sealed_root_tool(self) -> None:
        executor = {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"}
        planner = {"kind": "model", "model": "gpt-5.6-sol", "effort": "xhigh"}
        advisor = {
            "kind": "qwen_cli",
            "model": NATIVE.QWEN_MODEL,
            "effort": "native",
            "region": "global",
            "server": "qwen-advisor-python3",
        }
        mode, usage = NATIVE.build_policy(executor, planner, advisor, None)

        self.assertIn("direct Qwen Planner paired with the sealed Qwen Advisor", mode)
        self.assertIn("qwen-advisor-python3", usage)
        self.assertIn("Token Plan JSON API", usage)
        self.assertIn("runtime model qwen3.8-max-preview", usage)
        self.assertIn("not a spawned child", usage)

    def test_policy_root_fallback_planner_without_advisor_and_fable_hints(self) -> None:
        executor = {"kind": "model", "model": "gpt-5.6-luna", "effort": "high"}
        advisor = {"kind": "model", "model": "gpt-5.6-terra", "effort": "high"}
        root_mode, root_usage = NATIVE.build_policy(executor, None, advisor)
        self.assertIn("root drafts and revises every plan", root_mode)
        self.assertIn("fresh self-contained review call", root_mode)
        self.assertIn("No Planner route is configured", root_usage)

        planner = {"kind": "model", "model": "gpt-5.6-sol", "effort": "xhigh"}
        planner_mode, planner_usage = NATIVE.build_policy(executor, planner, None)
        self.assertIn("root validates the plan before releasing Executor", planner_mode)
        self.assertIn("No advisor route is configured", planner_usage)
        self.assertNotIn("review_plan", planner_usage)

        fable_planner = {
            "kind": "fable",
            "model": NATIVE.FABLE_MODEL,
            "effort": "high",
            "server": "fable-advisor-python3",
        }
        _, fable_planner_usage = NATIVE.build_policy(
            executor, fable_planner, advisor
        )
        self.assertLess(
            fable_planner_usage.index("create_plan"),
            fable_planner_usage.index("revise_plan"),
        )
        self.assertIn("review packet", fable_planner_usage)

        fable_advisor = dict(fable_planner)
        _, fable_advisor_usage = NATIVE.build_policy(
            executor, planner, fable_advisor
        )
        self.assertIn("review_plan", fable_advisor_usage)
        self.assertIn('fork_turns = "none"', fable_advisor_usage)

    def test_root_planning_allows_sol_advisor_and_sol_executor_override(self) -> None:
        executor = {"kind": "model", "model": "gpt-5.6-sol", "effort": "medium"}
        advisor = {"kind": "model", "model": "gpt-5.6-sol", "effort": "max"}

        mode, usage = NATIVE.build_policy(executor, None, advisor)

        self.assertIn("No Planner is configured", mode)
        self.assertIn("root owns planning", mode)
        self.assertIn(
            "same model ID as the root is not a duplicate configured Planner route",
            mode,
        )
        self.assertIn('model = "gpt-5.6-sol", reasoning_effort = "max"', usage)
        self.assertIn('model = "gpt-5.6-sol", reasoning_effort = "medium"', usage)
        self.assertGreaterEqual(usage.count('fork_turns = "none"'), 2)
        self.assertIn("explicit current-task model, effort, agent", usage)
        self.assertIn("Never invent or substitute GPT-5.5, Terra, Qwen, Fable", usage)
        self.assertIn("If the exact explicit route is unavailable", usage)
        self.assertIn("when both are configured", usage)

    def test_planner_argument_validation(self) -> None:
        exclusive = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--planner-agent",
            "planner_agent",
            check=False,
        )
        self.assertEqual(exclusive.returncode, 2)
        self.assertIn("not allowed with argument", exclusive.stderr)

        effort = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-agent",
            "planner_agent",
            "--planner-effort",
            "high",
            check=False,
        )
        self.assertEqual(effort.returncode, 2)
        self.assertIn("custom planner agent owns its effort", effort.stderr)

        invalid = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "bad model",
            check=False,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("Invalid planner model", invalid.stderr)

    def test_designer_argument_validation(self) -> None:
        external = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--designer-agent",
            "designer_agent",
            check=False,
        )
        self.assertEqual(external.returncode, 2)
        self.assertIn("unrecognized arguments: --designer-agent", external.stderr)

        invalid = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--designer-model",
            "bad model",
            check=False,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("Invalid designer model", invalid.stderr)

        for action in ("--status", "--disable", "--repair"):
            with self.subTest(action=action):
                result = self.run_script(
                    action,
                    "--designer-effort",
                    "high",
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("does not accept seat settings", result.stderr)

    def test_capability_probe_checks_the_complete_routing_surface(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="supported")
        with mock.patch.object(NATIVE.subprocess, "run", return_value=completed) as run:
            supported, _ = NATIVE.supports_native_policy(self.codex)
        self.assertTrue(supported)
        argv = run.call_args.args[0]
        self.assertIn(
            'features.multi_agent_v2.tool_namespace="agents"',
            argv,
        )
        self.assertIn(
            "features.multi_agent_v2.hide_spawn_agent_metadata=false",
            argv,
        )
        self.assertTrue(
            any("multi_agent_mode_hint_text" in value for value in argv)
        )
        self.assertTrue(any("usage_hint_text" in value for value in argv))

    def test_setup_status_and_disable_round_trip(self) -> None:
        preview = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
        )
        self.assertIn("Dry run only", preview.stdout)
        self.assertFalse((self.home / ".fake-user-config.json").exists())

        applied = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
        )
        self.assertIn("Native routing policy installed", applied.stdout)
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        self.assertEqual(feature["max_concurrent_threads_per_session"], 5)
        self.assertFalse(feature["hide_spawn_agent_metadata"])
        self.assertEqual(feature["tool_namespace"], "agents")
        self.assertIn(NATIVE.MANAGED_MARKER, feature["usage_hint_text"])
        self.assertEqual(config["unrelated"], {"keep": True})

        status = self.run_script("--status")
        self.assertIn("Native policy: installed and effective", status.stdout)
        self.assertIn(f"Plugin identity: {self.plugin_id}", status.stdout)
        self.assertIn(f"Executing plugin identity: {self.plugin_id}", status.stdout)
        self.assertNotIn("Plugin identity mismatch:", status.stdout)
        self.assertIn("V2 activation: not inferred", status.stdout)
        self.assertIn("Executor: gpt-5.6-luna@xhigh", status.stdout)
        self.assertIn("Designer: none", status.stdout)
        self.assertIn("Advisor: none", status.stdout)
        self.assertIn("V2 tool namespace: agents", status.stdout)
        self.assertIn("Routing validation: not performed", status.stdout)

        required = self.run_script("--status", "--require-effective")
        self.assertEqual(required.returncode, 0)

        disabled = self.run_script("--disable", "--apply")
        self.assertIn("Native routing disabled", disabled.stdout)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertEqual(feature, {"max_concurrent_threads_per_session": 5})
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_status_uses_saved_namespace_when_an_alternate_identity_executes(self) -> None:
        saved_id = self.plugin_id
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--advisor-fable",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_before = state_path.read_bytes()
        config = self.read_fake_config()
        alternate_id = "codex-orchestration@alternate-status"
        config.setdefault("plugins", {})[alternate_id] = {
            "mcp_servers": {"fable-advisor-python3": {"enabled": False}}
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        self.activate_plugin_cache(alternate_id)
        self.write_plugin_inventory(
            self.plugin_inventory_record(
                saved_id, self.source_plugin_root, enabled=False
            ),
            self.plugin_inventory_record(
                alternate_id, self.source_plugin_root, enabled=True
            ),
        )

        status = self.run_script("--status", "--require-effective", check=False)

        self.assertEqual(status.returncode, 1)
        self.assertIn("Native policy: installed and effective", status.stdout)
        self.assertIn(f"Plugin identity: {saved_id}", status.stdout)
        self.assertIn(f"Executing plugin identity: {alternate_id}", status.stdout)
        self.assertIn(
            f"Plugin identity mismatch: saved namespace owner {saved_id}; "
            f"executing cache owner {alternate_id}",
            status.stdout,
        )
        self.assertIn("Advisor: Claude Fable 5 high", status.stdout)
        self.assertNotIn("managed fields conflict", status.stdout)
        self.assertEqual(state_path.read_bytes(), state_before)

    def test_status_rechecks_exact_state_digest_before_publication(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        real_assert = NATIVE._assert_state_digest

        def mutate_before_publication(path: Path, digest: str | None) -> None:
            state = json.loads(path.read_text(encoding="utf-8"))
            state["executor"]["model"] = "gpt-5.6-terra"
            path.write_text(json.dumps(state), encoding="utf-8")
            real_assert(path, digest)

        stdout = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.fake_env()),
            mock.patch.object(
                NATIVE,
                "_assert_state_digest",
                side_effect=mutate_before_publication,
            ),
            mock.patch.object(sys, "stdout", stdout),
            self.assertRaisesRegex(
                NATIVE.ConfigurationError,
                "Saved routing state changed concurrently",
            ),
        ):
            NATIVE._status(self.codex, self.home, [self.codex], False)
        self.assertEqual(stdout.getvalue(), "")

    def test_disable_refuses_overridden_restore_and_retains_state(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_bytes = state_path.read_bytes()
        (self.home / ".fake-ok-overridden").touch()

        disabled = self.run_script("--disable", "--apply", check=False)

        self.assertEqual(disabled.returncode, 2)
        self.assertIn("higher-priority layer overrides", disabled.stderr)
        self.assertNotIn("Native routing disabled", disabled.stdout)
        self.assertEqual(state_path.read_bytes(), state_bytes)

    def test_disable_readback_preserves_state_after_post_write_edit(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_bytes = state_path.read_bytes()
        (self.home / ".fake-mutate-after-write").touch()

        disabled = self.run_script("--disable", "--apply", check=False)

        self.assertEqual(disabled.returncode, 2)
        self.assertIn("newer edit was preserved", disabled.stderr)
        self.assertNotIn("Native routing disabled", disabled.stdout)
        self.assertEqual(state_path.read_bytes(), state_bytes)
        self.assertEqual(
            self.read_fake_config()["features"]["multi_agent_v2"][
                "usage_hint_text"
            ],
            "CONCURRENT USER EDIT",
        )

    def test_disable_readback_preserves_state_when_effective_restore_is_overridden(
        self,
    ) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_bytes = state_path.read_bytes()
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(self.read_fake_config()), encoding="utf-8"
        )

        disabled = self.run_script("--disable", "--apply", check=False)

        self.assertEqual(disabled.returncode, 2)
        self.assertIn("overridden in this workspace", disabled.stderr)
        self.assertNotIn("Native routing disabled", disabled.stdout)
        self.assertEqual(state_path.read_bytes(), state_bytes)

    def test_direct_planner_designer_setup_status_and_require_effective(self) -> None:
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--planner-effort",
            "auto",
            "--advisor-model",
            "gpt-5.6-terra",
            "--advisor-effort",
            "high",
            "--designer-model",
            "gpt-5.6-luna",
            "--designer-effort",
            "medium",
            "--apply",
        )
        self.assertIn("Planner: gpt-5.6-sol@xhigh", setup.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["schema"], 7)
        self.assertEqual(state["policy_version"], 7)
        self.assertEqual(state["plugin_id"], self.plugin_id)
        self.assertEqual(state["planner"]["effort"], "xhigh")
        self.assertEqual(state["designer"]["effort"], "medium")

        status = self.run_script("--status", "--require-effective")
        self.assertIn("Planner: gpt-5.6-sol@xhigh", status.stdout)
        self.assertIn("Designer: gpt-5.6-luna@medium", status.stdout)
        self.assertEqual(status.returncode, 0)

    def test_success_dry_runs_and_noop_repair_require_final_identity_recheck(self) -> None:
        setup_result, setup_stdout, setup_stderr = self.run_main_with_identity_failure(
            "setup dry-run publication",
            "--executor-model",
            "gpt-5.6-luna",
        )
        self.assertEqual(setup_result, 2)
        self.assertNotIn("Dry run only", setup_stdout)
        self.assertIn("forced drift", setup_stderr)

        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        repair_result, repair_stdout, repair_stderr = self.run_main_with_identity_failure(
            "repair no-op publication", "--repair"
        )
        self.assertEqual(repair_result, 2)
        self.assertNotIn("already matches", repair_stdout)
        self.assertIn("forced drift", repair_stderr)

        disable_result, disable_stdout, disable_stderr = (
            self.run_main_with_identity_failure(
                "disable dry-run publication", "--disable"
            )
        )
        self.assertEqual(disable_result, 2)
        self.assertNotIn("Dry run only", disable_stdout)
        self.assertIn("forced drift", disable_stderr)

    def test_status_discards_buffered_output_when_final_recheck_fails(self) -> None:
        def stale_status(*_args: object, **_kwargs: object) -> int:
            print("stale identity result")
            raise NATIVE.plugin_identity.IdentityDriftError("forced status drift")

        stdout = io.StringIO()
        with (
            mock.patch.object(NATIVE, "_status_unbuffered", side_effect=stale_status),
            mock.patch.object(sys, "stdout", stdout),
            self.assertRaises(NATIVE.plugin_identity.IdentityDriftError),
        ):
            NATIVE._status(self.codex, self.home, [self.codex], True)
        self.assertEqual(stdout.getvalue(), "")

    def test_kimi_subscription_designer_setup_status_and_disable(self) -> None:
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--planner-fable",
            "--planner-effort",
            "high",
            "--designer-kimi",
            "--apply",
        )
        self.assertIn("Designer: Kimi K3 max (Kimi Code subscription)", setup.stdout)
        self.assertIn("existing Kimi Code OAuth subscription via ACP", setup.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["schema"], 7)
        self.assertEqual(state["plugin_id"], self.plugin_id)
        self.assertEqual(state["designer"]["kind"], "kimi_cli")
        self.assertEqual(state["designer"]["model"], "kimi-code/k3")
        self.assertEqual(state["designer"]["effort"], "max")
        selected = state["designer"]["server"]
        planner_selected = state["planner"]["server"]
        self.assertTrue(state["managed"]["mcp"][selected])
        self.assertTrue(state["managed"]["mcp"][planner_selected])
        config = self.read_fake_config()
        servers = config["plugins"][self.plugin_id]["mcp_servers"]
        self.assertTrue(servers[selected]["enabled"])
        self.assertTrue(servers[planner_selected]["enabled"])

        status = self.run_script("--status", "--require-effective")
        self.assertIn("Kimi K3 Designer: ready", status.stdout)
        self.assertEqual(status.returncode, 0)

        self.run_script("--disable", "--apply")
        restored = self.read_fake_config()
        restored_servers = restored["plugins"][self.plugin_id]["mcp_servers"]
        self.assertNotIn("enabled", restored_servers[selected])
        self.assertNotIn("enabled", restored_servers[planner_selected])
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_disabled_canonical_sibling_never_receives_alternate_identity_writes(self) -> None:
        canonical_id = NATIVE.LEGACY_PLUGIN_ID
        canonical_root = self.root / "disabled-canonical-plugin"
        canonical_root.mkdir()
        (canonical_root / "payload.txt").write_text("canonical", encoding="utf-8")
        canonical_config = {
            "mcp_servers": {
                "fable-advisor-python3": {"enabled": False},
                "fable-advisor-python": {"enabled": True},
            }
        }
        initial = {
            "features": {"multi_agent_v2": {}},
            "plugins": {canonical_id: canonical_config},
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.write_plugin_inventory(
            self.plugin_inventory_record(
                canonical_id, canonical_root, enabled=False
            ),
            self.plugin_inventory_record(
                self.plugin_id, self.source_plugin_root, enabled=True
            ),
        )

        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--advisor-fable",
            "--apply",
        )
        configured = self.read_fake_config()
        self.assertEqual(configured["plugins"][canonical_id], canonical_config)
        self.assertTrue(
            configured["plugins"][self.plugin_id]["mcp_servers"]
            ["fable-advisor-python3"]["enabled"]
        )
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["plugin_id"], self.plugin_id)

        self.write_plugin_inventory(
            self.plugin_inventory_record(
                canonical_id, canonical_root, enabled=False
            ),
            self.plugin_inventory_record(
                self.plugin_id, self.source_plugin_root, enabled=False
            ),
        )
        self.run_script("--disable", "--apply")
        restored = self.read_fake_config()
        self.assertEqual(restored["plugins"][canonical_id], canonical_config)
        alternate_servers = restored["plugins"][self.plugin_id]["mcp_servers"]
        self.assertNotIn("enabled", alternate_servers["fable-advisor-python3"])

    def test_legacy_mcp_state_refuses_alternate_setup_and_disables_canonical_only(self) -> None:
        canonical_id = NATIVE.LEGACY_PLUGIN_ID
        alternate_id = self.plugin_id
        initial = {
            "features": {"multi_agent_v2": {}},
            "plugins": {
                canonical_id: {
                    "mcp_servers": {"fable-advisor-python3": {}}
                },
                alternate_id: {
                    "mcp_servers": {
                        "fable-advisor-python3": {"enabled": False}
                    }
                }
            },
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.activate_plugin_cache(canonical_id)
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--advisor-fable",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        legacy = json.loads(state_path.read_text(encoding="utf-8"))
        legacy["schema"] = 6
        legacy["policy_version"] = 6
        legacy.pop("plugin_id")
        state_path.write_text(json.dumps(legacy), encoding="utf-8")

        self.activate_plugin_cache(alternate_id)
        self.write_plugin_inventory(
            self.plugin_inventory_record(
                canonical_id, self.source_plugin_root, enabled=False
            ),
            self.plugin_inventory_record(
                alternate_id, self.source_plugin_root, enabled=True
            ),
        )
        status = self.run_script("--status", "--require-effective", check=False)
        self.assertEqual(status.returncode, 1)
        self.assertIn(f"Plugin identity: {canonical_id}", status.stdout)
        self.assertIn(f"Executing plugin identity: {alternate_id}", status.stdout)
        self.assertIn("Plugin identity mismatch:", status.stdout)

        before_refused_setup = self.read_fake_config()
        refused_setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--advisor-fable",
            "--apply",
            check=False,
        )
        self.assertEqual(refused_setup.returncode, 2)
        self.assertIn("Legacy MCP restore state", refused_setup.stderr)
        self.assertEqual(self.read_fake_config(), before_refused_setup)

        refused_repair = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused_repair.returncode, 2)
        self.assertIn("Saved plugin identity", refused_repair.stderr)
        self.assertEqual(self.read_fake_config(), before_refused_setup)

        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)
        self.assertFalse(state_path.exists())

    def test_prepare_qwen_installs_only_stable_helper_and_prints_hidden_prompt_lane(self) -> None:
        target = (
            self.home
            / "codex-orchestration"
            / "bin"
            / NATIVE.external_credentials.HELPER_NAME
        )
        preview = self.run_script(
            "--prepare-qwen",
            "--qwen-region",
            "china",
            allow_incompatible=False,
        )
        self.assertIn("Dry run only", preview.stdout)
        self.assertFalse(target.exists())

        applied = self.run_script(
            "--prepare-qwen",
            "--qwen-region",
            "china",
            "--apply",
            allow_incompatible=False,
        )
        self.assertTrue(target.is_file())
        packaged = Path(NATIVE.external_credentials.__file__).with_name(
            NATIVE.external_credentials.HELPER_NAME
        )
        self.assertEqual(target.read_bytes(), packaged.read_bytes())
        self.assertTrue(
            "credential: configured" in applied.stdout
            or "secret prompt is hidden" in applied.stdout
        )

    def test_legacy_non_mcp_state_schemas_upgrade_to_seven_without_losing_restore(self) -> None:
        for legacy_schema in (1, 3, 4, 5, 6):
            with self.subTest(schema=legacy_schema):
                setup_arguments = ["--executor-model", "gpt-5.6-luna"]
                self.run_script(*setup_arguments, "--apply")

                state_path = self.home / NATIVE.STATE_FILENAME
                legacy = json.loads(state_path.read_text(encoding="utf-8"))
                original_previous = legacy["previous"]
                legacy["schema"] = legacy_schema
                legacy["policy_version"] = legacy_schema
                legacy.pop("plugin_id")
                if legacy_schema < 3:
                    legacy.pop("planner", None)
                if legacy_schema < 4:
                    legacy.pop("designer", None)
                legacy["managed"]["mode"] = (
                    f"{NATIVE.MANAGED_MARKER}\nlegacy schema {legacy_schema} mode"
                )
                legacy["managed"]["usage"] = (
                    f"{NATIVE.MANAGED_MARKER}\nlegacy schema {legacy_schema} usage"
                )
                state_path.write_text(json.dumps(legacy), encoding="utf-8")

                config = self.read_fake_config()
                feature = config["features"]["multi_agent_v2"]
                feature["multi_agent_mode_hint_text"] = legacy["managed"]["mode"]
                feature["usage_hint_text"] = legacy["managed"]["usage"]
                (self.home / ".fake-user-config.json").write_text(
                    json.dumps(config), encoding="utf-8"
                )

                self.run_script(
                    "--executor-model",
                    "gpt-5.6-luna",
                    "--planner-model",
                    "gpt-5.6-sol",
                    "--designer-model",
                    "gpt-5.6-luna",
                    "--apply",
                )
                upgraded = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(upgraded["schema"], 7)
                self.assertEqual(upgraded["policy_version"], 7)
                self.assertEqual(upgraded["plugin_id"], self.plugin_id)
                self.assertEqual(upgraded["previous"], original_previous)
                self.assertEqual(upgraded["planner"]["model"], "gpt-5.6-sol")
                self.assertEqual(upgraded["designer"]["model"], "gpt-5.6-luna")
                self.run_script("--disable", "--apply")
                self.assertEqual(
                    self.read_fake_config()["features"]["multi_agent_v2"],
                    {"max_concurrent_threads_per_session": 5},
                )
                self.assertFalse(state_path.exists())

    def test_state_policy_version_must_match_schema(self) -> None:
        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        state_path = self.home / NATIVE.STATE_FILENAME
        current = json.loads(state_path.read_text(encoding="utf-8"))

        for schema, wrong_policy in (
            (1, 2),
            (2, 3),
            (3, 4),
            (4, 5),
            (5, 6),
            (6, 1),
            (6, True),
            (7, 6),
        ):
            with self.subTest(schema=schema, policy=wrong_policy):
                state = json.loads(json.dumps(current))
                state["schema"] = schema
                state["policy_version"] = wrong_policy
                if schema < 7:
                    state.pop("plugin_id")
                if schema < 3:
                    state.pop("planner")
                if schema < 4:
                    state.pop("designer")
                state_path.write_text(json.dumps(state), encoding="utf-8")

                status = self.run_script("--status", check=False)
                self.assertEqual(status.returncode, 2)
                self.assertIn("Saved routing state is invalid", status.stderr)
                self.assertNotIn("policy_version", status.stderr)

    def test_legacy_state_schemas_reject_planner_key_even_when_null(self) -> None:
        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        state_path = self.home / NATIVE.STATE_FILENAME
        current = json.loads(state_path.read_text(encoding="utf-8"))

        for schema in (1, 2):
            with self.subTest(schema=schema):
                state = json.loads(json.dumps(current))
                state["schema"] = schema
                state["policy_version"] = schema
                state.pop("plugin_id")
                state["planner"] = None
                state_path.write_text(json.dumps(state), encoding="utf-8")

                status = self.run_script("--status", check=False)
                self.assertEqual(status.returncode, 2)
                self.assertIn("Saved routing state is invalid", status.stderr)

    def test_legacy_state_schemas_reject_designer_key_even_when_null(self) -> None:
        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        state_path = self.home / NATIVE.STATE_FILENAME
        current = json.loads(state_path.read_text(encoding="utf-8"))

        for schema in (1, 2, 3):
            with self.subTest(schema=schema):
                state = json.loads(json.dumps(current))
                state["schema"] = schema
                state["policy_version"] = schema
                state.pop("plugin_id")
                if schema < 3:
                    state.pop("planner")
                state["designer"] = None
                state_path.write_text(json.dumps(state), encoding="utf-8")

                status = self.run_script("--status", check=False)
                self.assertEqual(status.returncode, 2)
                self.assertIn("Saved routing state is invalid", status.stderr)

    def test_schema_one_rejects_fable_and_mcp_fields(self) -> None:
        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        state_path = self.home / NATIVE.STATE_FILENAME
        current = json.loads(state_path.read_text(encoding="utf-8"))
        current["schema"] = 1
        current["policy_version"] = 1
        current.pop("plugin_id")
        current.pop("planner")
        mutations = {
            "fable advisor": lambda state: state.__setitem__(
                "advisor",
                {
                    "kind": "fable",
                    "model": NATIVE.FABLE_MODEL,
                    "effort": "high",
                    "server": "fable-advisor-python3",
                },
            ),
            "managed mcp": lambda state: state["managed"].__setitem__("mcp", None),
            "previous mcp": lambda state: state["previous"].__setitem__("mcp", None),
        }

        for label, mutate in mutations.items():
            with self.subTest(field=label):
                state = json.loads(json.dumps(current))
                mutate(state)
                state_path.write_text(json.dumps(state), encoding="utf-8")

                status = self.run_script("--status", check=False)
                self.assertEqual(status.returncode, 2)
                self.assertIn("Saved routing state is invalid", status.stderr)

    def test_saved_managed_strings_require_exact_marker_line(self) -> None:
        self.run_script("--executor-model", "gpt-5.6-luna", "--apply")
        state_path = self.home / NATIVE.STATE_FILENAME
        current = json.loads(state_path.read_text(encoding="utf-8"))
        mutations = {
            "mode": "arbitrary managed mode",
            "usage": f"{NATIVE.MANAGED_MARKER}-forged suffix",
            "marker only": NATIVE.MANAGED_MARKER,
            "empty body": f"{NATIVE.MANAGED_MARKER}\n   ",
        }

        for label, unmarked in mutations.items():
            with self.subTest(field=label):
                state = json.loads(json.dumps(current))
                field = "usage" if label == "usage" else "mode"
                state["managed"][field] = unmarked
                state_path.write_text(json.dumps(state), encoding="utf-8")

                status = self.run_script("--status", check=False)
                self.assertEqual(status.returncode, 2)
                self.assertIn("Saved routing state is invalid", status.stderr)
                self.assertNotIn(unmarked, status.stderr)

    def test_unknown_state_schema_fails_closed(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema"] = 999
        state_path.write_text(json.dumps(state), encoding="utf-8")

        status = self.run_script("--status", check=False)
        self.assertEqual(status.returncode, 2)
        self.assertIn("Saved routing state is invalid", status.stderr)

    def test_invalid_saved_planner_route_fails_closed(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["planner"]["effort"] = "not valid"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        status = self.run_script("--status", "--require-effective", check=False)
        self.assertEqual(status.returncode, 2)
        self.assertIn("Saved routing state is invalid", status.stderr)

    def test_existing_user_policy_requires_explicit_replace_and_is_restored(self) -> None:
        initial = {
            "features": {
                "multi_agent_v2": {
                    "hide_spawn_agent_metadata": True,
                    "tool_namespace": "custom_namespace",
                    "multi_agent_mode_hint_text": "MY MODE",
                    "usage_hint_text": "MY USAGE",
                }
            }
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )

        refused = self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
            check=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("user-authored mode hint", refused.stderr)

        self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--replace-existing-policy",
            "--apply",
        )
        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)

    def test_boolean_feature_shape_is_restored(self) -> None:
        initial = {"features": {"multi_agent_v2": True}, "keep": "yes"}
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertTrue(feature["enabled"])
        self.assertEqual(feature["tool_namespace"], "agents")
        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)

    def test_boolean_feature_shape_survives_a_seat_update(self) -> None:
        initial = {"features": {"multi_agent_v2": False}, "keep": "yes"}
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "xhigh",
            "--apply",
        )
        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)

    def test_recovered_marker_without_state_can_still_be_disabled(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        (self.home / NATIVE.STATE_FILENAME).unlink()
        self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.run_script("--disable", "--apply")
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertNotIn("multi_agent_mode_hint_text", feature)
        self.assertNotIn("usage_hint_text", feature)
        self.assertFalse(feature["hide_spawn_agent_metadata"])
        self.assertEqual(feature["tool_namespace"], "agents")

    def test_partial_marker_recovery_removes_the_surviving_managed_text(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        (self.home / NATIVE.STATE_FILENAME).unlink()
        config = self.read_fake_config()
        config["features"]["multi_agent_v2"].pop("usage_hint_text")
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.run_script("--disable", "--apply")
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertNotIn("multi_agent_mode_hint_text", feature)
        self.assertNotIn("usage_hint_text", feature)
        self.assertEqual(feature["tool_namespace"], "agents")

    def test_namespace_edit_after_setup_blocks_disable_and_is_preserved(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        config = self.read_fake_config()
        config["features"]["multi_agent_v2"]["tool_namespace"] = "collaboration"
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        status = self.run_script("--status")
        self.assertIn("managed fields conflict", status.stdout)
        self.assertIn("run --repair as a dry run", status.stdout)
        self.assertIn("Seats: suppressed", status.stdout)
        required = self.run_script(
            "--status", "--require-effective", check=False
        )
        self.assertEqual(required.returncode, 1)
        update = self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
            check=False,
        )
        self.assertEqual(update.returncode, 2)
        self.assertIn("changed outside this plugin", update.stderr)
        disabled = self.run_script("--disable", "--apply", check=False)
        self.assertEqual(disabled.returncode, 2)
        self.assertIn("edited after setup", disabled.stderr)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertEqual(feature["tool_namespace"], "collaboration")
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_repair_restores_only_saved_managed_hints_and_keeps_state(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--advisor-fable",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_bytes = state_path.read_bytes()
        state = json.loads(state_bytes)
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\nroute through execution_worker"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\nroute through verification_worker"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        status = self.run_script("--status")
        self.assertIn("managed fields conflict", status.stdout)

        preview = self.run_script("--repair")
        self.assertIn("mode and usage", preview.stdout)
        self.assertIn("Dry run only", preview.stdout)
        self.assertEqual(self.read_fake_config(), config)
        self.assertEqual(state_path.read_bytes(), state_bytes)

        repaired = self.run_script("--repair", "--apply")
        self.assertIn("Native routing policy repaired", repaired.stdout)
        self.assertIn("fully quit and reopen Codex", repaired.stdout)
        self.assertIn("does not change Claude Fable 5 authentication", repaired.stdout)
        after = self.read_fake_config()
        repaired_feature = after["features"]["multi_agent_v2"]
        self.assertEqual(
            repaired_feature["multi_agent_mode_hint_text"],
            state["managed"]["mode"],
        )
        self.assertEqual(
            repaired_feature["usage_hint_text"],
            state["managed"]["usage"],
        )
        self.assertFalse(repaired_feature["hide_spawn_agent_metadata"])
        self.assertEqual(repaired_feature["tool_namespace"], "agents")
        self.assertEqual(after["unrelated"], {"keep": True})
        self.assertEqual(state_path.read_bytes(), state_bytes)
        healthy = self.run_script("--status", "--require-effective")
        self.assertIn("installed and effective", healthy.stdout)

    def test_repair_refuses_unmarked_or_unrelated_control_drift(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent mode"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        feature["tool_namespace"] = "collaboration"
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("only managed mode/usage drift", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

        feature["tool_namespace"] = "agents"
        feature["usage_hint_text"] = "USER AUTHORED USAGE"
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("managed ownership marker", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

    def test_repair_preserves_a_concurrent_user_edit(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent mode"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.home / ".fake-mutate-after-write").touch()
        repaired = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(repaired.returncode, 2)
        self.assertIn("newer edit was preserved", repaired.stderr)
        self.assertEqual(
            self.read_fake_config()["features"]["multi_agent_v2"]["usage_hint_text"],
            "CONCURRENT USER EDIT",
        )
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_repair_requires_state_and_noops_when_already_matching(self) -> None:
        missing = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("requires valid saved plugin state", missing.stderr)
        self.assertFalse((self.home / ".fake-user-config.json").exists())

        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        before_config = self.read_fake_config()
        state_path = self.home / NATIVE.STATE_FILENAME
        before_state = state_path.read_bytes()
        no_op = self.run_script("--repair", "--apply")
        self.assertIn("already matches", no_op.stdout)
        self.assertEqual(self.read_fake_config(), before_config)
        self.assertEqual(state_path.read_bytes(), before_state)

    def test_repair_rolls_back_when_effective_policy_is_overridden(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent mode"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        serialized = json.dumps(config)
        (self.home / ".fake-user-config.json").write_text(
            serialized, encoding="utf-8"
        )
        (self.home / ".fake-effective-config.json").write_text(
            serialized, encoding="utf-8"
        )
        repaired = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(repaired.returncode, 2)
        self.assertIn("did not become effective", repaired.stderr)
        self.assertEqual(self.read_fake_config(), config)
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_repair_refuses_fable_launcher_enablement_drift(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--advisor-fable",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent mode"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        config["plugins"][self.plugin_id]["mcp_servers"][
            "fable-advisor-python3"
        ]["enabled"] = False
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("Fable launcher setting changed", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

    def test_repair_refuses_integer_substitution_for_fable_boolean(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--advisor-fable",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        config["plugins"][self.plugin_id]["mcp_servers"][
            "fable-advisor-python3"
        ]["enabled"] = 1
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("Fable launcher setting changed", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

    def test_repair_detects_a_concurrent_saved_state_edit(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["multi_agent_mode_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent mode"
        )
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.home / ".fake-mutate-state-after-write").touch()
        repaired = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(repaired.returncode, 2)
        self.assertIn("state changed concurrently", repaired.stderr)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(
            state["previous"]["usage"]["value"], "CONCURRENT STATE EDIT"
        )

    def test_repair_detects_same_object_state_byte_replacement(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        config["features"]["multi_agent_v2"]["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        parsed_before = json.loads(state_path.read_text(encoding="utf-8"))
        (self.home / ".fake-reformat-state-after-write").touch()

        repaired = self.run_script("--repair", "--apply", check=False)

        self.assertEqual(repaired.returncode, 2)
        self.assertIn("state changed concurrently", repaired.stderr)
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), parsed_before)

    def test_repair_handles_one_hint_in_a_scalar_conversion_only(self) -> None:
        initial = {"features": {"multi_agent_v2": True}, "keep": "yes"}
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state_bytes = state_path.read_bytes()
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        preview = self.run_script("--repair")
        self.assertIn("saved managed usage hint only", preview.stdout)
        self.run_script("--repair", "--apply")
        self.assertEqual(state_path.read_bytes(), state_bytes)

        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage again"
        )
        feature["unrelated_new_field"] = True
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("table has other changes", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

    def test_repair_refuses_noop_scalar_table_drift(self) -> None:
        initial = {"features": {"multi_agent_v2": True}, "keep": "yes"}
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        config["features"]["multi_agent_v2"]["enabled"] = False
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        refused = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn("already matches", refused.stdout)
        self.assertIn("another owned control", refused.stderr)
        self.assertEqual(self.read_fake_config(), config)

    def test_repair_detects_concurrent_scalar_table_drift(self) -> None:
        initial = {"features": {"multi_agent_v2": True}, "keep": "yes"}
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        self.run_script(
            "--executor-model",
            "gpt-5.6-sol",
            "--executor-effort",
            "medium",
            "--apply",
        )
        config = self.read_fake_config()
        config["features"]["multi_agent_v2"]["usage_hint_text"] = (
            f"{NATIVE.MANAGED_MARKER}\ndifferent usage"
        )
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (self.home / ".fake-mutate-feature-after-write").touch()
        repaired = self.run_script("--repair", "--apply", check=False)
        self.assertEqual(repaired.returncode, 2)
        self.assertIn("newer edit was preserved", repaired.stderr)
        self.assertEqual(
            self.read_fake_config()["features"]["multi_agent_v2"][
                "max_concurrent_threads_per_session"
            ],
            9,
        )
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_disable_without_state_removes_only_each_proven_hint(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        (self.home / NATIVE.STATE_FILENAME).unlink()
        config = self.read_fake_config()
        feature = config["features"]["multi_agent_v2"]
        feature["usage_hint_text"] = "USER USAGE"
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        disabled = self.run_script("--disable", "--apply")
        self.assertIn("1 proven managed hint string", disabled.stdout)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertNotIn("multi_agent_mode_hint_text", feature)
        self.assertEqual(feature["usage_hint_text"], "USER USAGE")
        self.assertFalse(feature["hide_spawn_agent_metadata"])
        self.assertEqual(feature["tool_namespace"], "agents")

    def test_incompatible_client_blocks_setup_but_never_disable(self) -> None:
        old_codex = self.root / "old-codex"
        old_codex.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        old_codex.chmod(0o755)
        refused = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--compat-bin",
            str(old_codex),
            check=False,
            allow_incompatible=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("shared config unreadable", refused.stderr)

        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        disabled = self.run_script(
            "--disable",
            "--apply",
            "--compat-bin",
            str(old_codex),
            allow_incompatible=False,
        )
        self.assertIn("Native routing disabled", disabled.stdout)

    def test_require_effective_rejects_inactive_and_incompatible_status(self) -> None:
        inactive = self.run_script(
            "--status", "--require-effective", check=False
        )
        self.assertEqual(inactive.returncode, 1)
        self.assertIn("Native policy: inactive", inactive.stdout)
        self.assertIn(f"Plugin identity: {self.plugin_id}", inactive.stdout)
        self.assertIn(f"Executing plugin identity: {self.plugin_id}", inactive.stdout)
        self.assertNotIn("Plugin identity mismatch:", inactive.stdout)

        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        old_codex = self.root / "old-status-codex"
        old_codex.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        old_codex.chmod(0o755)
        incompatible = self.run_script(
            "--status",
            "--require-effective",
            "--compat-bin",
            str(old_codex),
            check=False,
        )
        self.assertEqual(incompatible.returncode, 1)
        self.assertIn("incompatible", incompatible.stdout)

    def test_require_effective_rejects_orphaned_managed_personal_role(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        agents = self.home / "agents"
        agents.mkdir()
        orphan_name = "codex_orchestration_executor_012345abcdef"
        (agents / "orphan.toml").write_text(
            "\n".join(
                (
                    NATIVE.CUSTOM_AGENT_MANAGED_MARKER,
                    f'name = "{orphan_name}"',
                    'description = "Managed orphan"',
                    'model = "gpt-5.6-luna"',
                    'developer_instructions = "Stay bounded."',
                    "",
                )
            ),
            encoding="utf-8",
        )
        status = self.run_script(
            "--status", "--require-effective", check=False
        )
        self.assertEqual(status.returncode, 1)
        self.assertIn("Orphaned managed custom agents", status.stdout)
        self.assertIn(orphan_name, status.stdout)

    def test_require_effective_requires_status(self) -> None:
        result = self.run_script("--require-effective", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --status", result.stderr)

    def test_state_from_another_config_is_refused(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["config_file"] = str(self.root / "different" / "config.toml")
        state_path.write_text(json.dumps(state), encoding="utf-8")

        result = self.run_script("--status", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("different Codex config file", result.stderr)

    def test_status_suppresses_seats_when_state_conflicts(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["managed"]["usage"] = (
            f"{NATIVE.MANAGED_MARKER}\nDIFFERENT MANAGED VALUE"
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        status = self.run_script("--status")
        self.assertIn("managed fields conflict", status.stdout)
        self.assertIn("Seats: suppressed", status.stdout)
        self.assertNotIn("Executor: gpt-5.6-luna", status.stdout)

    def test_concurrent_user_edit_after_write_is_preserved(self) -> None:
        (self.home / ".fake-mutate-after-write").touch()
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("newer edit was preserved", result.stderr)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertEqual(feature["usage_hint_text"], "CONCURRENT USER EDIT")
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_concurrent_namespace_edit_after_write_is_preserved(self) -> None:
        (self.home / ".fake-mutate-namespace-after-write").touch()
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("newer edit was preserved", result.stderr)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertEqual(feature["tool_namespace"], "collaboration")
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_state_write_works_when_fchmod_is_unavailable(self) -> None:
        state_path = self.home / "portable-state.json"
        state = {
            "schema": NATIVE.STATE_SCHEMA,
            "managed_by": "codex-orchestration",
            "config_file": str(self.home / "config.toml"),
        }
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        with mock.patch.object(NATIVE.os, "fchmod", None, create=True):
            digest = NATIVE._write_state(state_path, state, identity_guard, None)
        identity_guard.assert_unchanged.assert_called_once_with(
            "routing-state publication"
        )
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)
        self.assertEqual(digest, NATIVE.hashlib.sha256(state_path.read_bytes()).hexdigest())

    @unittest.skipUnless(sys.platform == "win32", "requires Windows rename semantics")
    def test_rename_noreplace_windows_consumes_source_without_overwrite(self) -> None:
        source = self.home / "rename-source"
        destination = self.home / "rename-destination"
        source.write_bytes(b"new")

        NATIVE._rename_noreplace(source, destination)

        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"new")
        self.assertEqual(destination.lstat().st_nlink, 1)

        source.write_bytes(b"blocked")
        with self.assertRaises(FileExistsError):
            NATIVE._rename_noreplace(source, destination)
        self.assertEqual(source.read_bytes(), b"blocked")
        self.assertEqual(destination.read_bytes(), b"new")

    def test_rename_noreplace_linux_native_contract(self) -> None:
        source = self.home / "linux-source"
        destination = self.home / "linux-destination"

        for result, error, expected in (
            (0, 0, None),
            (-1, NATIVE.errno.EEXIST, FileExistsError),
            (-1, NATIVE.errno.ENOSYS, NATIVE.ConfigurationError),
            (-1, NATIVE.errno.EACCES, OSError),
        ):
            with self.subTest(result=result, error=error):
                native_rename = mock.Mock(return_value=result)
                libc = mock.Mock()
                libc.renameat2 = native_rename
                patches = (
                    mock.patch.object(NATIVE.sys, "platform", "linux"),
                    mock.patch.object(NATIVE.ctypes, "CDLL", return_value=libc),
                    mock.patch.object(NATIVE.ctypes, "get_errno", return_value=error),
                    mock.patch.object(NATIVE.os, "rename", side_effect=AssertionError),
                )
                with patches[0], patches[1], patches[2], patches[3]:
                    if expected is None:
                        NATIVE._rename_noreplace(source, destination)
                    else:
                        with self.assertRaises(expected):
                            NATIVE._rename_noreplace(source, destination)
                native_rename.assert_called_once_with(
                    -100,
                    os.fsencode(source),
                    -100,
                    os.fsencode(destination),
                    1,
                )

        with (
            mock.patch.object(NATIVE.sys, "platform", "linux"),
            mock.patch.object(NATIVE.ctypes, "CDLL", return_value=object()),
        ):
            with self.assertRaisesRegex(NATIVE.ConfigurationError, "unavailable"):
                NATIVE._rename_noreplace(source, destination)

    def test_rename_noreplace_macos_native_contract(self) -> None:
        source = self.home / "macos-source"
        destination = self.home / "macos-destination"

        for result, error, expected in (
            (0, 0, None),
            (-1, NATIVE.errno.EEXIST, FileExistsError),
            (-1, NATIVE.errno.EINVAL, NATIVE.ConfigurationError),
            (-1, NATIVE.errno.EIO, OSError),
        ):
            with self.subTest(result=result, error=error):
                native_rename = mock.Mock(return_value=result)
                libc = mock.Mock()
                libc.renamex_np = native_rename
                with (
                    mock.patch.object(NATIVE.sys, "platform", "darwin"),
                    mock.patch.object(NATIVE.ctypes, "CDLL", return_value=libc),
                    mock.patch.object(NATIVE.ctypes, "get_errno", return_value=error),
                    mock.patch.object(NATIVE.os, "rename", side_effect=AssertionError),
                ):
                    if expected is None:
                        NATIVE._rename_noreplace(source, destination)
                    else:
                        with self.assertRaises(expected):
                            NATIVE._rename_noreplace(source, destination)
                native_rename.assert_called_once_with(
                    os.fsencode(source), os.fsencode(destination), 0x00000004
                )

    def test_rename_noreplace_other_platform_fails_closed(self) -> None:
        with mock.patch.object(NATIVE.sys, "platform", "freebsd14"):
            with self.assertRaisesRegex(NATIVE.ConfigurationError, "unsupported"):
                NATIVE._rename_noreplace(
                    self.home / "source", self.home / "destination"
                )

    def test_state_publication_and_restore_never_use_hard_links(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        state = self.valid_state()
        with mock.patch.object(NATIVE.os, "link") as hard_link:
            first_digest = NATIVE._write_state(
                state_path, state, identity_guard, None
            )
            NATIVE._write_state(
                state_path,
                self.valid_state("gpt-5.6-terra"),
                identity_guard,
                first_digest,
            )
            hard_link.assert_not_called()
        self.assertEqual(state_path.lstat().st_nlink, 1)

    def test_existing_state_capture_failure_preserves_canonical(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior = self.valid_state()
        prior_digest = NATIVE._write_state(
            state_path, prior, identity_guard, None
        )
        prior_bytes = state_path.read_bytes()

        with mock.patch.object(
            NATIVE, "_rename_noreplace", side_effect=PermissionError("capture")
        ):
            with self.assertRaisesRegex(NATIVE.ConfigurationError, "changed concurrently"):
                NATIVE._write_state(
                    state_path,
                    self.valid_state("gpt-5.6-terra"),
                    identity_guard,
                    prior_digest,
                )

        self.assertEqual(state_path.read_bytes(), prior_bytes)
        self.assertEqual(state_path.lstat().st_nlink, 1)

    def test_existing_state_capture_race_preserves_both_pathnames(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        prior_bytes = state_path.read_bytes()
        concurrent_bytes = b"concurrent capture destination"
        real_capture_path = NATIVE._private_state_capture_path
        raced_path: Path | None = None

        def occupy_capture_destination(path: Path) -> Path:
            nonlocal raced_path
            raced_path = real_capture_path(path)
            raced_path.write_bytes(concurrent_bytes)
            return raced_path

        with mock.patch.object(
            NATIVE,
            "_private_state_capture_path",
            side_effect=occupy_capture_destination,
        ):
            with self.assertRaisesRegex(
                NATIVE.ConfigurationError, "changed concurrently"
            ):
                NATIVE._write_state(
                    state_path,
                    self.valid_state("gpt-5.6-terra"),
                    identity_guard,
                    prior_digest,
                )

        self.assertIsNotNone(raced_path)
        self.assertEqual(state_path.read_bytes(), prior_bytes)
        self.assertEqual(raced_path.read_bytes(), concurrent_bytes)

    def test_capture_real_rename_then_interrupt_continues_transaction(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def interrupt_after_capture(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            real_rename(source, destination)
            if calls == 1:
                raise KeyboardInterrupt("after capture rename")

        with mock.patch.object(
            NATIVE, "_rename_noreplace", side_effect=interrupt_after_capture
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertEqual(calls, 2)
        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), replacement)

    def test_write_capture_helper_return_interrupt_keeps_owned_path(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")
        real_capture = NATIVE._capture_expected_state
        owned_capture: Path | None = None

        def interrupt_after_capture(
            path: Path, captured: Path, expected_digest: str
        ) -> None:
            nonlocal owned_capture
            owned_capture = captured
            real_capture(path, captured, expected_digest)
            raise KeyboardInterrupt("after capture helper return")

        with mock.patch.object(
            NATIVE, "_capture_expected_state", side_effect=interrupt_after_capture
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertIsNotNone(owned_capture)
        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), replacement)
        self.assertFalse(owned_capture.exists())

    def test_remove_capture_helper_return_interrupt_keeps_owned_path(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        real_capture = NATIVE._capture_expected_state
        owned_capture: Path | None = None

        def interrupt_after_capture(
            path: Path, captured: Path, expected_digest: str
        ) -> None:
            nonlocal owned_capture
            owned_capture = captured
            real_capture(path, captured, expected_digest)
            raise KeyboardInterrupt("after capture helper return")

        with mock.patch.object(
            NATIVE, "_capture_expected_state", side_effect=interrupt_after_capture
        ):
            NATIVE._remove_state(state_path, identity_guard, prior_digest)

        self.assertIsNotNone(owned_capture)
        self.assertFalse(state_path.exists())
        self.assertFalse(owned_capture.exists())

    def test_capture_validation_interrupt_continues_with_known_artifact(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")
        real_assert = NATIVE._assert_state_digest

        def interrupt_after_validation(path: Path, digest: str | None) -> None:
            real_assert(path, digest)
            if path.name.endswith(".cas-backup"):
                raise KeyboardInterrupt("after capture validation")

        with mock.patch.object(
            NATIVE, "_assert_state_digest", side_effect=interrupt_after_validation
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), replacement)
        self.assertEqual(
            list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup")), []
        )

    def test_absent_publication_real_rename_then_interrupt_is_committed(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        state = self.valid_state()
        real_rename = NATIVE._rename_noreplace

        def interrupt_after_publish(source: Path, destination: Path) -> None:
            real_rename(source, destination)
            raise KeyboardInterrupt("after publication rename")

        with (
            mock.patch.object(
                NATIVE, "_rename_noreplace", side_effect=interrupt_after_publish
            ),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            digest = NATIVE._write_state(state_path, state, identity_guard, None)

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), state)

    def test_replacement_real_rename_then_interrupt_is_committed(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def interrupt_after_publication(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            real_rename(source, destination)
            if calls == 2:
                raise KeyboardInterrupt("after replacement rename")

        with (
            mock.patch.object(
                NATIVE,
                "_rename_noreplace",
                side_effect=interrupt_after_publication,
            ),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertEqual(calls, 2)
        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), replacement)

    def test_indeterminate_replacement_preserves_staged_and_recovery_evidence(
        self,
    ) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        prior_bytes = state_path.read_bytes()
        replacement = self.valid_state("gpt-5.6-terra")
        replacement_bytes = (
            json.dumps(replacement, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        ambiguous = self.valid_state("gpt-5.6-sol")
        ambiguous_bytes = (
            json.dumps(ambiguous, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def create_ambiguous_publication(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                real_rename(source, destination)
                return
            state_path.write_bytes(ambiguous_bytes)
            raise KeyboardInterrupt("ambiguous publication outcome")

        with mock.patch.object(
            NATIVE,
            "_rename_noreplace",
            side_effect=create_ambiguous_publication,
        ):
            with self.assertRaisesRegex(
                NATIVE.StateTransactionIndeterminateError,
                "all paths were preserved",
            ) as raised:
                NATIVE._write_state(
                    state_path, replacement, identity_guard, prior_digest
                )

        self.assertNotIn("prior state was restored", str(raised.exception))
        self.assertEqual(state_path.read_bytes(), ambiguous_bytes)
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), prior_bytes)
        staged = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.tmp"))
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].read_bytes(), replacement_bytes)

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_first_install_publish_rename_interrupt_keeps_config_state_paired(
        self,
    ) -> None:
        real_rename = NATIVE._rename_noreplace

        def interrupt_after_publish(source: Path, destination: Path) -> None:
            real_rename(source, destination)
            raise KeyboardInterrupt("after first publication rename")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                NATIVE, "_rename_noreplace", side_effect=interrupt_after_publish
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", io.StringIO()),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 0)
        state = NATIVE._read_state(self.home / NATIVE.STATE_FILENAME)
        self.assertIsNotNone(state)
        self.assertTrue(
            NATIVE._managed_matches(
                state,
                NATIVE._current_values(self.read_fake_config(), self.plugin_id),
            )
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_update_publish_rename_interrupt_keeps_config_state_paired(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def interrupt_after_replacement(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            real_rename(source, destination)
            if calls == 2:
                raise KeyboardInterrupt("after replacement publication rename")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                NATIVE, "_rename_noreplace", side_effect=interrupt_after_replacement
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", io.StringIO()),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 0)
        self.assertEqual(calls, 2)
        state = NATIVE._read_state(self.home / NATIVE.STATE_FILENAME)
        self.assertIsNotNone(state)
        self.assertEqual(state["executor"]["model"], "gpt-5.6-terra")
        self.assertTrue(
            NATIVE._managed_matches(
                state,
                NATIVE._current_values(self.read_fake_config(), self.plugin_id),
            )
        )

    def test_existing_state_publication_failure_restores_prior_bytes(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior = self.valid_state()
        prior_digest = NATIVE._write_state(
            state_path, prior, identity_guard, None
        )
        prior_bytes = state_path.read_bytes()
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def fail_publication_then_restore(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("publication")
            real_rename(source, destination)

        with mock.patch.object(
            NATIVE, "_rename_noreplace", side_effect=fail_publication_then_restore
        ):
            with self.assertRaisesRegex(NATIVE.ConfigurationError, "appeared during publication"):
                NATIVE._write_state(
                    state_path,
                    self.valid_state("gpt-5.6-terra"),
                    identity_guard,
                    prior_digest,
                )

        self.assertEqual(calls, 3)
        self.assertEqual(state_path.read_bytes(), prior_bytes)
        self.assertEqual(state_path.lstat().st_nlink, 1)

    def test_prior_capture_cleanup_failure_keeps_published_state(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        original_unlink = Path.unlink
        capture_unlinks = 0

        def fail_final_capture_unlink(candidate: Path, missing_ok: bool = False) -> None:
            nonlocal capture_unlinks
            if candidate.name.endswith(".cas-backup"):
                capture_unlinks += 1
                if capture_unlinks == 2:
                    raise PermissionError("cleanup")
            original_unlink(candidate, missing_ok=missing_ok)

        stderr = io.StringIO()
        with (
            mock.patch.object(Path, "unlink", new=fail_final_capture_unlink),
            mock.patch.object(sys, "stderr", stderr),
        ):
            digest = NATIVE._write_state(
                state_path,
                self.valid_state("gpt-5.6-terra"),
                identity_guard,
                prior_digest,
            )

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(state_path.lstat().st_nlink, 1)
        self.assertIn("WARNING: routing state was published", stderr.getvalue())
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].lstat().st_nlink, 1)

    def test_absent_state_directory_fsync_failure_is_committed(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        state = self.valid_state()
        stderr = io.StringIO()

        with (
            mock.patch.object(
                NATIVE,
                "_fsync_directory",
                side_effect=PermissionError("durability"),
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            digest = NATIVE._write_state(state_path, state, identity_guard, None)

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), state)
        self.assertIn("routing state was published", stderr.getvalue())
        self.assertIn("durability could not be confirmed", stderr.getvalue())

    def test_interrupt_like_postcommit_maintenance_cannot_escape(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        state = self.valid_state()

        with (
            mock.patch.object(
                NATIVE,
                "_fsync_directory",
                side_effect=KeyboardInterrupt("maintenance interrupt"),
            ),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            digest = NATIVE._write_state(state_path, state, identity_guard, None)

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), state)

    def test_replacement_directory_fsync_failure_retains_prior_capture(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        prior_bytes = state_path.read_bytes()
        replacement = self.valid_state("gpt-5.6-terra")
        stderr = io.StringIO()

        with (
            mock.patch.object(
                NATIVE,
                "_fsync_directory",
                side_effect=PermissionError("durability"),
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(NATIVE._read_state(state_path), replacement)
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), prior_bytes)
        self.assertIn(f"recovery remains at {captures[0]}", stderr.getvalue())

    def test_removal_directory_fsync_failure_retains_prior_capture(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        prior_bytes = state_path.read_bytes()
        stderr = io.StringIO()

        with (
            mock.patch.object(
                NATIVE,
                "_fsync_directory",
                side_effect=PermissionError("durability"),
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            NATIVE._remove_state(state_path, identity_guard, prior_digest)

        self.assertFalse(state_path.exists())
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), prior_bytes)
        self.assertIn("routing state was removed", stderr.getvalue())
        self.assertIn(f"recovery remains at {captures[0]}", stderr.getvalue())

    def test_replacement_cleanup_fsync_failure_does_not_fail_publication(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")
        stderr = io.StringIO()

        with (
            mock.patch.object(
                NATIVE,
                "_fsync_directory",
                side_effect=[None, PermissionError("cleanup durability")],
            ),
            mock.patch.object(sys, "stderr", stderr),
        ):
            digest = NATIVE._write_state(
                state_path, replacement, identity_guard, prior_digest
            )

        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], digest)
        self.assertEqual(
            list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup")), []
        )
        self.assertIn("cleanup directory durability", stderr.getvalue())

    def test_removal_cleanup_failure_does_not_restore_canonical(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        original_unlink = Path.unlink
        capture_unlinks = 0

        def fail_capture_unlink(candidate: Path, missing_ok: bool = False) -> None:
            nonlocal capture_unlinks
            if candidate.name.endswith(".cas-backup"):
                capture_unlinks += 1
                if capture_unlinks == 2:
                    raise PermissionError("cleanup")
            original_unlink(candidate, missing_ok=missing_ok)

        stderr = io.StringIO()
        with (
            mock.patch.object(Path, "unlink", new=fail_capture_unlink),
            mock.patch.object(sys, "stderr", stderr),
        ):
            NATIVE._remove_state(state_path, identity_guard, prior_digest)

        self.assertFalse(state_path.exists())
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertIn("routing state was removed", stderr.getvalue())
        self.assertIn("recovery artifact remains", stderr.getvalue())

    def test_complete_restore_failure_retains_captured_prior_bytes(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        prior_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        prior_bytes = state_path.read_bytes()

        real_rename = NATIVE._rename_noreplace
        calls = 0

        def capture_then_block(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                real_rename(source, destination)
                return
            raise PermissionError("blocked")

        with mock.patch.object(
            NATIVE, "_rename_noreplace", side_effect=capture_then_block
        ):
            with self.assertRaisesRegex(
                NATIVE.ConfigurationError, "restoration failed"
            ):
                NATIVE._write_state(
                    state_path,
                    self.valid_state("gpt-5.6-terra"),
                    identity_guard,
                    prior_digest,
                )

        self.assertFalse(state_path.exists())
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), prior_bytes)
        self.assertEqual(captures[0].lstat().st_nlink, 1)

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_publication_failure_restores_state_and_rolls_back_config(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        prior_state = state_path.read_bytes()
        prior_config = self.read_fake_config()
        real_rename = NATIVE._rename_noreplace
        calls = 0

        def fail_publication_then_restore(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("forced publication failure")
            real_rename(source, destination)

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                NATIVE,
                "_rename_noreplace",
                side_effect=fail_publication_then_restore,
            ),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 3)
        self.assertIn("config write was rolled back", stderr.getvalue())
        self.assertEqual(self.read_fake_config(), prior_config)
        self.assertEqual(state_path.read_bytes(), prior_state)
        self.assertEqual(state_path.lstat().st_nlink, 1)

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_postcommit_fsync_and_stderr_failures_keep_config_state_consistent(
        self,
    ) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME

        class BrokenStderr:
            def __init__(self) -> None:
                self.writes = 0

            def write(self, _message: str) -> None:
                self.writes += 1
                raise BrokenPipeError("closed diagnostic stream")

            def flush(self) -> None:
                raise BrokenPipeError("closed diagnostic stream")

        real_app_server_close = NATIVE.AppServer.close

        def close_app_server_streams(app: NATIVE.AppServer) -> None:
            real_app_server_close(app)
            app._stdin.close()
            app._stdout.close()

        def invoke(*arguments: str) -> tuple[int, str, BrokenStderr]:
            stdout = io.StringIO()
            stderr = BrokenStderr()
            argv = [
                str(self.installed_script),
                "--codex-bin",
                str(self.codex),
                "--codex-home",
                str(self.home),
                "--allow-incompatible-client",
                *arguments,
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
                mock.patch.dict(os.environ, self.fake_env()),
                mock.patch.object(
                    NATIVE.AppServer,
                    "close",
                    new=close_app_server_streams,
                ),
                mock.patch.object(
                    NATIVE,
                    "_fsync_directory",
                    side_effect=PermissionError("forced directory fsync failure"),
                ),
            ):
                result = NATIVE.main()
            return result, stdout.getvalue(), stderr

        result, stdout, stderr = invoke(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.assertEqual(result, 0)
        self.assertIn("Native routing policy installed", stdout)
        self.assertGreater(stderr.writes, 0)
        state = NATIVE._read_state(state_path)
        self.assertIsNotNone(state)
        self.assertTrue(
            NATIVE._managed_matches(
                state,
                NATIVE._current_values(self.read_fake_config(), self.plugin_id),
            )
        )

        prior_bytes = state_path.read_bytes()
        result, stdout, stderr = invoke(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.assertEqual(result, 0)
        self.assertIn("Native routing policy installed", stdout)
        self.assertGreater(stderr.writes, 0)
        state = NATIVE._read_state(state_path)
        self.assertIsNotNone(state)
        self.assertEqual(state["executor"]["model"], "gpt-5.6-terra")
        self.assertTrue(
            NATIVE._managed_matches(
                state,
                NATIVE._current_values(self.read_fake_config(), self.plugin_id),
            )
        )
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].read_bytes(), prior_bytes)

        removed_bytes = state_path.read_bytes()
        result, stdout, stderr = invoke("--disable", "--apply")
        self.assertEqual(result, 0)
        self.assertIn("Native routing disabled", stdout)
        self.assertGreater(stderr.writes, 0)
        self.assertFalse(state_path.exists())
        self.assertEqual(
            self.read_fake_config()["features"]["multi_agent_v2"],
            {"max_concurrent_threads_per_session": 5},
        )
        captures = list(self.home.glob(f".{NATIVE.STATE_FILENAME}.*.cas-backup"))
        self.assertEqual(len(captures), 2)
        self.assertIn(removed_bytes, [capture.read_bytes() for capture in captures])

    def test_windows_transaction_mutex_uses_exact_global_name_and_fails_closed(
        self,
    ) -> None:
        lock_identity = os.path.normcase(os.path.realpath(self.home))
        name_hash = NATIVE.hashlib.sha256(lock_identity.encode("utf-8")).hexdigest()
        expected_name = f"Global\\CodexOrchestrationNativeRouting-{name_hash}"

        for create_result, wait_result, expected_error in (
            (123, 0x00000000, None),
            (0, 0x00000000, "Could not create"),
            (123, 0xFFFFFFFF, "transaction is active"),
        ):
            with self.subTest(
                create_result=create_result,
                wait_result=wait_result,
            ):
                kernel32 = mock.Mock()
                kernel32.CreateMutexW.return_value = create_result
                kernel32.WaitForSingleObject.return_value = wait_result
                with (
                    mock.patch.object(NATIVE.os, "name", "nt"),
                    mock.patch.object(
                        NATIVE.ctypes,
                        "WinDLL",
                        create=True,
                        return_value=kernel32,
                    ),
                ):
                    if expected_error is None:
                        with NATIVE._transaction_directory_lock(self.home):
                            pass
                    else:
                        with self.assertRaisesRegex(
                            NATIVE.ConfigurationError, expected_error
                        ):
                            with NATIVE._transaction_directory_lock(self.home):
                                pass

                kernel32.CreateMutexW.assert_called_once_with(
                    None, False, expected_name
                )
                self.assertNotIn("Local\\", expected_name)

    def test_transaction_lock_rejects_a_concurrent_process(self) -> None:
        probe = textwrap.dedent(
            """\
            import importlib.util
            from pathlib import Path
            import sys

            script = Path(sys.argv[1])
            spec = importlib.util.spec_from_file_location("native_lock_probe", script)
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(script.parent))
            spec.loader.exec_module(module)
            try:
                with module._transaction_directory_lock(Path(sys.argv[2])):
                    raise SystemExit(0)
            except module.ConfigurationError as exc:
                print(str(exc), file=sys.stderr)
                raise SystemExit(23)
            """
        )
        with NATIVE._transaction_directory_lock(self.home):
            result = subprocess.run(
                [sys.executable, "-c", probe, str(SCRIPT), str(self.home)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn("native-routing transaction is active", result.stderr)

    def test_absent_state_cas_refuses_an_intervening_valid_state(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        state = self.valid_state()
        state_path.write_text(json.dumps(state), encoding="utf-8")
        identity_guard = mock.Mock(spec=["assert_unchanged"])

        with self.assertRaisesRegex(
            NATIVE.ConfigurationError, "changed concurrently"
        ):
            NATIVE._write_state(state_path, state, identity_guard, None)

    def test_existing_state_cas_refuses_replacement_and_removal(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        original = self.valid_state()
        original_digest = NATIVE._write_state(
            state_path, original, identity_guard, None
        )
        intervening = self.valid_state("gpt-5.6-terra")
        state_path.write_text(json.dumps(intervening), encoding="utf-8")

        with self.assertRaisesRegex(
            NATIVE.ConfigurationError, "changed concurrently"
        ):
            NATIVE._write_state(
                state_path, original, identity_guard, original_digest
            )
        with self.assertRaisesRegex(
            NATIVE.ConfigurationError, "changed concurrently"
        ):
            NATIVE._remove_state(state_path, identity_guard, original_digest)
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8")), intervening
        )

    def test_state_cas_rechecks_mutation_during_identity_guard(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        original = self.valid_state()
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        original_digest = NATIVE._write_state(
            state_path, original, identity_guard, None
        )
        intervening = self.valid_state("gpt-5.6-terra")
        intervening_bytes = json.dumps(intervening).encode("utf-8")

        def mutate_during_guard(_phase: str) -> None:
            state_path.write_bytes(intervening_bytes)

        identity_guard.assert_unchanged.side_effect = mutate_during_guard
        with self.assertRaisesRegex(NATIVE.ConfigurationError, "changed concurrently"):
            NATIVE._remove_state(state_path, identity_guard, original_digest)
        self.assertEqual(state_path.read_bytes(), intervening_bytes)

        identity_guard.assert_unchanged.side_effect = None
        state_path.write_bytes(json.dumps(original).encode("utf-8"))
        original_digest = NATIVE._read_state_snapshot(state_path)[1]
        identity_guard.assert_unchanged.side_effect = mutate_during_guard
        with self.assertRaisesRegex(NATIVE.ConfigurationError, "changed concurrently"):
            NATIVE._write_state(
                state_path, self.valid_state("gpt-5.6-sol"), identity_guard, original_digest
            )
        self.assertEqual(state_path.read_bytes(), intervening_bytes)

    def test_absent_state_publication_never_overwrites_intervening_state(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        replacement = self.valid_state("gpt-5.6-terra")
        replacement_bytes = json.dumps(replacement).encode("utf-8")
        identity_guard = mock.Mock(spec=["assert_unchanged"])

        def publish_during_guard(_phase: str) -> None:
            state_path.write_bytes(replacement_bytes)

        identity_guard.assert_unchanged.side_effect = publish_during_guard
        with self.assertRaisesRegex(NATIVE.ConfigurationError, "changed concurrently"):
            NATIVE._write_state(state_path, self.valid_state(), identity_guard, None)
        self.assertEqual(state_path.read_bytes(), replacement_bytes)


    def test_state_cas_preserves_write_after_captured_validation(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        original = self.valid_state()
        original_digest = NATIVE._write_state(
            state_path, original, identity_guard, None
        )
        newer_bytes = json.dumps(self.valid_state("gpt-5.6-terra")).encode("utf-8")
        original_assert = NATIVE._assert_state_digest

        def mutate_after_validation(path: Path, digest: str | None) -> None:
            original_assert(path, digest)
            if path.name.endswith(".cas-backup"):
                state_path.write_bytes(newer_bytes)

        with mock.patch.object(
            NATIVE, "_assert_state_digest", side_effect=mutate_after_validation
        ):
            with self.assertRaisesRegex(
                NATIVE.ConfigurationError, "was not overwritten"
            ):
                NATIVE._write_state(
                    state_path,
                    self.valid_state("gpt-5.6-sol"),
                    identity_guard,
                    original_digest,
                )
        self.assertEqual(state_path.read_bytes(), newer_bytes)
        self.assertEqual(len(list(self.home.glob("*.cas-backup"))), 1)

    def test_state_cas_preserves_write_after_remove_validation(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        original_digest = NATIVE._write_state(
            state_path, self.valid_state(), identity_guard, None
        )
        newer_bytes = json.dumps(self.valid_state("gpt-5.6-terra")).encode("utf-8")
        original_assert = NATIVE._assert_state_digest

        def mutate_after_validation(path: Path, digest: str | None) -> None:
            original_assert(path, digest)
            if path.name.endswith(".cas-backup"):
                state_path.write_bytes(newer_bytes)

        with mock.patch.object(
            NATIVE, "_assert_state_digest", side_effect=mutate_after_validation
        ):
            with self.assertRaisesRegex(
                NATIVE.ConfigurationError, "appeared during removal"
            ):
                NATIVE._remove_state(state_path, identity_guard, original_digest)
        self.assertEqual(state_path.read_bytes(), newer_bytes)

    def test_state_cas_digest_progression_binds_write_remove_and_rollback(self) -> None:
        state_path = self.home / NATIVE.STATE_FILENAME
        identity_guard = mock.Mock(spec=["assert_unchanged"])
        original = self.valid_state()
        original_digest = NATIVE._write_state(
            state_path, original, identity_guard, None
        )
        replacement = self.valid_state("gpt-5.6-terra")

        replacement_digest = NATIVE._write_state(
            state_path, replacement, identity_guard, original_digest
        )
        self.assertEqual(
            NATIVE._read_state_snapshot(state_path)[1], replacement_digest
        )
        NATIVE._remove_state(state_path, identity_guard, replacement_digest)
        self.assertFalse(state_path.exists())
        republished_digest = NATIVE._write_state(
            state_path, replacement, identity_guard, None
        )
        rollback_digest = NATIVE._write_state(
            state_path, original, identity_guard, republished_digest
        )
        self.assertEqual(rollback_digest, original_digest)
        self.assertEqual(NATIVE._read_state_snapshot(state_path)[1], original_digest)

    def test_effective_project_override_is_reported_and_blocks_setup(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        )
        effective = self.read_fake_config()
        effective["features"]["multi_agent_v2"]["tool_namespace"] = "collaboration"
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(effective), encoding="utf-8"
        )
        status = self.run_script("--status")
        self.assertIn("installed but overridden", status.stdout)
        self.assertIn("not routed through agents", status.stdout)

        update = self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
            check=False,
        )
        self.assertEqual(update.returncode, 2)
        self.assertIn("effective readback did not match", update.stderr)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["executor"]["model"], "gpt-5.6-luna")

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_first_install_interrupt_before_state_rollback_compensates_forward(
        self,
    ) -> None:
        self.assert_effective_state_rollback_interrupt_pairing(
            update=False, after_state_commit=False
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_first_install_interrupt_after_state_rollback_keeps_prior_pair(self) -> None:
        self.assert_effective_state_rollback_interrupt_pairing(
            update=False, after_state_commit=True
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_update_interrupt_before_state_rollback_compensates_forward(self) -> None:
        self.assert_effective_state_rollback_interrupt_pairing(
            update=True, after_state_commit=False
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_update_interrupt_after_state_rollback_keeps_prior_pair(self) -> None:
        self.assert_effective_state_rollback_interrupt_pairing(
            update=True, after_state_commit=True
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_effective_state_rollback_compensation_failure_is_actionable(self) -> None:
        base_config = {
            "features": {
                "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
            },
            "unrelated": {"keep": True},
        }
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(base_config), encoding="utf-8"
        )
        real_batch_write = NATIVE._batch_write
        calls = 0

        def batch_write(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls < 3:
                return real_batch_write(*args, **kwargs)
            raise KeyboardInterrupt("forced compensation failure")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(NATIVE, "_batch_write", side_effect=batch_write),
            mock.patch.object(
                NATIVE,
                "_remove_state",
                side_effect=KeyboardInterrupt("before state rollback"),
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 3)
        self.assertIn("forward config compensation failed", stderr.getvalue())
        self.assertIn("may be inconsistent", stderr.getvalue())
        self.assertIn("Run status", stderr.getvalue())
        self.assertEqual(self.read_fake_config(), base_config)
        self.assertEqual(
            NATIVE._read_state(self.home / NATIVE.STATE_FILENAME)["executor"]["model"],
            "gpt-5.6-luna",
        )

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_effective_state_rollback_unknown_digest_is_actionable(self) -> None:
        base_config = {
            "features": {
                "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
            },
            "unrelated": {"keep": True},
        }
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(base_config), encoding="utf-8"
        )
        state_path = self.home / NATIVE.STATE_FILENAME
        unknown_state = self.valid_state("gpt-5.6-sol")

        def publish_unknown_state(*_args: object, **_kwargs: object) -> None:
            state_path.write_text(
                json.dumps(unknown_state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            raise KeyboardInterrupt("unknown rollback outcome")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(NATIVE, "_remove_state", side_effect=publish_unknown_state),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertIn("matched neither the exact prior", stderr.getvalue())
        self.assertIn("may be inconsistent", stderr.getvalue())
        self.assertIn("Run status", stderr.getvalue())
        self.assertEqual(self.read_fake_config(), base_config)
        self.assertEqual(NATIVE._read_state(state_path), unknown_state)

    def test_effective_readback_rejects_unexpected_rollback_status(self) -> None:
        effective = {
            "features": {
                "multi_agent_v2": {
                    "hide_spawn_agent_metadata": True,
                    "tool_namespace": "collaboration",
                }
            }
        }
        (self.home / ".fake-effective-config.json").write_text(
            json.dumps(effective), encoding="utf-8"
        )
        real_batch_write = NATIVE._batch_write
        calls = 0

        def batch_write(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_batch_write(*args, **kwargs)
            return {"status": "unexpected", "version": "sha256:unknown"}

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(NATIVE, "_batch_write", side_effect=batch_write),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 2)
        self.assertIn("automatic rollback failed", stderr.getvalue())
        self.assertIn("unexpected rollback status", stderr.getvalue())
        self.assertTrue((self.home / NATIVE.STATE_FILENAME).exists())

    def test_ok_overridden_restores_every_owned_field(self) -> None:
        initial = {
            "features": {
                "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
            },
            "unrelated": {"keep": True},
        }
        (self.home / ".fake-ok-overridden").touch()
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("user config change was rolled back", result.stderr)
        self.assertNotIn("automatic rollback failed", result.stderr)
        self.assertEqual(self.read_fake_config(), initial)
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_ok_overridden_rollback_failure_is_reported_truthfully(self) -> None:
        (self.home / ".fake-ok-overridden").touch()
        (self.home / ".fake-fail-overridden-rollback").touch()
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--apply",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("automatic rollback failed", result.stderr)
        self.assertIn("user layer may still contain", result.stderr)
        self.assertNotIn("user config change was rolled back", result.stderr)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertEqual(feature["tool_namespace"], "agents")
        self.assertIn(NATIVE.MANAGED_MARKER, feature["usage_hint_text"])
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_state_failure_rejects_unexpected_rollback_status(self) -> None:
        real_batch_write = NATIVE._batch_write
        calls = 0

        def batch_write(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_batch_write(*args, **kwargs)
            return {"status": "unexpected", "version": "sha256:unknown"}

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                NATIVE,
                "_write_state",
                side_effect=NATIVE.ConfigurationError("forced state failure"),
            ),
            mock.patch.object(NATIVE, "_batch_write", side_effect=batch_write),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 2)
        self.assertIn("may still contain managed fields", stderr.getvalue())
        self.assertIn("unexpected rollback status", stderr.getvalue())
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertIn(NATIVE.MANAGED_MARKER, feature["usage_hint_text"])

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_state_interrupt_repropagates_only_after_config_rollback(self) -> None:
        real_batch_write = NATIVE._batch_write
        events: list[str] = []

        def batch_write(*args: object, **kwargs: object) -> dict[str, object]:
            events.append("config-write")
            return real_batch_write(*args, **kwargs)

        def interrupt_state_write(*_args: object, **_kwargs: object) -> str:
            events.append("state-interrupt")
            raise KeyboardInterrupt("forced pre-state interrupt")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(NATIVE, "_batch_write", side_effect=batch_write),
            mock.patch.object(NATIVE, "_write_state", side_effect=interrupt_state_write),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", io.StringIO()),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "forced pre-state"):
                NATIVE.main()

        self.assertEqual(events, ["config-write", "state-interrupt", "config-write"])
        self.assertEqual(
            self.read_fake_config(),
            {
                "features": {
                    "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
                },
                "unrelated": {"keep": True},
            },
        )
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    @unittest.skipUnless(os.name == "posix", "fake Codex fixture is executable on POSIX")
    def test_state_interrupt_rollback_interrupt_is_actionable_error(self) -> None:
        real_batch_write = NATIVE._batch_write
        calls = 0

        def batch_write(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_batch_write(*args, **kwargs)
            raise KeyboardInterrupt("forced rollback interrupt")

        argv = [
            str(self.installed_script),
            "--codex-bin",
            str(self.codex),
            "--codex-home",
            str(self.home),
            "--allow-incompatible-client",
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "high",
            "--apply",
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(NATIVE, "_batch_write", side_effect=batch_write),
            mock.patch.object(
                NATIVE,
                "_write_state",
                side_effect=KeyboardInterrupt("forced pre-state interrupt"),
            ),
            mock.patch.object(sys, "stdout", io.StringIO()),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.dict(os.environ, self.fake_env()),
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 2)
        self.assertIn("state persistence and automatic rollback both failed", stderr.getvalue())
        self.assertIn("may still contain managed fields", stderr.getvalue())
        self.assertIn("Run status before continuing", stderr.getvalue())
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertIn(NATIVE.MANAGED_MARKER, feature["usage_hint_text"])
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_custom_agent_route_and_optional_advisor(self) -> None:
        self.write_personal_agent("codex_orchestration_executor")
        self.write_personal_agent("codex_orchestration_advisor")
        result = self.run_script(
            "--executor-agent",
            "codex_orchestration_executor",
            "--advisor-agent",
            "codex_orchestration_advisor",
            "--apply",
        )
        self.assertIn("custom agent codex_orchestration_executor", result.stdout)
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        usage = feature["usage_hint_text"]
        self.assertIn('agent_type = "codex_orchestration_executor"', usage)
        self.assertIn('agent_type = "codex_orchestration_advisor"', usage)
        self.assertIn("No Designer route is configured", usage)

    def test_custom_planner_shadow_and_orphan_tracking(self) -> None:
        name = "codex_orchestration_planner_012345abcdef"
        self.write_personal_agent(name, managed=True)
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-agent",
            name,
            "--apply",
        )
        self.assertIn(f"Planner: custom agent {name}", setup.stdout)
        healthy = self.run_script("--status", "--require-effective")
        self.assertIn("Orphaned managed custom agents: none", healthy.stdout)

        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--apply",
        )
        orphaned = self.run_script(
            "--status", "--require-effective", check=False
        )
        self.assertEqual(orphaned.returncode, 1)
        self.assertIn(name, orphaned.stdout)

        # Re-selecting the role is still refused if the project shadows it.
        project_agents = self.root / ".codex" / "agents"
        project_agents.mkdir(parents=True)
        (project_agents / "shadow.toml").write_text(
            "\n".join(
                (
                    f'name = "{name}"',
                    'description = "Shadow"',
                    'model = "other-model"',
                    'developer_instructions = "Shadow the planner."',
                    "",
                )
            ),
            encoding="utf-8",
        )
        shadowed = subprocess.run(
            [
                sys.executable,
                str(self.installed_script),
                "--codex-bin",
                str(self.codex),
                "--codex-home",
                str(self.home),
                "--allow-incompatible-client",
                "--executor-model",
                "gpt-5.6-luna",
                "--planner-agent",
                name,
                "--apply",
            ],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
            env=self.fake_env(),
        )
        self.assertEqual(shadowed.returncode, 2)
        self.assertIn("Planner personal agent", shadowed.stderr)
        self.assertIn("shadowed by a project role", shadowed.stderr)

    def test_identical_planner_advisor_routes_rejected_on_clean_setup_and_update(self) -> None:
        same_model = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--planner-effort",
            "low",
            "--advisor-model",
            "gpt-5.6-sol",
            "--advisor-effort",
            "ultra",
            check=False,
        )
        self.assertEqual(same_model.returncode, 2)
        self.assertIn("Planner and Advisor routes must be distinct", same_model.stderr)
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

        self.write_personal_agent("shared_planning_agent")
        same_agent = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-agent",
            "shared_planning_agent",
            "--advisor-agent",
            "shared_planning_agent",
            check=False,
        )
        self.assertEqual(same_agent.returncode, 2)
        self.assertIn("Planner and Advisor routes must be distinct", same_agent.stderr)

        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--advisor-model",
            "gpt-5.6-terra",
            "--apply",
        )
        before_config = self.read_fake_config()
        before_state = (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        rejected_update = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-terra",
            "--advisor-model",
            "gpt-5.6-terra",
            "--apply",
            check=False,
        )
        self.assertEqual(rejected_update.returncode, 2)
        self.assertEqual(self.read_fake_config(), before_config)
        self.assertEqual(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8"),
            before_state,
        )

    def test_two_fable_planning_seats_are_rejected_before_prerequisites(self) -> None:
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-fable",
            "--planner-effort",
            "low",
            "--advisor-fable",
            "--advisor-effort",
            "max",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("both cannot use Claude Fable 5", result.stderr)
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_fable_planner_with_gpt_advisor_uses_one_launcher_and_restores(self) -> None:
        initial = {
            "features": {"multi_agent_v2": {}},
            "plugins": {
                self.plugin_id: {
                    "mcp_servers": {
                        "fable-advisor-python3": {"enabled": False},
                        "fable-advisor-python": {"enabled": True},
                        "fable-advisor-py": {"enabled": True},
                    }
                }
            },
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-fable",
            "--planner-effort",
            "max",
            "--advisor-model",
            "gpt-5.6-terra",
            "--apply",
        )
        self.assertIn("Planner: Claude Fable 5 max", setup.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["planner"]["kind"], "fable")
        self.assertEqual(state["advisor"]["kind"], "model")
        managed_mcp = state["managed"]["mcp"]
        self.assertEqual(sum(value is True for value in managed_mcp.values()), 1)
        self.assertTrue(managed_mcp["fable-advisor-python3"])
        servers = self.read_fake_config()["plugins"][self.plugin_id]["mcp_servers"]
        self.assertEqual(
            sum(entry["enabled"] is True for entry in servers.values()), 1
        )

        moved = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--advisor-fable",
            "--advisor-effort",
            "high",
            "--apply",
        )
        self.assertIn("Advisor: Claude Fable 5 high", moved.stdout)
        moved_servers = self.read_fake_config()["plugins"][self.plugin_id][
            "mcp_servers"
        ]
        self.assertTrue(moved_servers["fable-advisor-python3"]["enabled"])
        self.assertEqual(
            sum(entry["enabled"] is True for entry in moved_servers.values()), 1
        )

        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)

    def test_gpt_planner_with_fable_advisor(self) -> None:
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--planner-model",
            "gpt-5.6-sol",
            "--advisor-fable",
            "--advisor-effort",
            "medium",
            "--apply",
        )
        self.assertIn("Planner: gpt-5.6-sol@xhigh", setup.stdout)
        self.assertIn("Advisor: Claude Fable 5 medium", setup.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["planner"]["kind"], "model")
        self.assertEqual(state["advisor"]["kind"], "fable")

    def test_fable_setup_status_update_and_disable_restore_mcp_policy(self) -> None:
        initial = {
            "features": {
                "multi_agent_v2": {"max_concurrent_threads_per_session": 5}
            },
            "plugins": {
                self.plugin_id: {
                    "mcp_servers": {
                        "fable-advisor-python3": {"enabled": False},
                        "fable-advisor-python": {"enabled": True},
                    }
                }
            },
            "unrelated": {"keep": True},
        }
        (self.home / ".fake-user-config.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--advisor-effort",
            "max",
            "--apply",
        )
        self.assertIn("Claude Fable 5 max", setup.stdout)
        config = self.read_fake_config()
        servers = config["plugins"][self.plugin_id]["mcp_servers"]
        self.assertTrue(servers["fable-advisor-python3"]["enabled"])
        self.assertFalse(servers["fable-advisor-python"]["enabled"])
        self.assertNotIn("fable-advisor-py", servers)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["advisor"]["kind"], "fable")
        self.assertEqual(state["advisor"]["model"], "claude-fable-5")
        self.assertIn("mcp", state["previous"])

        status = self.run_script("--status")
        self.assertIn("Claude Fable 5: ready", status.stdout)
        self.assertIn("no model call made", status.stdout)

        update = self.run_script(
            "--executor-model",
            "gpt-5.6-terra",
            "--executor-effort",
            "high",
            "--apply",
        )
        self.assertIn("Advisor: none", update.stdout)
        servers = self.read_fake_config()["plugins"][self.plugin_id]["mcp_servers"]
        self.assertTrue(all(not entry["enabled"] for entry in servers.values()))

        self.run_script("--disable", "--apply")
        self.assertEqual(self.read_fake_config(), initial)

    def test_fable_effort_defaults_to_high_and_ultra_maps_to_max(self) -> None:
        setup = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--apply",
        )
        self.assertIn("Advisor: Claude Fable 5 high", setup.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["advisor"]["effort"], "high")

        update = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--advisor-effort",
            "ultra",
            "--apply",
        )
        self.assertIn("Advisor: Claude Fable 5 max", update.stdout)
        self.assertIn("Advisor effort alias: ultra -> max", update.stdout)
        state = json.loads(
            (self.home / NATIVE.STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(state["advisor"]["effort"], "max")

    def test_fable_effort_normalization_accepts_every_public_label(self) -> None:
        expected = {
            "auto": "high",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
            "max": "max",
            "ultra": "max",
        }
        for requested, effective in expected.items():
            with self.subTest(requested=requested):
                self.assertEqual(
                    NATIVE.normalize_fable_effort(requested), effective
                )

        with self.assertRaisesRegex(
            NATIVE.ConfigurationError, "low.*medium.*high.*xhigh.*max.*ultra"
        ):
            NATIVE.normalize_fable_effort("extreme")

    def test_fable_setup_rejects_effort_missing_from_installed_claude(self) -> None:
        self.claude.write_text(
            self.claude.read_text(encoding="utf-8").replace(
                "(low, medium, high, xhigh, max)",
                "(low, medium, high, max)",
            ),
            encoding="utf-8",
        )
        result = self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--advisor-effort",
            "xhigh",
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "Claude Code does not advertise Fable effort 'xhigh'",
            result.stderr,
        )
        self.assertFalse((self.home / NATIVE.STATE_FILENAME).exists())

    def test_require_effective_rejects_unavailable_saved_fable_effort(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--advisor-effort",
            "xhigh",
            "--apply",
        )
        self.claude.write_text(
            self.claude.read_text(encoding="utf-8").replace(
                "(low, medium, high, xhigh, max)",
                "(low, medium, high, max)",
            ),
            encoding="utf-8",
        )

        status = self.run_script(
            "--status",
            "--require-effective",
            check=False,
        )

        self.assertEqual(status.returncode, 1)
        self.assertIn(
            "Claude Code does not advertise Fable effort 'xhigh'",
            status.stdout,
        )

    def test_require_effective_rejects_unavailable_fable_auth(self) -> None:
        self.run_script(
            "--executor-model",
            "gpt-5.6-luna",
            "--executor-effort",
            "xhigh",
            "--advisor-fable",
            "--apply",
        )
        self.claude.write_text(
            self.claude.read_text(encoding="utf-8").replace(
                '"loggedIn": True',
                '"loggedIn": False',
            ),
            encoding="utf-8",
        )

        status = self.run_script(
            "--status",
            "--require-effective",
            check=False,
        )

        self.assertEqual(status.returncode, 1)
        self.assertIn(
            "must be logged in through a first-party Pro or Max account",
            status.stdout,
        )

    def test_missing_or_project_shadowed_custom_agent_is_refused(self) -> None:
        missing = self.run_script(
            "--executor-agent",
            "codex_orchestration_executor",
            "--apply",
            check=False,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("must resolve to exactly one personal file", missing.stderr)

        self.write_personal_agent("codex_orchestration_executor")
        project_agents = self.root / ".codex" / "agents"
        project_agents.mkdir(parents=True)
        (project_agents / "shadow.toml").write_text(
            "\n".join(
                (
                    'name = "codex_orchestration_executor"',
                    'description = "Shadow"',
                    'model = "other-model"',
                    'developer_instructions = "Shadow the personal route."',
                    "",
                )
            ),
            encoding="utf-8",
        )
        shadowed = subprocess.run(
            [
                sys.executable,
                str(self.installed_script),
                "--codex-bin",
                str(self.codex),
                "--codex-home",
                str(self.home),
                "--allow-incompatible-client",
                "--executor-agent",
                "codex_orchestration_executor",
                "--apply",
            ],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
            env=self.fake_env(),
        )
        self.assertEqual(shadowed.returncode, 2)
        self.assertIn("shadowed by a project role", shadowed.stderr)


if __name__ == "__main__":
    unittest.main()
