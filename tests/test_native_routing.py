from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
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

if "app-server" not in sys.argv:
    raise SystemExit(2)

home = Path(os.environ["CODEX_HOME"]).resolve()
home.mkdir(parents=True, exist_ok=True)
store = home / ".fake-user-config.json"
effective_store = home / ".fake-effective-config.json"
version_file = home / ".fake-version"
mutate_after_write = home / ".fake-mutate-after-write"
mutate_namespace_after_write = home / ".fake-mutate-namespace-after-write"
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
    parts = path.split(".")
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
        store.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_script(
        self,
        *arguments: str,
        check: bool = True,
        allow_incompatible: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        compatibility = ["--allow-incompatible-client"] if allow_incompatible else []
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
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
        )
        if check and result.returncode != 0:
            self.fail(f"command failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def read_fake_config(self) -> dict[str, object]:
        return json.loads(
            (self.home / ".fake-user-config.json").read_text(encoding="utf-8")
        )

    def write_personal_agent(self, name: str) -> Path:
        agents = self.home / "agents"
        agents.mkdir(exist_ok=True)
        path = agents / f"{name.replace('_', '-')}.toml"
        path.write_text(
            "\n".join(
                (
                    f'name = "{name}"',
                    'description = "Test custom route"',
                    'model = "gpt-5.6-luna"',
                    'model_reasoning_effort = "high"',
                    'developer_instructions = "Stay bounded and report to the root."',
                    "",
                )
            ),
            encoding="utf-8",
        )
        return path

    def test_policy_keeps_root_authority_and_pins_fork_none(self) -> None:
        executor = {"kind": "model", "model": "gpt-5.6-luna", "effort": "xhigh"}
        advisor = {"kind": "model", "model": "gpt-5.6-terra", "effort": "high"}
        mode, usage = NATIVE.build_policy(executor, advisor)

        self.assertIn("root task model, you are the orchestrator", mode)
        self.assertIn("Codex still decides whether a plan or subagent helps", mode)
        self.assertIn("never spawn descendants", mode)
        self.assertIn("Explicit user instructions win", mode)
        self.assertIn("Advisor failure or unavailability is not approval", mode)
        self.assertIn('model = "gpt-5.6-luna"', usage)
        self.assertIn('reasoning_effort = "xhigh"', usage)
        self.assertGreaterEqual(usage.count('fork_turns = "none"'), 2)
        self.assertIn('Never use fork_turns = "all"', usage)
        self.assertIn("If you are a spawned child, do not call this tool", usage)
        self.assertNotIn("tool_namespace", mode + usage)
        self.assertNotIn("enabled = true", mode + usage)

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
        self.assertIn("V2 activation: not inferred", status.stdout)
        self.assertIn("Executor: gpt-5.6-luna@xhigh", status.stdout)
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
        state["managed"]["usage"] = "DIFFERENT MANAGED VALUE"
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
        with mock.patch.object(NATIVE.os, "fchmod", None):
            NATIVE._write_state(state_path, state)
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)

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
            str(SCRIPT),
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
            str(SCRIPT),
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
        ):
            result = NATIVE.main()

        self.assertEqual(result, 2)
        self.assertEqual(calls, 2)
        self.assertIn("may still contain managed fields", stderr.getvalue())
        self.assertIn("unexpected rollback status", stderr.getvalue())
        feature = self.read_fake_config()["features"]["multi_agent_v2"]
        self.assertIn(NATIVE.MANAGED_MARKER, feature["usage_hint_text"])

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
                str(SCRIPT),
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
        )
        self.assertEqual(shadowed.returncode, 2)
        self.assertIn("shadowed by a project role", shadowed.stderr)


if __name__ == "__main__":
    unittest.main()
