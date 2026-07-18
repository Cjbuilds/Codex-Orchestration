from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/codex-orchestration/skills/codex-orchestration/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "external_configurator", SCRIPTS / "external_configurator.py"
)
assert SPEC and SPEC.loader
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class FakeBackend:
    def __init__(self) -> None:
        self.providers: dict[str, object] = {}
        self.version = 0
        self.writes: list[tuple[str, object | None]] = []

    def read_provider(self, provider_id: str):
        return (
            provider_id in self.providers,
            deepcopy(self.providers.get(provider_id)),
            f"v{self.version}",
        )

    def write_provider(self, provider_id: str, value, version) -> None:
        if version != f"v{self.version}":
            raise CONFIG.ExternalConfigurationError("stale config version")
        self.writes.append((provider_id, deepcopy(value)))
        if value is None:
            self.providers.pop(provider_id, None)
        else:
            self.providers[provider_id] = deepcopy(value)
        self.version += 1


def prepared(home: Path, backend: FakeBackend) -> dict[str, object]:
    CONFIG.prepare_provider(home, "openrouter", backend)
    registry, _ = CONFIG.load_registry(home)
    return registry


def qualify(home: Path) -> None:
    registry, digest = CONFIG.load_registry(home)
    registry["providers"]["openrouter"]["qualified"] = True
    registry["providers"]["openrouter"]["state"] = "CAPABILITY_VERIFIED"
    registry["providers"]["openrouter"]["capability_checked_at"] = (
        "2026-07-17T00:00:00+00:00"
    )
    registry["providers"]["openrouter"]["capability_source"] = "test-gate0"
    CONFIG.external_registry.write_registry(
        CONFIG.registry_path(home), registry, expected_sha256=digest
    )


GATE0_HELP = """Run Codex non-interactively
  --ephemeral
  --skip-git-repo-check
  -s, --sandbox <SANDBOX_MODE>
      [possible values: read-only, workspace-write]
  -o, --output-last-message <FILE>
"""


def gate0_help_result():
    return mock.Mock(returncode=0, stdout=GATE0_HELP, stderr="")


def write_user_helper(home: Path, marker: bytes = b"test-value") -> Path:
    if os.name == "nt":
        helper = home / "user-helper.exe"
        shutil.copy2(Path(getattr(sys, "_base_executable", sys.executable)), helper)
        with helper.open("ab") as handle:
            handle.write(marker)
        return helper
    helper = home / "user-helper"
    helper.write_text(
        "#!/bin/sh\nprintf '" + marker.decode("ascii") + "\\n'\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    return helper


def mutate_user_helper(helper: Path, marker: bytes) -> None:
    if os.name == "nt":
        with helper.open("ab") as handle:
            handle.write(marker)
        return
    helper.write_text(
        "#!/bin/sh\nprintf '" + marker.decode("ascii") + "\\n'\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)


class ExternalConfiguratorTests(unittest.TestCase):
    def test_prepare_is_additive_nonsecret_and_returns_external_auth_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            command = CONFIG.prepare_provider(home, "openrouter", backend)
            helper = home / "codex-orchestration/bin/external_auth_helper.py"
            self.assertEqual(
                command,
                CONFIG.external_credentials.enrollment_command(
                    helper, "openrouter"
                ),
            )
            self.assertEqual(
                command[-3:], ["enroll", "--provider", "openrouter"]
            )
            self.assertIn(str(helper), command[:-3])
            provider = backend.providers["openrouter"]
            self.assertEqual(set(provider), {"name", "base_url", "wire_api", "auth"})
            self.assertNotIn("model", provider)
            serialized = json.dumps(provider).lower()
            for forbidden in ("api_key", "bearer", "password", "sk-"):
                self.assertNotIn(forbidden, serialized)
            registry, _ = CONFIG.load_registry(home)
            self.assertEqual(registry["providers"]["openrouter"]["state"], "AUTH_REQUIRED")
            self.assertFalse(CONFIG.journal_path(home).exists())
            if os.name == "posix":
                self.assertEqual(
                    stat.S_IMODE(CONFIG.registry_path(home).stat().st_mode), 0o600
                )

    def test_prepare_refuses_existing_provider_collision_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            backend.providers["openrouter"] = {"name": "user-owned"}
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "already exists"):
                CONFIG.prepare_provider(home, "openrouter", backend)
            self.assertEqual(backend.writes, [])

    def test_user_helper_requires_explicit_trust_and_is_pinned_without_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            helper = write_user_helper(home)
            backend = FakeBackend()
            with self.assertRaisesRegex(
                CONFIG.ExternalConfigurationError, "explicit --trust-user-helper"
            ):
                CONFIG.prepare_provider(
                    home, "openrouter", backend, user_helper=helper
                )
            command = CONFIG.prepare_provider(
                home,
                "openrouter",
                backend,
                user_helper=helper,
                trust_user_helper=True,
            )
            self.assertEqual(command, [])
            registry, _ = CONFIG.load_registry(home)
            record = registry["providers"]["openrouter"]
            trust = registry["cli_trust"]["openrouter"]
            self.assertEqual(record["auth_kind"], "user_helper")
            self.assertEqual(trust["path"], str(helper.resolve()))
            self.assertTrue(trust["fingerprint"].startswith("sha256:"))
            self.assertNotIn("test-value", json.dumps(registry))
            mutate_user_helper(helper, b"changed")
            status = CONFIG.inspect_status(home, backend)
            self.assertEqual(
                status["providers"]["openrouter"]["config"], "CONFIG_DRIFT"
            )
            target = CONFIG.retrust_user_helper(home, "openrouter")
            self.assertEqual(target, str(helper.resolve()))
            registry, _ = CONFIG.load_registry(home)
            self.assertFalse(registry["providers"]["openrouter"]["qualified"])
            self.assertEqual(
                registry["providers"]["openrouter"]["state"], "AUTH_REQUIRED"
            )

    def test_preview_commands_make_no_external_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with mock.patch.object(
                CONFIG.native_routing,
                "resolve_binary",
                side_effect=AssertionError("preview started Codex"),
            ):
                result = CONFIG.main(
                    [
                        "--codex-home",
                        str(home),
                        "prepare",
                        "--provider",
                        "openrouter",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(list(home.iterdir()), [])

    def test_recovery_rolls_back_only_exact_partial_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            provider = CONFIG.external_providers.load_provider("openrouter")
            helper, _ = CONFIG.external_credentials.install_stable_helper(home)
            expected = CONFIG.provider_config(provider, helper)
            registry = CONFIG.external_registry.empty_registry(home)
            after = deepcopy(registry)
            after["providers"]["openrouter"] = CONFIG.provider_record(provider, expected)
            journal = {
                "schema": 1,
                "managed_by": "codex-orchestration",
                "action": "prepare_provider",
                "phase": "provider_applied",
                "provider": "openrouter",
                "provider_config_sha256": CONFIG._sha256_json(expected),
                "registry_before_sha256": None,
                "registry_after_sha256": CONFIG.hashlib.sha256(
                    CONFIG.external_registry.canonical_bytes(after)
                ).hexdigest(),
            }
            CONFIG._write_journal(CONFIG.journal_path(home), journal)
            backend.providers["openrouter"] = deepcopy(expected)
            self.assertTrue(CONFIG.recover_provider_transaction(home, backend))
            self.assertNotIn("openrouter", backend.providers)
            self.assertFalse(CONFIG.journal_path(home).exists())

    def test_recovery_refuses_drift_and_preserves_user_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            provider = CONFIG.external_providers.load_provider("openrouter")
            helper, _ = CONFIG.external_credentials.install_stable_helper(home)
            expected = CONFIG.provider_config(provider, helper)
            after = CONFIG.external_registry.empty_registry(home)
            after["providers"]["openrouter"] = CONFIG.provider_record(provider, expected)
            journal = {
                "schema": 1,
                "managed_by": "codex-orchestration",
                "action": "prepare_provider",
                "phase": "provider_applied",
                "provider": "openrouter",
                "provider_config_sha256": CONFIG._sha256_json(expected),
                "registry_before_sha256": None,
                "registry_after_sha256": CONFIG.hashlib.sha256(
                    CONFIG.external_registry.canonical_bytes(after)
                ).hexdigest(),
            }
            CONFIG._write_journal(CONFIG.journal_path(home), journal)
            backend.providers["openrouter"] = {"name": "changed-by-user"}
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "RECOVERY_REQUIRED"):
                CONFIG.recover_provider_transaction(home, backend)
            self.assertEqual(
                backend.providers["openrouter"], {"name": "changed-by-user"}
            )

    def test_connect_requires_gate0_then_creates_exact_provider_pinned_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "Gate 0"):
                CONFIG.connect_role(
                    home,
                    "kimi_researcher",
                    "Review bounded research packets.",
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                )
            qualify(home)
            name = CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            registry, _ = CONFIG.load_registry(home)
            role = registry["roles"]["kimi_researcher"]
            content = Path(role["agent_file"]).read_text(encoding="utf-8")
            self.assertEqual(role["agent_name"], name)
            self.assertIn('model_provider = "openrouter"', content)
            self.assertIn('model = "moonshotai/kimi-k3"', content)
            self.assertIn('model_reasoning_effort = "max"', content)
            self.assertIn("model_context_window = 1048576", content)
            self.assertIn("model_auto_compact_token_limit = 950000", content)
            self.assertEqual(role["state"], "RESTART_REQUIRED")
            self.assertNotIn("api_key", content.lower())

    def test_ready_and_resolve_validate_digest_state_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            qualify(home)
            name = CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "not ready"):
                CONFIG.resolve_role(home, "kimi_researcher", "auto", backend)
            self.assertEqual(CONFIG.mark_role_ready(home, "kimi_researcher"), name)
            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ):
                resolved = CONFIG.resolve_role(home, "kimi_researcher", "max", backend)
            self.assertEqual(resolved["agent"], name)
            self.assertEqual(resolved["effort"], "max")
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "unsupported"):
                CONFIG.resolve_role(home, "kimi_researcher", "medium", backend)
            workspace = home / "project"
            project_agents = workspace / ".codex" / "agents"
            project_agents.mkdir(parents=True)
            (project_agents / "shadow.toml").write_text(
                f'name = "{name}"\ndescription = "shadow"\nmodel = "other"\n'
                'developer_instructions = "shadow"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    CONFIG.external_credentials, "credential_ready", return_value=True
                ),
                self.assertRaisesRegex(
                    CONFIG.ExternalConfigurationError, "project agent shadows"
                ),
            ):
                CONFIG.resolve_role(
                    home, "kimi_researcher", "max", backend, workspace
                )

    def test_resolve_fails_closed_on_provider_agent_helper_and_qualification_drift(self) -> None:
        attacks = (
            "provider",
            "manifest",
            "agent_edit",
            "agent_delete",
            "helper",
            "qualification",
        )
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                backend = FakeBackend()
                prepared(home, backend)
                qualify(home)
                CONFIG.connect_role(
                    home,
                    "kimi_researcher",
                    "Review bounded research packets.",
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                )
                CONFIG.mark_role_ready(home, "kimi_researcher")
                registry, digest = CONFIG.load_registry(home)
                agent = Path(
                    registry["roles"]["kimi_researcher"]["agent_file"]
                )
                if attack == "provider":
                    backend.providers["openrouter"]["base_url"] = "https://invalid.example/v1"
                elif attack == "manifest":
                    registry["providers"]["openrouter"]["adapter_version"] += 1
                    CONFIG.external_registry.write_registry(
                        CONFIG.registry_path(home),
                        registry,
                        expected_sha256=digest,
                    )
                elif attack == "agent_edit":
                    agent.write_text(
                        agent.read_text(encoding="utf-8") + "# drift\n",
                        encoding="utf-8",
                    )
                elif attack == "agent_delete":
                    agent.unlink()
                elif attack == "helper":
                    helper = (
                        home
                        / "codex-orchestration/bin/external_auth_helper.py"
                    )
                    helper.write_bytes(helper.read_bytes() + b"\n# drift\n")
                else:
                    registry["providers"]["openrouter"]["qualified"] = False
                    CONFIG.external_registry.write_registry(
                        CONFIG.registry_path(home),
                        registry,
                        expected_sha256=digest,
                    )
                with mock.patch.object(
                    CONFIG.external_credentials,
                    "credential_ready",
                    return_value=True,
                ):
                    with self.assertRaises(CONFIG.ExternalConfigurationError):
                        CONFIG.resolve_role(
                            home, "kimi_researcher", "max", backend
                        )

    def test_retrust_dequalifies_an_existing_role_at_resolve_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            helper = write_user_helper(home)
            backend = FakeBackend()
            CONFIG.prepare_provider(
                home,
                "openrouter",
                backend,
                user_helper=helper,
                trust_user_helper=True,
            )
            qualify(home)
            CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            CONFIG.mark_role_ready(home, "kimi_researcher")
            mutate_user_helper(helper, b"new-value")
            CONFIG.retrust_user_helper(home, "openrouter")
            with self.assertRaisesRegex(
                CONFIG.ExternalConfigurationError, "no longer qualified"
            ):
                CONFIG.resolve_role(home, "kimi_researcher", "max", backend)

    def test_two_roles_share_a_provider_and_disconnect_in_either_order(self) -> None:
        for order in (("researcher", "critic"), ("critic", "researcher")):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                backend = FakeBackend()
                prepared(home, backend)
                qualify(home)
                CONFIG.connect_role(
                    home,
                    "researcher",
                    "Review bounded research packets.",
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                )
                CONFIG.mark_role_ready(home, "researcher")
                CONFIG.connect_role(
                    home,
                    "critic",
                    "Critique bounded evidence packets.",
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                )
                registry, _ = CONFIG.load_registry(home)
                self.assertEqual(
                    registry["providers"]["openrouter"]["state"],
                    "RESTART_REQUIRED",
                )
                CONFIG.mark_role_ready(home, "critic")
                registry, _ = CONFIG.load_registry(home)
                self.assertEqual(
                    registry["providers"]["openrouter"]["state"], "READY"
                )
                CONFIG.disconnect_role(home, order[0])
                registry, _ = CONFIG.load_registry(home)
                self.assertEqual(set(registry["roles"]), {order[1]})
                self.assertEqual(
                    registry["providers"]["openrouter"]["state"], "READY"
                )
                CONFIG.disconnect_role(home, order[1])
                registry, _ = CONFIG.load_registry(home)
                self.assertEqual(registry["roles"], {})
                self.assertEqual(
                    registry["providers"]["openrouter"]["state"],
                    "CAPABILITY_VERIFIED",
                )

    def test_disconnect_staged_second_role_restores_existing_role_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            qualify(home)
            CONFIG.connect_role(
                home,
                "researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            CONFIG.mark_role_ready(home, "researcher")
            CONFIG.connect_role(
                home,
                "critic",
                "Critique bounded evidence packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            CONFIG.disconnect_role(home, "critic")
            registry, _ = CONFIG.load_registry(home)
            self.assertEqual(
                registry["providers"]["openrouter"]["state"], "READY"
            )
            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ):
                resolved = CONFIG.resolve_role(home, "researcher", "max", backend)
            self.assertEqual(resolved["role"], "researcher")

    def test_disconnect_and_provider_removal_preserve_chat_and_root_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            chat = home / "sessions/chat-123.jsonl"
            chat.parent.mkdir()
            chat.write_text("do-not-delete\n", encoding="utf-8")
            auth = home / "auth.json"
            auth.write_text('{"existing":"openai-login"}\n', encoding="utf-8")
            backend = FakeBackend()
            prepared(home, backend)
            qualify(home)
            CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            self.assertEqual(
                CONFIG.disconnect_role(home, "kimi_researcher"), "openrouter"
            )
            registry, _ = CONFIG.load_registry(home)
            self.assertEqual(registry["roles"], {})
            self.assertIn("openrouter", registry["providers"])
            CONFIG.remove_provider(home, "openrouter", backend)
            registry, _ = CONFIG.load_registry(home)
            self.assertEqual(registry["providers"], {})
            self.assertNotIn("openrouter", backend.providers)
            self.assertEqual(chat.read_text(encoding="utf-8"), "do-not-delete\n")
            self.assertEqual(
                auth.read_text(encoding="utf-8"),
                '{"existing":"openai-login"}\n',
            )

    def test_disconnect_refuses_drifted_agent_and_preserves_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            qualify(home)
            CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            registry, _ = CONFIG.load_registry(home)
            agent = Path(registry["roles"]["kimi_researcher"]["agent_file"])
            agent.write_text(agent.read_text(encoding="utf-8") + "# user change\n")
            with self.assertRaisesRegex(
                CONFIG.ExternalConfigurationError, "drifted"
            ):
                CONFIG.disconnect_role(home, "kimi_researcher")
            self.assertTrue(agent.exists())

    def test_remove_recovery_rolls_forward_after_exact_config_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            registry, before_digest = CONFIG.load_registry(home)
            expected = deepcopy(backend.providers["openrouter"])
            after = deepcopy(registry)
            after["providers"].pop("openrouter")
            journal = {
                "schema": 1,
                "managed_by": "codex-orchestration",
                "action": "remove_provider",
                "phase": "provider_removed",
                "provider": "openrouter",
                "provider_config_sha256": CONFIG._sha256_json(expected),
                "registry_before_sha256": before_digest,
                "registry_after_sha256": CONFIG.hashlib.sha256(
                    CONFIG.external_registry.canonical_bytes(after)
                ).hexdigest(),
            }
            CONFIG._write_journal(CONFIG.journal_path(home), journal)
            backend.providers.pop("openrouter")
            self.assertTrue(CONFIG.recover_provider_transaction(home, backend))
            recovered, _ = CONFIG.load_registry(home)
            self.assertEqual(recovered["providers"], {})
            self.assertFalse(CONFIG.journal_path(home).exists())

    def test_remove_recovery_preserves_roles_for_other_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            qualify(home)
            CONFIG.connect_role(
                home,
                "kimi_researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            registry, digest = CONFIG.load_registry(home)
            other = deepcopy(registry["providers"]["openrouter"])
            other["owned_config_keys"] = [
                key.replace("model_providers.openrouter.", "model_providers.other.")
                for key in other["owned_config_keys"]
            ]
            registry["providers"]["other"] = other
            registry["roles"]["kimi_researcher"]["provider"] = "other"
            CONFIG.external_registry.write_registry(
                CONFIG.registry_path(home), registry, expected_sha256=digest
            )

            registry, before_digest = CONFIG.load_registry(home)
            expected = deepcopy(backend.providers["openrouter"])
            after = deepcopy(registry)
            after["providers"].pop("openrouter")
            journal = {
                "schema": 1,
                "managed_by": "codex-orchestration",
                "action": "remove_provider",
                "phase": "provider_removed",
                "provider": "openrouter",
                "provider_config_sha256": CONFIG._sha256_json(expected),
                "registry_before_sha256": before_digest,
                "registry_after_sha256": CONFIG.hashlib.sha256(
                    CONFIG.external_registry.canonical_bytes(after)
                ).hexdigest(),
            }
            CONFIG._write_journal(CONFIG.journal_path(home), journal)
            backend.providers.pop("openrouter")

            self.assertTrue(CONFIG.recover_provider_transaction(home, backend))
            recovered, _ = CONFIG.load_registry(home)
            self.assertEqual(set(recovered["providers"]), {"other"})
            self.assertEqual(
                recovered["roles"]["kimi_researcher"]["provider"], "other"
            )
            self.assertFalse(CONFIG.journal_path(home).exists())

    def test_gate0_environment_uses_central_secret_filter(self) -> None:
        isolated_home = Path("/safe/isolated-home")
        with mock.patch.object(
            CONFIG.external_cli_trust,
            "sanitized_environment",
            return_value={"PATH": "/safe/bin", "KEEP_ME": "yes"},
        ) as sanitized:
            environment = CONFIG._gate0_environment(isolated_home)
        sanitized.assert_called_once_with()
        self.assertEqual(
            environment,
            {
                "PATH": "/safe/bin",
                "KEEP_ME": "yes",
                "CODEX_HOME": str(isolated_home),
            },
        )

    def test_gate0_requires_billing_ack_is_ephemeral_and_withholds_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            with self.assertRaisesRegex(CONFIG.ExternalConfigurationError, "acknowledgement"):
                CONFIG.run_gate0(
                    home,
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                    Path("/safe/codex"),
                    acknowledge_billing=False,
                )
            completed = mock.Mock(
                returncode=1,
                stdout="MALICIOUS MODEL OUTPUT WITH sensitive-test-value",
                stderr="sensitive-test-value",
            )
            observed_configs: list[dict[str, object]] = []

            def run_gate0(command, **kwargs):
                if command[-1] == "--help":
                    return gate0_help_result()
                config_path = Path(kwargs["cwd"]) / "config.toml"
                observed_configs.append(
                    CONFIG.tomllib.loads(config_path.read_text(encoding="utf-8"))
                )
                return completed

            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ), mock.patch.object(
                CONFIG.external_cli_trust,
                "sanitized_environment",
                return_value={"PATH": "/safe/bin", "KEEP_ME": "yes"},
            ) as sanitized, mock.patch.object(
                CONFIG.subprocess,
                "run",
                side_effect=run_gate0,
            ) as run:
                with self.assertRaises(CONFIG.ExternalConfigurationError) as failure:
                    CONFIG.run_gate0(
                        home,
                        "openrouter",
                        "moonshotai/kimi-k3",
                        "max",
                        Path("/safe/codex"),
                        acknowledge_billing=True,
                    )
            self.assertNotIn("sensitive-test-value", str(failure.exception))
            sanitized.assert_called_once_with()
            self.assertEqual(run.call_count, 2)
            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(Path("/safe/codex")), "exec"])
            self.assertIn("--ephemeral", command)
            self.assertIn("--skip-git-repo-check", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertIn("--output-last-message", command)
            self.assertEqual(run.call_args.kwargs["stdout"], CONFIG.subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["stderr"], CONFIG.subprocess.DEVNULL)
            self.assertEqual(
                run.call_args.kwargs["env"],
                {
                    "PATH": "/safe/bin",
                    "KEEP_ME": "yes",
                    "CODEX_HOME": os.fspath(run.call_args.kwargs["cwd"]),
                },
            )
            self.assertNotEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(home))
            self.assertEqual(len(observed_configs), 1)
            gate0_config = observed_configs[0]
            self.assertEqual(gate0_config["model"], "moonshotai/kimi-k3")
            self.assertEqual(gate0_config["model_provider"], "openrouter")
            self.assertEqual(gate0_config["model_reasoning_effort"], "max")
            openrouter = gate0_config["model_providers"]["openrouter"]
            self.assertEqual(openrouter["base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(openrouter["wire_api"], "responses")
            registry, _ = CONFIG.load_registry(home)
            provider = registry["providers"]["openrouter"]
            self.assertFalse(provider["qualified"])
            self.assertNotEqual(provider["state"], "CAPABILITY_VERIFIED")

    def test_gate0_uses_legal_transitions_and_cannot_requalify_a_ready_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)

            def run_gate0(command, **_kwargs):
                if command[-1] == "--help":
                    return gate0_help_result()
                output_index = command.index("--output-last-message") + 1
                Path(command[output_index]).write_text(
                    CONFIG.GATE0_SIGNAL, encoding="utf-8"
                )
                return mock.Mock(
                    returncode=0,
                    stdout=(
                        "Codex header\nprovider: openrouter\n"
                        f"{CONFIG.GATE0_SIGNAL}\ntokens used: 12\n"
                    ),
                    stderr="",
                )

            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ), mock.patch.object(
                CONFIG.subprocess, "run", side_effect=run_gate0
            ), mock.patch.object(
                CONFIG, "transition", wraps=CONFIG.transition
            ) as guarded:
                CONFIG.run_gate0(
                    home,
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                    Path("/safe/codex"),
                    acknowledge_billing=True,
                )
            self.assertIn(
                mock.call(CONFIG.Readiness.AUTH_REQUIRED, CONFIG.Readiness.AUTH_READY),
                guarded.call_args_list,
            )
            self.assertIn(
                mock.call(
                    CONFIG.Readiness.AUTH_READY,
                    CONFIG.Readiness.CAPABILITY_VERIFIED,
                ),
                guarded.call_args_list,
            )
            registry, _ = CONFIG.load_registry(home)
            record = registry["providers"]["openrouter"]
            self.assertEqual(record["state"], "CAPABILITY_VERIFIED")
            self.assertTrue(record["qualified"])
            CONFIG.connect_role(
                home,
                "researcher",
                "Review bounded research packets.",
                "openrouter",
                "moonshotai/kimi-k3",
                "max",
            )
            CONFIG.mark_role_ready(home, "researcher")
            with self.assertRaisesRegex(
                CONFIG.ExternalConfigurationError, "requires authentication readiness"
            ):
                CONFIG.run_gate0(
                    home,
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                    Path("/safe/codex"),
                    acknowledge_billing=True,
                )

    def test_gate0_rejects_missing_cli_flags_before_auth_or_spend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            incomplete_help = mock.Mock(
                returncode=0,
                stdout="  --ephemeral\n  --sandbox <MODE>\n  read-only\n",
                stderr="",
            )
            with mock.patch.object(
                CONFIG.external_credentials,
                "credential_ready",
                side_effect=AssertionError("auth checked before CLI contract"),
            ), mock.patch.object(
                CONFIG.subprocess, "run", return_value=incomplete_help
            ) as run:
                with self.assertRaisesRegex(
                    CONFIG.ExternalConfigurationError,
                    "no billable command was started",
                ):
                    CONFIG.run_gate0(
                        home,
                        "openrouter",
                        "moonshotai/kimi-k3",
                        "max",
                        Path("/safe/codex"),
                        acknowledge_billing=True,
                    )
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-2:], ["exec", "--help"])
            registry, _ = CONFIG.load_registry(home)
            self.assertEqual(
                registry["providers"]["openrouter"]["state"], "AUTH_REQUIRED"
            )

    @unittest.skipUnless(os.name == "posix", "fake executable uses a POSIX shebang")
    def test_gate0_fake_codex_uses_last_message_not_decorated_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            backend = FakeBackend()
            prepared(home, backend)
            fake = root / "fake-codex"
            fake.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                "import os\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "if args == ['exec', '--help']:\n"
                "    print('  --ephemeral\\n  --skip-git-repo-check\\n'\n"
                "          '  -s, --sandbox <MODE>\\n      [possible values: read-only]\\n'\n"
                "          '  -o, --output-last-message <FILE>')\n"
                "    raise SystemExit(0)\n"
                "required_args = ['--ephemeral', '--skip-git-repo-check', "
                "'--sandbox', 'read-only', '--output-last-message']\n"
                "if any(value not in args for value in required_args):\n"
                "    raise SystemExit(42)\n"
                "config = (Path(os.environ['CODEX_HOME']) / 'config.toml').read_text()\n"
                "required_config = [\n"
                "    'model = \\\"moonshotai/kimi-k3\\\"',\n"
                "    'model_provider = \\\"openrouter\\\"',\n"
                "    'model_reasoning_effort = \\\"max\\\"',\n"
                "    'base_url = \\\"https://openrouter.ai/api/v1\\\"',\n"
                "    'wire_api = \\\"responses\\\"',\n"
                "]\n"
                "if any(value not in config for value in required_config):\n"
                "    raise SystemExit(43)\n"
                "target = Path(args[args.index('--output-last-message') + 1])\n"
                f"target.write_text({CONFIG.GATE0_SIGNAL!r}, encoding='utf-8')\n"
                "print('Codex header\\nprompt echoed\\nprovider output\\ntokens used: 12')\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ):
                CONFIG.run_gate0(
                    home,
                    "openrouter",
                    "moonshotai/kimi-k3",
                    "max",
                    fake,
                    acknowledge_billing=True,
                )
            registry, _ = CONFIG.load_registry(home)
            self.assertTrue(registry["providers"]["openrouter"]["qualified"])
            self.assertEqual(
                registry["providers"]["openrouter"]["state"],
                "CAPABILITY_VERIFIED",
            )

    def test_gate0_never_qualifies_from_decorated_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            backend = FakeBackend()
            prepared(home, backend)
            decorated = f"prompt echo {CONFIG.GATE0_SIGNAL} sensitive-stdout"
            completed = mock.Mock(returncode=0, stdout=decorated, stderr="")
            with mock.patch.object(
                CONFIG.external_credentials, "credential_ready", return_value=True
            ), mock.patch.object(
                CONFIG.subprocess,
                "run",
                side_effect=[gate0_help_result(), completed],
            ) as run:
                with self.assertRaises(CONFIG.ExternalConfigurationError) as failure:
                    CONFIG.run_gate0(
                        home,
                        "openrouter",
                        "moonshotai/kimi-k3",
                        "max",
                        Path("/safe/codex"),
                        acknowledge_billing=True,
                    )

            self.assertEqual(run.call_count, 2)
            self.assertNotIn("sensitive-stdout", str(failure.exception))
            registry, _ = CONFIG.load_registry(home)
            provider = registry["providers"]["openrouter"]
            self.assertFalse(provider["qualified"])
            self.assertNotEqual(provider["state"], "CAPABILITY_VERIFIED")

    def test_gate0_rejects_decorated_and_oversized_last_messages(self) -> None:
        cases = {
            "decorated": f"sensitive-artifact {CONFIG.GATE0_SIGNAL} extra",
            "oversized": (
                f"sensitive-artifact {CONFIG.GATE0_SIGNAL} "
                + "x" * CONFIG.GATE0_LAST_MESSAGE_MAX_BYTES
            ),
        }
        for name, artifact in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                backend = FakeBackend()
                prepared(home, backend)

                def run_gate0(command, **_kwargs):
                    if command[-1] == "--help":
                        return gate0_help_result()
                    output_index = command.index("--output-last-message") + 1
                    Path(command[output_index]).write_text(
                        artifact, encoding="utf-8"
                    )
                    return mock.Mock(returncode=0, stdout="", stderr="")

                with mock.patch.object(
                    CONFIG.external_credentials,
                    "credential_ready",
                    return_value=True,
                ), mock.patch.object(
                    CONFIG.subprocess, "run", side_effect=run_gate0
                ) as run:
                    with self.assertRaises(
                        CONFIG.ExternalConfigurationError
                    ) as failure:
                        CONFIG.run_gate0(
                            home,
                            "openrouter",
                            "moonshotai/kimi-k3",
                            "max",
                            Path("/safe/codex"),
                            acknowledge_billing=True,
                        )

                self.assertEqual(run.call_count, 2)
                self.assertNotIn("sensitive-artifact", str(failure.exception))
                registry, _ = CONFIG.load_registry(home)
                provider = registry["providers"]["openrouter"]
                self.assertFalse(provider["qualified"])
                self.assertNotEqual(provider["state"], "CAPABILITY_VERIFIED")


if __name__ == "__main__":
    unittest.main()
