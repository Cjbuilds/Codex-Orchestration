from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import getpass
import hashlib
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
    / "configure_orchestration.py"
)
SPEC = importlib.util.spec_from_file_location("configure_orchestration", SCRIPT_PATH)
assert SPEC and SPEC.loader
CONFIGURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIGURE)

CATALOG = {
    "executor-test": {
        "slug": "executor-test",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low"},
            {"effort": "medium"},
            {"effort": "high"},
        ],
    },
    "advisor-test": {
        "slug": "advisor-test",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {"effort": "medium"},
            {"effort": "high"},
            {"effort": "xhigh"},
        ],
    },
}


def legacy_layer(model: str, marker: str | None = None, **extra: str) -> str:
    marker = marker or CONFIGURE.ROUTING_MARKER
    fields = [marker, f'model = "{model}"', 'model_reasoning_effort = "high"']
    fields.extend(f'{key} = "{value}"' for key, value in extra.items())
    return "\n".join([*fields, ""])


def legacy_config(
    *,
    marker: str | None = None,
    executor: bool = True,
    advisor: bool = False,
    prefix: str = "",
    suffix: str = "",
) -> str:
    marker = marker or CONFIGURE.ROUTING_MARKER
    blocks = [marker]
    if prefix:
        blocks.append(prefix.rstrip("\n"))
    if executor:
        blocks.extend(
            [
                "[agents.executor]",
                f"description = {CONFIGURE.toml_string(CONFIGURE.LEGACY_EXECUTOR_DESCRIPTION)}",
                'config_file = "agents/executor-model.toml"',
            ]
        )
    if advisor:
        blocks.extend(
            [
                "[agents.advisor]",
                f"description = {CONFIGURE.toml_string(CONFIGURE.LEGACY_ADVISOR_DESCRIPTION)}",
                'config_file = "agents/advisor-model.toml"',
            ]
        )
    if suffix:
        blocks.append(suffix.rstrip("\n"))
    return "\n\n".join(blocks) + "\n"


def v1_agent(name: str = "orchestrated_executor") -> str:
    fields = [
        CONFIGURE.V1_MARKER,
        f'name = "{name}"',
        f"description = {CONFIGURE.toml_string(CONFIGURE.V1_DESCRIPTION)}",
        'nickname_candidates = ["Forge", "Relay", "Vector", "Scout", "Delta"]',
        'model = "old-v1-model"',
        'model_reasoning_effort = "high"',
        f"developer_instructions = {CONFIGURE.toml_string(CONFIGURE.v1_executor_instructions())}",
        "",
    ]
    return "\n".join(fields)


class ConfigureOrchestrationTests(unittest.TestCase):
    def run_main(
        self,
        root: Path,
        *extra: str,
        catalog: dict[str, dict[str, object]] | None = None,
        codex_home_env: Path | None = None,
        remove_saved_roles: bool = False,
    ) -> tuple[int, str, str]:
        argv = [str(SCRIPT_PATH), "--root", str(root)]
        if remove_saved_roles:
            argv.append("--remove-saved-roles")
        else:
            argv.extend(
                [
                    "--executor-model",
                    "executor-test",
                    "--executor-effort",
                    "auto",
                ]
            )
        argv.extend(extra)
        stdout = StringIO()
        stderr = StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home_env or root / "_personal_codex")},
            ),
            mock.patch.object(
                CONFIGURE, "load_catalog", return_value=CATALOG if catalog is None else catalog
            ),
            mock.patch.object(
                CONFIGURE,
                "catalog_source",
                return_value="/mock/bin/codex (codex-cli test); requested by --codex-bin=codex",
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = CONFIGURE.main()
        return result, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def paths(root: Path) -> tuple[Path, Path, Path]:
        base = root / ".codex"
        return (
            base / "config.toml",
            base / "agents" / CONFIGURE.EXECUTOR_FILENAME,
            base / "agents" / CONFIGURE.ADVISOR_FILENAME,
        )

    def test_low_level_staging_requests_binary_descriptors_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            staged = root / "staged.toml"
            path.write_bytes(b"old\n")
            real_open = os.open
            real_binary_flag = getattr(os, "O_BINARY", 0)
            synthetic_binary_flag = 1 << 29
            tracked_binary_flag = real_binary_flag | synthetic_binary_flag
            observed_flags: list[int] = []

            def tracked_open(
                candidate: object,
                flags: int,
                mode: int = 0o777,
                **kwargs: object,
            ) -> int:
                observed_flags.append(flags)
                return real_open(
                    candidate,
                    flags & ~synthetic_binary_flag,
                    mode,
                    **kwargs,
                )

            with (
                mock.patch.object(
                    CONFIGURE.os,
                    "O_BINARY",
                    tracked_binary_flag,
                    create=True,
                ),
                mock.patch.object(CONFIGURE.os, "open", side_effect=tracked_open),
            ):
                CONFIGURE.stage_existing_file(path, "new\n", staged)

            self.assertGreaterEqual(len(observed_flags), 3)
            self.assertTrue(
                all(flags & synthetic_binary_flag for flags in observed_flags)
            )
            self.assertEqual(staged.read_bytes(), b"new\n")

    @staticmethod
    def prepared_fallback_state(root: Path) -> tuple[Path, Path, dict[str, object]]:
        """Create a valid prepared journal whose original tombstone is unavailable."""
        transaction_id = "a" * 24
        path = root / "agent.toml"
        path.write_bytes(b"old\n")
        path.chmod(0o600)
        original_identity = CONFIGURE._path_identity(path)
        prefix = f".codex-orchestration-txn-{transaction_id}-0"
        staged_new = root / f"{prefix}-new"
        staged_old = root / f"{prefix}-old"
        tombstone = root / f"{prefix}-tombstone"

        CONFIGURE.stage_existing_file(path, "new\n", staged_new)
        CONFIGURE.stage_existing_file(path, "old\n", staged_old)
        staged_new_identity = CONFIGURE._path_identity(staged_new)
        staged_old_identity = CONFIGURE._path_identity(staged_old)
        staged_old_metadata = CONFIGURE._metadata_digest(staged_old)
        CONFIGURE.stage_text(path, "", 0o600, tombstone)
        placeholder_identity = CONFIGURE._path_identity(tombstone)
        tombstone.unlink()

        os.replace(staged_new, path)
        payload: dict[str, object] = {
            "marker": CONFIGURE.TRANSACTION_MARKER,
            "transaction_id": transaction_id,
            "phase": "prepared",
            "entries": [
                {
                    "destination": "agent.toml",
                    "existed": True,
                    "delete": False,
                    "old_sha256": CONFIGURE._sha256_text("old\n"),
                    "new_sha256": CONFIGURE._sha256_text("new\n"),
                    "original_identity": CONFIGURE._identity_json(original_identity),
                    "staged_new": staged_new.name,
                    "staged_new_identity": CONFIGURE._identity_json(
                        staged_new_identity
                    ),
                    "staged_old": staged_old.name,
                    "staged_old_identity": CONFIGURE._identity_json(
                        staged_old_identity
                    ),
                    "staged_old_metadata_sha256": staged_old_metadata,
                    "tombstone": tombstone.name,
                    "tombstone_placeholder_identity": CONFIGURE._identity_json(
                        placeholder_identity
                    ),
                    "installed_identity": CONFIGURE._identity_json(
                        staged_new_identity
                    ),
                    "installed_metadata_sha256": CONFIGURE._metadata_digest(path),
                }
            ],
        }
        CONFIGURE._write_transaction_journal(root, payload)
        return path, staged_old, payload

    def test_standalone_executor_schema_and_instructions(self) -> None:
        generated = CONFIGURE.build_agent_file(
            "executor", "executor-test", "medium", None
        )
        parsed = tomllib.loads(generated)

        self.assertEqual(parsed["name"], CONFIGURE.EXECUTOR_NAME)
        self.assertEqual(parsed["description"], CONFIGURE.EXECUTOR_DESCRIPTION)
        self.assertEqual(parsed["model"], "executor-test")
        self.assertEqual(parsed["model_reasoning_effort"], "medium")
        self.assertNotIn("sandbox_mode", parsed)
        self.assertIn("do not redesign the overall plan", parsed["developer_instructions"])
        self.assertIn("do not", parsed["developer_instructions"])
        self.assertIn("contact the advisor", parsed["developer_instructions"])
        self.assertTrue(generated.startswith(CONFIGURE.MANAGED_MARKER))

    def test_standalone_advisor_schema_is_read_only_and_root_only(self) -> None:
        generated = CONFIGURE.build_agent_file(
            "advisor", "advisor-test", "high", "anthropic"
        )
        parsed = tomllib.loads(generated)

        self.assertEqual(parsed["name"], CONFIGURE.ADVISOR_NAME)
        self.assertEqual(parsed["sandbox_mode"], "read-only")
        self.assertEqual(parsed["model_provider"], "anthropic")
        instructions = parsed["developer_instructions"]
        self.assertIn("PLAN_APPROVED", instructions)
        self.assertIn("PLAN_REVISE", instructions)
        self.assertIn("Address the root only", instructions)
        self.assertIn("Do not edit files", instructions)
        self.assertIn("contact executors", instructions)
        self.assertIn("no material gap in the supplied packet", instructions)
        self.assertIn("not a guarantee", instructions)
        self.assertIn("requests a read-only sandbox", parsed["description"])

    def test_clean_project_apply_creates_only_standalone_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, stdout, _ = self.run_main(root, "--apply")
            config, executor, advisor = self.paths(root)

            self.assertEqual(result, 0)
            self.assertFalse(config.exists())
            self.assertFalse(advisor.exists())
            parsed = tomllib.loads(executor.read_text(encoding="utf-8"))
            self.assertEqual(parsed["name"], CONFIGURE.EXECUTOR_NAME)
            self.assertEqual(parsed["model_reasoning_effort"], "medium")
            self.assertIn("Start a new Codex task", stdout)
            self.assertEqual(
                {
                    path.relative_to(root / ".codex").as_posix()
                    for path in (root / ".codex").rglob("*")
                    if path.is_file()
                },
                {"agents/codex-orchestration-executor.toml"},
            )

    def test_normal_apply_preserves_root_config_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            config.parent.mkdir(parents=True)
            original = '''# user config
model = "root-stays"
model_reasoning_effort = "xhigh"
model_provider = "openai"

[agents]
max_threads = 9
max_depth = 2

[features]
multi_agent = true
'''
            config.write_text(original, encoding="utf-8")

            result, _, _ = self.run_main(root, "--apply")

            self.assertEqual(result, 0)
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_dry_run_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, stdout, _ = self.run_main(root)
            self.assertEqual(result, 0)
            self.assertIn("Dry run only", stdout)
            self.assertFalse((root / ".codex").exists())

    def test_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(self.run_main(root, "--apply")[0], 0)
            _, executor, _ = self.paths(root)
            before = executor.read_bytes()

            result, stdout, _ = self.run_main(root, "--apply")

            self.assertEqual(result, 0)
            self.assertEqual(executor.read_bytes(), before)
            self.assertNotIn("--- ", stdout)

    def test_advisor_configure_preserve_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _ = self.run_main(
                root,
                "--advisor-model",
                "advisor-test",
                "--advisor-effort",
                "auto",
                "--apply",
            )
            _, _, advisor = self.paths(root)
            self.assertEqual(result, 0)
            before = advisor.read_bytes()

            result, _, stderr = self.run_main(root, "--apply")
            self.assertEqual(result, 0)
            self.assertEqual(advisor.read_bytes(), before)
            self.assertIn("left unchanged", stderr)

            result, _, _ = self.run_main(root, "--remove-advisor", "--apply")
            self.assertEqual(result, 0)
            self.assertFalse(advisor.exists())

    def test_remove_saved_roles_previews_then_removes_both_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _ = self.run_main(
                root,
                "--advisor-model",
                "advisor-test",
                "--advisor-effort",
                "high",
                "--apply",
            )
            config, executor, advisor = self.paths(root)
            self.assertEqual(result, 0)
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("model = \"root-owned\"\n", encoding="utf-8")

            result, stdout, _ = self.run_main(
                root,
                remove_saved_roles=True,
            )
            self.assertEqual(result, 0)
            self.assertIn("Dry run only", stdout)
            self.assertTrue(executor.exists())
            self.assertTrue(advisor.exists())

            result, _, _ = self.run_main(
                root,
                "--apply",
                remove_saved_roles=True,
            )
            self.assertEqual(result, 0)
            self.assertFalse(executor.exists())
            self.assertFalse(advisor.exists())
            self.assertEqual(
                config.read_text(encoding="utf-8"),
                'model = "root-owned"\n',
            )

    def test_remove_saved_roles_refuses_unmanaged_advisor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _ = self.run_main(root, "--apply")
            _, executor, advisor = self.paths(root)
            self.assertEqual(result, 0)
            advisor.write_text('name = "mine"\n', encoding="utf-8")

            result, _, stderr = self.run_main(
                root,
                "--apply",
                remove_saved_roles=True,
            )

            self.assertEqual(result, 2)
            self.assertIn("unmanaged custom agent", stderr)
            self.assertTrue(executor.exists())
            self.assertEqual(advisor.read_text(encoding="utf-8"), 'name = "mine"\n')

    def test_unmanaged_canonical_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, executor, _ = self.paths(root)
            executor.parent.mkdir(parents=True)
            executor.write_text(
                f'name = "{CONFIGURE.EXECUTOR_NAME}"\n'
                'description = "mine"\n'
                'developer_instructions = "mine"\n',
                encoding="utf-8",
            )

            result, _, stderr = self.run_main(root, "--apply")

            self.assertEqual(result, 2)
            self.assertIn("unmanaged custom agent", stderr)

    def test_zero_byte_canonical_agents_are_never_treated_as_absent(self) -> None:
        for role_index in (1, 2):
            with self.subTest(role_index=role_index), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = self.paths(root)[role_index]
                path.parent.mkdir(parents=True)
                path.touch()

                result, _, stderr = self.run_main(root, "--apply")

                self.assertEqual(result, 2)
                self.assertIn("unmanaged custom agent", stderr)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(), b"")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, _ = self.run_main(root, "--apply")
            _, executor, _ = self.paths(root)
            self.assertEqual(result, 0)
            executor.write_bytes(b"")

            result, _, stderr = self.run_main(
                root,
                "--apply",
                remove_saved_roles=True,
            )

            self.assertEqual(result, 2)
            self.assertIn("unmanaged custom agent", stderr)
            self.assertTrue(executor.exists())
            self.assertEqual(executor.read_bytes(), b"")

    def test_copied_marker_below_first_line_does_not_prove_agent_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, executor, _ = self.paths(root)
            executor.parent.mkdir(parents=True)
            generated = CONFIGURE.build_agent_file(
                "executor", "executor-test", "medium", None
            )
            executor.write_text("# user file\n" + generated, encoding="utf-8")

            result, _, stderr = self.run_main(root, "--apply")

            self.assertEqual(result, 2)
            self.assertIn("unmanaged custom agent", stderr)

    def test_modified_managed_fields_and_extra_keys_are_refused(self) -> None:
        mutations = (
            ('description = "changed"\n', "description"),
            ('sandbox_mode = "workspace-write"\n', "schema"),
            ('[mcp_servers.mine]\nurl = "https://example.test"\n', "schema"),
        )
        for addition, expected in mutations:
            with self.subTest(addition=addition), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, executor, _ = self.paths(root)
                executor.parent.mkdir(parents=True)
                base = CONFIGURE.build_agent_file(
                    "executor", "executor-test", "medium", None
                )
                if addition.startswith("description"):
                    parsed_line = f"description = {CONFIGURE.toml_string(CONFIGURE.EXECUTOR_DESCRIPTION)}\n"
                    base = base.replace(parsed_line, addition)
                else:
                    base += addition
                executor.write_text(base, encoding="utf-8")

                result, _, stderr = self.run_main(root, "--apply")
                self.assertEqual(result, 2)
                self.assertIn(expected, stderr)

    def test_duplicate_namespaced_name_in_another_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = root / ".codex" / "agents" / "another.toml"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text(
                f'name = "{CONFIGURE.EXECUTOR_NAME}"\n'
                'description = "duplicate"\n'
                'developer_instructions = "duplicate"\n',
                encoding="utf-8",
            )

            result, _, stderr = self.run_main(root, "--apply")
            self.assertEqual(result, 2)
            self.assertIn("already defined", stderr)

    def test_namespaced_collision_in_other_scope_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            personal = root / "personal-codex"
            existing = personal / "agents" / CONFIGURE.EXECUTOR_FILENAME
            existing.parent.mkdir(parents=True)
            existing.write_text(
                CONFIGURE.build_agent_file(
                    "executor", "executor-test", "medium", None
                ),
                encoding="utf-8",
            )

            result, _, stderr = self.run_main(
                root, "--apply", codex_home_env=personal
            )

            self.assertEqual(result, 2)
            self.assertIn("already defined", stderr)
            self.assertFalse(
                (root / ".codex" / "agents" / CONFIGURE.EXECUTOR_FILENAME).exists()
            )

    def test_project_scope_rejects_codex_home_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, stderr = self.run_main(
                root, "--codex-home", str(root / "alternate"), "--apply"
            )
            self.assertEqual(result, 2)
            self.assertIn("personal-scope only", stderr)
            self.assertFalse((root / ".codex").exists())

    def test_project_provider_flags_are_refused_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, stderr = self.run_main(
                root, "--executor-provider", "anthropic", "--apply"
            )
            self.assertEqual(result, 2)
            self.assertIn("Project-scoped", stderr)
            self.assertFalse((root / ".codex").exists())

    def test_personal_known_provider_is_written_but_definition_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / "config.toml"
            config.write_text(
                '[model_providers.anthropic]\nname = "Configured elsewhere"\nbase_url = "https://example.test"\n',
                encoding="utf-8",
            )
            result, _, _ = self.run_main(
                home,
                "--scope",
                "personal",
                "--codex-home",
                str(home),
                "--executor-provider",
                "anthropic",
                "--apply",
            )
            executor = home / "agents" / CONFIGURE.EXECUTOR_FILENAME

            self.assertEqual(result, 0)
            parsed = tomllib.loads(executor.read_text(encoding="utf-8"))
            self.assertEqual(parsed["model_provider"], "anthropic")
            self.assertEqual(
                config.read_text(encoding="utf-8"),
                '[model_providers.anthropic]\nname = "Configured elsewhere"\nbase_url = "https://example.test"\n',
            )
            self.assertNotIn("base_url", executor.read_text(encoding="utf-8"))

    def test_personal_route_names_are_stable_and_distinct_from_project_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "personal-codex"
            result, stdout, _ = self.run_main(
                root,
                "--scope",
                "personal",
                "--codex-home",
                str(codex_home),
                "--personal-route-names",
                "--apply",
                codex_home_env=codex_home,
            )

            self.assertEqual(result, 0)
            suffix = hashlib.sha256(os.fsencode(str(codex_home.resolve()))).hexdigest()[:12]
            expected_name = f"codex_orchestration_executor_{suffix}"
            expected_file = (
                codex_home
                / "agents"
                / f"codex-orchestration-executor-{suffix}.toml"
            )
            self.assertTrue(expected_file.is_file())
            parsed = tomllib.loads(expected_file.read_text(encoding="utf-8"))
            self.assertEqual(parsed["name"], expected_name)
            self.assertNotEqual(parsed["name"], CONFIGURE.DEFAULT_EXECUTOR_NAME)
            self.assertIn(f"Executor agent name: {expected_name}", stdout)

            removed, _, _ = self.run_main(
                root,
                "--scope",
                "personal",
                "--codex-home",
                str(codex_home),
                "--personal-route-names",
                "--apply",
                codex_home_env=codex_home,
                remove_saved_roles=True,
            )
            self.assertEqual(removed, 0)
            self.assertFalse(expected_file.exists())

    def test_personal_unknown_provider_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            result, _, stderr = self.run_main(
                home,
                "--scope",
                "personal",
                "--codex-home",
                str(home),
                "--executor-provider",
                "missing",
                "--apply",
            )
            self.assertEqual(result, 2)
            self.assertIn("neither built in nor defined", stderr)

    def test_catalog_source_is_reported_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, stdout, _ = self.run_main(Path(temporary))
            self.assertEqual(result, 0)
            self.assertIn("/mock/bin/codex", stdout)
            self.assertIn("codex-cli test", stdout)
            self.assertIn("--codex-bin=codex", stdout)

    def test_unlisted_confirmed_model_requires_explicit_effort(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result, _, stderr = self.run_main(
                root, "--confirm-unlisted-models", catalog={}
            )
            self.assertEqual(result, 2)
            self.assertIn("Choose an explicit executor effort", stderr)

            result, _, stderr = self.run_main(
                root,
                "--executor-effort",
                "high",
                "--confirm-unlisted-models",
                catalog={},
            )
            self.assertEqual(result, 0)
            self.assertIn("accepted from an external capability check", stderr)

    def test_external_confirmation_can_proceed_when_catalog_binary_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = [
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--codex-bin",
                "/missing/codex",
                "--executor-model",
                "desktop-confirmed-model",
                "--executor-effort",
                "high",
                "--confirm-unlisted-models",
            ]
            stdout = StringIO()
            stderr = StringIO()
            unavailable = CONFIGURE.ConfigurationError("binary not found")
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(CONFIGURE, "catalog_source", side_effect=unavailable),
                mock.patch.object(CONFIGURE, "load_catalog", side_effect=unavailable),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = CONFIGURE.main()

            self.assertEqual(result, 0)
            self.assertIn("Catalog source: unavailable", stdout.getvalue())
            self.assertIn("external host confirmation", stdout.getvalue())
            self.assertIn("Catalog source unavailable", stderr.getvalue())

    def test_direct_codex_bin_must_be_regular_and_executable(self) -> None:
        self.assertEqual(
            CONFIGURE.resolve_codex_executable(sys.executable),
            str(Path(sys.executable).resolve()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(CONFIGURE.ConfigurationError):
                CONFIGURE.resolve_codex_executable(str(root))
            binary = root / "codex"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o600)
            with self.assertRaises(CONFIGURE.ConfigurationError):
                CONFIGURE.resolve_codex_executable(str(binary))

    def test_catalog_subprocesses_have_timeouts_and_json_shape_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            if os.name == "nt":
                binary = Path(sys.executable)
            else:
                binary = Path(temporary) / "codex"
                binary.write_text("#!/bin/sh\n", encoding="utf-8")
                binary.chmod(0o700)
            with mock.patch.object(
                CONFIGURE.subprocess,
                "run",
                side_effect=CONFIGURE.subprocess.TimeoutExpired(
                    cmd=[str(binary), "--version"], timeout=1
                ),
            ):
                with self.assertRaisesRegex(CONFIGURE.ConfigurationError, "timed out"):
                    CONFIGURE.catalog_source(str(binary))

            malformed = CONFIGURE.subprocess.CompletedProcess(
                [str(binary), "debug", "models"], 0, stdout="[]", stderr=""
            )
            with mock.patch.object(CONFIGURE.subprocess, "run", return_value=malformed):
                with self.assertRaisesRegex(CONFIGURE.ConfigurationError, "root must be"):
                    CONFIGURE.load_catalog(str(binary), None)

            missing_models = CONFIGURE.subprocess.CompletedProcess(
                [str(binary), "debug", "models"], 0, stdout="{}", stderr=""
            )
            with mock.patch.object(
                CONFIGURE.subprocess, "run", return_value=missing_models
            ):
                with self.assertRaisesRegex(CONFIGURE.ConfigurationError, "models array"):
                    CONFIGURE.load_catalog(str(binary), None)

            valid = CONFIGURE.subprocess.CompletedProcess(
                [str(binary), "debug", "models"],
                0,
                stdout='{"models": []}',
                stderr="",
            )
            project = Path(temporary) / "project"
            home = Path(temporary) / "codex-home"
            project.mkdir()
            with mock.patch.object(
                CONFIGURE.subprocess, "run", return_value=valid
            ) as run:
                CONFIGURE.load_catalog(
                    str(binary), None, cwd=project, codex_home=home
                )
            self.assertEqual(run.call_args.kwargs["cwd"], project)
            self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(home))

            invalid_utf8 = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
            for function, arguments in (
                (CONFIGURE.catalog_source, (str(binary),)),
                (CONFIGURE.load_catalog, (str(binary), None)),
            ):
                with self.subTest(function=function.__name__), mock.patch.object(
                    CONFIGURE.subprocess,
                    "run",
                    side_effect=invalid_utf8,
                ):
                    with self.assertRaisesRegex(
                        CONFIGURE.ConfigurationError,
                        "invalid UTF-8",
                    ):
                        function(*arguments)

    def test_auto_effort_resolves_per_seat(self) -> None:
        self.assertEqual(
            CONFIGURE.resolve_role_effort("auto", "Executor", "executor-test", CATALOG),
            "medium",
        )
        self.assertEqual(
            CONFIGURE.resolve_role_effort("auto", "Advisor", "advisor-test", CATALOG),
            "high",
        )

    def test_symlinked_managed_components_are_refused(self) -> None:
        for target in (".codex", ".codex/agents", f".codex/agents/{CONFIGURE.EXECUTOR_FILENAME}"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outside = root / "outside"
                if target.endswith(".toml"):
                    outside.write_text("must stay", encoding="utf-8")
                else:
                    outside.mkdir()
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    path.symlink_to(outside, target_is_directory=outside.is_dir())
                except OSError as exc:
                    if os.name == "nt" and exc.winerror == 1314:
                        self.skipTest("Windows symlink privilege is unavailable")
                    raise

                result, _, stderr = self.run_main(root, "--apply")
                self.assertEqual(result, 2)
                self.assertIn("symlinked", stderr.lower())
                if outside.is_file():
                    self.assertEqual(outside.read_text(encoding="utf-8"), "must stay")

    def test_symlinked_unrelated_agent_is_refused_during_collision_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agents = root / ".codex" / "agents"
            agents.mkdir(parents=True)
            outside = root / "outside.toml"
            outside.write_text('name = "other"\n', encoding="utf-8")
            try:
                (agents / "linked.toml").symlink_to(outside)
            except OSError as exc:
                if os.name == "nt" and exc.winerror == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise

            result, _, stderr = self.run_main(root, "--apply")
            self.assertEqual(result, 2)
            self.assertIn("symlinked custom-agent", stderr)

    def test_previous_routing_format_requires_explicit_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            config.write_text(legacy_config(), encoding="utf-8")
            layer.write_text(legacy_layer("old-executor"), encoding="utf-8")

            result, _, stderr = self.run_main(root, "--apply")
            self.assertEqual(result, 2)
            self.assertIn("--migrate-legacy", stderr)
            self.assertTrue(layer.exists())

    def test_routing_migration_preserves_root_and_limits_and_makes_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, executor, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            prefix = '''model = "root-stays"
model_reasoning_effort = "xhigh"

[agents]
max_threads = 4
max_depth = 1'''
            original_config = legacy_config(prefix=prefix)
            original_layer = legacy_layer("old-executor")
            config.write_text(original_config, encoding="utf-8")
            layer.write_text(original_layer, encoding="utf-8")

            result, _, stderr = self.run_main(
                root, "--migrate-legacy", "--apply"
            )
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))

            self.assertEqual(result, 0)
            self.assertEqual(parsed["model"], "root-stays")
            self.assertEqual(parsed["model_reasoning_effort"], "xhigh")
            self.assertEqual(parsed["agents"]["max_threads"], 4)
            self.assertEqual(parsed["agents"]["max_depth"], 1)
            self.assertNotIn("executor", parsed["agents"])
            self.assertFalse(layer.exists())
            self.assertTrue(executor.exists())
            self.assertEqual(
                config.with_name(config.name + ".bak.codex-orchestration").read_text(
                    encoding="utf-8"
                ),
                original_config,
            )
            self.assertEqual(
                layer.with_name(layer.name + ".bak.codex-orchestration").read_text(
                    encoding="utf-8"
                ),
                original_layer,
            )
            self.assertIn("preserved unchanged", stderr)

    def test_crlf_managed_files_update_remove_and_migrate_byte_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, executor, _ = self.paths(root)
            executor.parent.mkdir(parents=True)
            crlf_executor = CONFIGURE.build_agent_file(
                "executor", "executor-test", "medium", None
            ).replace("\n", "\r\n")
            executor.write_bytes(crlf_executor.encode("utf-8"))

            result, _, _ = self.run_main(
                root,
                "--executor-effort",
                "high",
                "--apply",
            )
            self.assertEqual(result, 0)
            self.assertIn(b'model_reasoning_effort = "high"', executor.read_bytes())

            executor.write_bytes(crlf_executor.encode("utf-8"))
            result, _, _ = self.run_main(
                root,
                "--apply",
                remove_saved_roles=True,
            )
            self.assertEqual(result, 0)
            self.assertFalse(executor.exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, executor, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            original_config = legacy_config(
                prefix='model = "root-stays"\n\n[agents]\nmax_threads = 4'
            ).replace("\n", "\r\n")
            original_layer = legacy_layer("old-executor").replace("\n", "\r\n")
            config.write_bytes(original_config.encode("utf-8"))
            layer.write_bytes(original_layer.encode("utf-8"))

            result, _, _ = self.run_main(root, "--migrate-legacy", "--apply")

            self.assertEqual(result, 0)
            self.assertTrue(executor.exists())
            self.assertFalse(layer.exists())
            parsed = tomllib.loads(CONFIGURE.read_text(config))
            self.assertEqual(parsed["model"], "root-stays")
            self.assertEqual(parsed["agents"]["max_threads"], 4)
            self.assertEqual(
                config.with_name(config.name + ".bak.codex-orchestration").read_bytes(),
                original_config.encode("utf-8"),
            )
            self.assertEqual(
                layer.with_name(layer.name + ".bak.codex-orchestration").read_bytes(),
                original_layer.encode("utf-8"),
            )

    def test_migration_preview_redacts_unrelated_config_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            secret = "DO_NOT_PRINT_THIS_SECRET"
            config.write_text(
                legacy_config(prefix=f'API_TOKEN = "{secret}"'),
                encoding="utf-8",
            )
            layer.write_text(legacy_layer("old-executor"), encoding="utf-8")

            result, stdout, stderr = self.run_main(root, "--migrate-legacy")

            self.assertEqual(result, 0)
            self.assertNotIn(secret, stdout)
            self.assertNotIn(secret, stderr)
            self.assertIn("contents redacted", stdout)
            self.assertIn("diff context are redacted", stdout)

    def test_ff26623_marker_migrates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            config.write_text(
                legacy_config(marker=CONFIGURE.PREVIOUS_ROUTING_MARKER),
                encoding="utf-8",
            )
            layer.write_text(
                legacy_layer(
                    "old-executor", marker=CONFIGURE.PREVIOUS_ROUTING_MARKER
                ),
                encoding="utf-8",
            )

            result, _, _ = self.run_main(root, "--migrate-legacy", "--apply")
            self.assertEqual(result, 0)
            self.assertFalse(layer.exists())

    def test_legacy_advisor_is_converted_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, advisor = self.paths(root)
            agents = root / ".codex" / "agents"
            agents.mkdir(parents=True)
            config.write_text(legacy_config(advisor=True), encoding="utf-8")
            (agents / CONFIGURE.LEGACY_EXECUTOR_LAYER).write_text(
                legacy_layer("old-executor"), encoding="utf-8"
            )
            (agents / CONFIGURE.LEGACY_ADVISOR_LAYER).write_text(
                legacy_layer("advisor-test"), encoding="utf-8"
            )

            result, _, stderr = self.run_main(
                root, "--migrate-legacy", "--apply"
            )
            parsed = tomllib.loads(advisor.read_text(encoding="utf-8"))

            self.assertEqual(result, 0)
            self.assertEqual(parsed["model"], "advisor-test")
            self.assertEqual(parsed["sandbox_mode"], "read-only")
            self.assertIn("converted", stderr)

    def test_initial_v1_default_and_custom_agents_migrate_only_with_exact_template(self) -> None:
        for name in ("orchestrated_executor", "custom_worker"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, _, _ = self.paths(root)
                agent = root / ".codex" / "agents" / f"{name}.toml"
                agent.parent.mkdir(parents=True)
                config.write_text(
                    CONFIGURE.V1_MARKER
                    + '\nmodel = "old-root"\n\n[agents]\nmax_threads = 3\nmax_depth = 1\n',
                    encoding="utf-8",
                )
                original = v1_agent(name)
                agent.write_text(original, encoding="utf-8")

                result, _, _ = self.run_main(
                    root, "--migrate-legacy", "--apply"
                )
                self.assertEqual(result, 0)
                self.assertFalse(agent.exists())
                self.assertEqual(
                    agent.with_name(agent.name + ".bak.codex-orchestration").read_text(
                        encoding="utf-8"
                    ),
                    original,
                )
                parsed = tomllib.loads(config.read_text(encoding="utf-8"))
                self.assertEqual(parsed["model"], "old-root")
                self.assertEqual(parsed["agents"]["max_threads"], 3)

    def test_modified_v1_agent_is_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            agent = root / ".codex" / "agents" / CONFIGURE.LEGACY_V1_DEFAULT_FILENAME
            agent.parent.mkdir(parents=True)
            config.write_text(CONFIGURE.V1_MARKER + "\n", encoding="utf-8")
            original = v1_agent().replace(CONFIGURE.V1_DESCRIPTION, "User changed it")
            agent.write_text(original, encoding="utf-8")

            result, _, stderr = self.run_main(
                root, "--migrate-legacy", "--apply"
            )
            self.assertEqual(result, 2)
            self.assertIn("modified", stderr)
            self.assertEqual(agent.read_text(encoding="utf-8"), original)

    def test_incomplete_legacy_route_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            config.parent.mkdir(parents=True)
            config.write_text(legacy_config(), encoding="utf-8")

            result, _, stderr = self.run_main(
                root, "--migrate-legacy", "--apply"
            )
            self.assertEqual(result, 2)
            self.assertIn("missing or unmanaged layer", stderr)

    def test_legacy_layer_marker_below_first_line_does_not_prove_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.paths(root)
            layer = root / ".codex" / "agents" / CONFIGURE.LEGACY_EXECUTOR_LAYER
            layer.parent.mkdir(parents=True)
            config.write_text(legacy_config(), encoding="utf-8")
            layer.write_text(
                "# copied comment\n" + legacy_layer("old-executor"),
                encoding="utf-8",
            )

            result, _, stderr = self.run_main(
                root, "--migrate-legacy", "--apply"
            )

            self.assertEqual(result, 2)
            self.assertIn("not on its emitted first line", stderr)
            self.assertTrue(layer.exists())

    def test_every_legacy_config_marker_must_be_first_line(self) -> None:
        for marker in (
            CONFIGURE.ROUTING_MARKER,
            CONFIGURE.PREVIOUS_ROUTING_MARKER,
            CONFIGURE.V1_MARKER,
        ):
            with self.subTest(marker=marker):
                text = 'model = "user"\n' + marker + "\n"
                with self.assertRaisesRegex(CONFIGURE.ConfigurationError, "first-line"):
                    CONFIGURE.legacy_config_marker(text)

    def test_marker_and_header_inside_multiline_string_are_preserved(self) -> None:
        text = '''note = """
# Managed by codex-orchestration. Model routing only.
[agents.executor]
description = "not a real table"
"""

[features]
multi_agent = true
'''
        self.assertFalse(
            CONFIGURE.has_exact_marker(text, CONFIGURE.LEGACY_CONFIG_MARKERS)
        )
        self.assertEqual(CONFIGURE.real_table_headers(text), [(6, "features")])

    def test_surgical_cleanup_preserves_multiline_strings_and_unrelated_tables(self) -> None:
        prefix = '''note = """
[agents.executor]
This is text, not a table.
"""

[features]
multi_agent = true'''
        text = legacy_config(prefix=prefix)
        cleaned = CONFIGURE.remove_legacy_tables_and_markers(text, {"executor"})
        parsed = tomllib.loads(cleaned)

        self.assertIn("[agents.executor]", parsed["note"])
        self.assertTrue(parsed["features"]["multi_agent"])
        self.assertNotIn("agents", parsed)

    def test_surgical_cleanup_preserves_comments_after_generated_assignments(self) -> None:
        text = legacy_config(suffix="# user trailing note with no following table")
        cleaned = CONFIGURE.remove_legacy_tables_and_markers(text, {"executor"})

        self.assertIn("# user trailing note with no following table", cleaned)
        self.assertEqual(tomllib.loads(cleaned), {})

    def test_personal_legacy_advisor_provider_must_still_be_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            agents = home / "agents"
            agents.mkdir()
            (home / "config.toml").write_text(
                legacy_config(executor=False, advisor=True), encoding="utf-8"
            )
            (agents / CONFIGURE.LEGACY_ADVISOR_LAYER).write_text(
                legacy_layer(
                    "advisor-test", model_provider="missing-provider"
                ),
                encoding="utf-8",
            )

            result, _, stderr = self.run_main(
                home,
                "--scope",
                "personal",
                "--codex-home",
                str(home),
                "--migrate-legacy",
                "--apply",
            )

            self.assertEqual(result, 2)
            self.assertIn("neither built in nor defined", stderr)

    def test_different_existing_backup_blocks_migration(self) -> None:
        for existing_backup in ("different", ""):
            with self.subTest(existing_backup=existing_backup):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    config, _, _ = self.paths(root)
                    layer = (
                        root
                        / ".codex"
                        / "agents"
                        / CONFIGURE.LEGACY_EXECUTOR_LAYER
                    )
                    layer.parent.mkdir(parents=True)
                    config.write_text(legacy_config(), encoding="utf-8")
                    layer.write_text(legacy_layer("old-executor"), encoding="utf-8")
                    backup = layer.with_name(
                        layer.name + ".bak.codex-orchestration"
                    )
                    backup.write_text(existing_backup, encoding="utf-8")

                    result, _, stderr = self.run_main(
                        root, "--migrate-legacy", "--apply"
                    )
                    self.assertEqual(result, 2)
                    self.assertIn("different data", stderr)
                    self.assertTrue(layer.exists())

    def test_hard_linked_managed_file_cannot_modify_an_outside_peer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            self.run_main(root, "--apply")
            _, executor, _ = self.paths(root)
            outside = Path(temporary) / "outside-agent.toml"
            os.link(executor, outside)
            before = outside.read_text(encoding="utf-8")

            result, _, stderr = self.run_main(
                root,
                "--executor-effort",
                "high",
                "--apply",
            )

            self.assertEqual(result, 2)
            self.assertIn("hard-linked", stderr)
            self.assertEqual(executor.read_text(encoding="utf-8"), before)
            self.assertEqual(outside.read_text(encoding="utf-8"), before)

    def test_transaction_failure_restores_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.run_main(
                root,
                "--advisor-model",
                "advisor-test",
                "--advisor-effort",
                "high",
                "--apply",
            )
            _, executor, advisor = self.paths(root)
            before = {path: path.read_text(encoding="utf-8") for path in (executor, advisor)}
            real_replace = CONFIGURE._replace_staged_atomically
            injected = False

            def fail_advisor_once(
                staged: Path,
                destination: Path,
                tombstone: Path,
                staged_identity: tuple[int, int],
                original_identity: tuple[int, int],
                expected_metadata: tuple[object, ...],
                expected_content_sha256: str,
                replacement_content_sha256: str,
            ) -> None:
                nonlocal injected
                if Path(destination) == advisor and not injected:
                    injected = True
                    raise OSError("injected")
                real_replace(
                    staged,
                    destination,
                    tombstone,
                    staged_identity,
                    original_identity,
                    expected_metadata,
                    expected_content_sha256,
                    replacement_content_sha256,
                )

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=fail_advisor_once,
            ):
                result, _, stderr = self.run_main(
                    root,
                    "--executor-effort",
                    "high",
                    "--advisor-model",
                    "advisor-test",
                    "--advisor-effort",
                    "xhigh",
                    "--apply",
                )

            self.assertTrue(injected)
            self.assertEqual(result, 2)
            self.assertIn("committed files were restored", stderr)
            self.assertEqual(
                {path: path.read_text(encoding="utf-8") for path in (executor, advisor)},
                before,
            )

    def test_final_readback_failure_rolls_back_the_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.toml"
            path.write_text("old\n", encoding="utf-8")
            real_read_text = CONFIGURE.read_text
            reads = 0

            def corrupt_final_readback(candidate: Path) -> str:
                nonlocal reads
                if candidate == path:
                    reads += 1
                    if reads == 3:
                        return "corrupt\n"
                return real_read_text(candidate)

            with mock.patch.object(
                CONFIGURE, "read_text", side_effect=corrupt_final_readback
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError, "committed files were restored"
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")]
                    )

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")

    def test_incomplete_rollback_keeps_recovery_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_bytes(b"first-old\n")
            second.write_bytes(b"second-old\n")
            real_replace = CONFIGURE._replace_staged_atomically
            real_restore = CONFIGURE._restore_original_from_tombstone
            second_failed = False
            first_restore_failed = False

            def fail_second_commit(
                staged: Path,
                destination: Path,
                tombstone: Path,
                staged_identity: tuple[int, int],
                original_identity: tuple[int, int],
                expected_metadata: tuple[object, ...],
                expected_content_sha256: str,
                replacement_content_sha256: str,
            ) -> None:
                nonlocal second_failed
                destination_path = Path(destination)
                if destination_path == second and not second_failed:
                    second_failed = True
                    raise OSError("injected commit failure")
                real_replace(
                    staged,
                    destination,
                    tombstone,
                    staged_identity,
                    original_identity,
                    expected_metadata,
                    expected_content_sha256,
                    replacement_content_sha256,
                )

            def fail_first_rollback(
                destination: Path,
                tombstone: Path | None,
                original_identity: tuple[int, int],
                installed_identity: tuple[int, int] | None,
            ) -> None:
                nonlocal first_restore_failed
                if destination == first and not first_restore_failed:
                    first_restore_failed = True
                    raise OSError("injected rollback failure")
                real_restore(
                    destination,
                    tombstone,
                    original_identity,
                    installed_identity,
                )

            with (
                mock.patch.object(
                    CONFIGURE,
                    "_replace_staged_atomically",
                    side_effect=fail_second_commit,
                ),
                mock.patch.object(
                    CONFIGURE,
                    "_restore_original_from_tombstone",
                    side_effect=fail_first_rollback,
                ),
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "rollback was incomplete.*recovery journal kept at",
                ) as raised:
                    CONFIGURE.apply_changes_transactionally(
                        [
                            (first, "first-old\n", "first-new\n"),
                            (second, "second-old\n", "second-new\n"),
                        ]
                    )

            journal = Path(
                str(raised.exception).split("recovery journal kept at ", 1)[1]
            )
            self.assertTrue(journal.exists())
            recovery_paths = [
                path
                for path in journal.parent.iterdir()
                if path.name.startswith(".codex-orchestration-txn-")
            ]
            self.assertTrue(recovery_paths)
            self.assertIn(
                "first-old\n",
                [
                    path.read_text(encoding="utf-8")
                    for path in recovery_paths
                    if path.is_file()
                ],
            )
            self.assertTrue(first_restore_failed)

    def test_keyboard_interrupt_rolls_back_before_propagating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_bytes(b"first-old\n")
            second.write_bytes(b"second-old\n")
            real_replace = CONFIGURE._replace_staged_atomically
            interrupted = False

            def interrupt_second(
                staged: Path,
                destination: Path,
                tombstone: Path,
                staged_identity: tuple[int, int],
                original_identity: tuple[int, int],
                expected_metadata: tuple[object, ...],
                expected_content_sha256: str,
                replacement_content_sha256: str,
            ) -> None:
                nonlocal interrupted
                if destination == second and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt()
                real_replace(
                    staged,
                    destination,
                    tombstone,
                    staged_identity,
                    original_identity,
                    expected_metadata,
                    expected_content_sha256,
                    replacement_content_sha256,
                )

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=interrupt_second,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    CONFIGURE.apply_changes_transactionally(
                        [
                            (first, "first-old\n", "first-new\n"),
                            (second, "second-old\n", "second-new\n"),
                        ]
                    )

            self.assertTrue(interrupted)
            self.assertEqual(first.read_text(encoding="utf-8"), "first-old\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-old\n")

    def test_atomic_update_preserves_security_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.toml"
            path.write_bytes(b"old\n")
            path.chmod(0o640)
            before = path.stat()
            xattr_name = "user.codex_orchestration_test"
            xattr_supported = False
            if hasattr(os, "setxattr"):
                try:
                    os.setxattr(path, xattr_name, b"preserve")
                    xattr_supported = True
                except OSError:
                    pass
            elif shutil.which("xattr"):
                completed = subprocess.run(
                    ["xattr", "-w", xattr_name, "preserve", str(path)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                xattr_supported = completed.returncode == 0

            acl_before = None
            if sys.platform == "darwin":
                completed = subprocess.run(
                    [
                        "/bin/chmod",
                        "+a",
                        f"{getpass.getuser()} allow read",
                        str(path),
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                if completed.returncode == 0:
                    acl_before = CONFIGURE._acl_snapshot(path)
            xattrs_before = (
                CONFIGURE._xattr_snapshot(path) if xattr_supported else None
            )

            CONFIGURE.apply_changes_transactionally(
                [(path, "old\n", "new\n")]
            )

            after = path.stat()
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertNotEqual(after.st_ino, before.st_ino)
            self.assertEqual(after.st_mode & 0o777, before.st_mode & 0o777)
            if xattr_supported:
                self.assertEqual(CONFIGURE._xattr_snapshot(path), xattrs_before)
            if acl_before is not None:
                self.assertEqual(CONFIGURE._acl_snapshot(path), acl_before)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_abrupt_exit_while_staging_cannot_corrupt_live_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.toml"
            old = "A" * (1024 * 1024)
            new = "B" * (1024 * 1024)
            path.write_text(old, encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                def crash_during_staging(
                    staged: Path,
                    content: str,
                    expected_identity: tuple[int, int],
                ) -> None:
                    descriptor = os.open(staged, os.O_WRONLY)
                    os.write(descriptor, content[:16].encode("utf-8"))
                    os.fsync(descriptor)
                    os._exit(77)

                CONFIGURE._write_staged_content = crash_during_staging
                CONFIGURE.apply_changes_transactionally([(path, old, new)])
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 77)
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            root = Path(temporary)
            journal = root / CONFIGURE.TRANSACTION_JOURNAL
            self.assertTrue(journal.exists())
            journal_text = journal.read_text(encoding="utf-8")
            self.assertNotIn("A" * 32, journal_text)
            self.assertNotIn("B" * 32, journal_text)

            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            self.assertFalse(journal.exists())
            self.assertFalse(
                any(
                    candidate.name.startswith(".codex-orchestration-txn-")
                    for candidate in root.iterdir()
                )
            )

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_crash_after_tombstone_link_is_recovered_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_text("old\n", encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                def crash_before_publish(*args: object) -> None:
                    os._exit(78)

                CONFIGURE._replace_staged_atomically = crash_before_publish
                CONFIGURE.apply_changes_transactionally(
                    [(path, "old\n", "new\n")],
                    transaction_root=root,
                )
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertEqual(os.WEXITSTATUS(status), 78)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_nlink, 2)
            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_crash_between_two_swaps_rolls_back_from_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_text("first-old\n", encoding="utf-8")
            second.write_text("second-old\n", encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                real_replace = CONFIGURE._replace_staged_atomically
                replacements = 0

                def crash_before_second_swap(*args: object) -> None:
                    nonlocal replacements
                    replacements += 1
                    if replacements == 2:
                        os._exit(79)
                    real_replace(*args)

                CONFIGURE._replace_staged_atomically = crash_before_second_swap
                CONFIGURE.apply_changes_transactionally(
                    [
                        (first, "first-old\n", "first-new\n"),
                        (second, "second-old\n", "second-new\n"),
                    ],
                    transaction_root=root,
                )
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertEqual(os.WEXITSTATUS(status), 79)
            self.assertEqual(first.read_text(encoding="utf-8"), "first-new\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-old\n")

            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(first.read_text(encoding="utf-8"), "first-old\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-old\n")
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())
            self.assertFalse(
                any(
                    candidate.name.startswith(".codex-orchestration-txn-")
                    for candidate in root.iterdir()
                )
            )

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_crash_after_commit_marker_keeps_new_state_and_cleans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_text("old\n", encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                real_write_journal = CONFIGURE._write_transaction_journal

                def crash_after_commit(
                    transaction_root: Path,
                    payload: dict[str, object],
                ) -> None:
                    real_write_journal(transaction_root, payload)
                    if payload.get("phase") == "committed":
                        os._exit(80)

                CONFIGURE._write_transaction_journal = crash_after_commit
                CONFIGURE.apply_changes_transactionally(
                    [(path, "old\n", "new\n")],
                    transaction_root=root,
                )
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertEqual(os.WEXITSTATUS(status), 80)
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            path.write_text("post-commit user edit\n", encoding="utf-8")
            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "post-commit user edit\n",
            )
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_interrupt_after_durable_commit_marker_never_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.toml"
            second = root / "second.toml"
            first.write_bytes(b"first-old\n")
            second.write_bytes(b"second-old\n")
            real_write_journal = CONFIGURE._write_transaction_journal
            interrupted = False

            def interrupt_after_durable_commit(
                transaction_root: Path,
                payload: dict[str, object],
            ) -> None:
                nonlocal interrupted
                real_write_journal(transaction_root, payload)
                if payload.get("phase") == "committed":
                    interrupted = True
                    raise KeyboardInterrupt()

            with (
                mock.patch.object(
                    CONFIGURE,
                    "_write_transaction_journal",
                    side_effect=interrupt_after_durable_commit,
                ),
                mock.patch.object(
                    CONFIGURE,
                    "_restore_original_from_tombstone",
                    side_effect=AssertionError("rollback must not run after commit"),
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    CONFIGURE.apply_changes_transactionally(
                        [
                            (first, "first-old\n", "first-new\n"),
                            (second, "second-old\n", "second-new\n"),
                        ],
                        transaction_root=root,
                    )

            self.assertTrue(interrupted)
            self.assertEqual(first.read_text(encoding="utf-8"), "first-new\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-new\n")
            journal = root / CONFIGURE.TRANSACTION_JOURNAL
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["phase"],
                "committed",
            )

            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(first.read_text(encoding="utf-8"), "first-new\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second-new\n")
            self.assertFalse(journal.exists())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_prepared_recovery_refuses_to_overwrite_post_crash_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_text("old\n", encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                real_write_journal = CONFIGURE._write_transaction_journal

                def crash_before_commit_marker(
                    transaction_root: Path,
                    payload: dict[str, object],
                ) -> None:
                    if payload.get("phase") == "committed":
                        os._exit(82)
                    real_write_journal(transaction_root, payload)

                CONFIGURE._write_transaction_journal = crash_before_commit_marker
                CONFIGURE.apply_changes_transactionally(
                    [(path, "old\n", "new\n")],
                    transaction_root=root,
                )
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertEqual(os.WEXITSTATUS(status), 82)
            path.write_text("post-crash user edit\n", encoding="utf-8")

            with self.assertRaisesRegex(
                CONFIGURE.ConfigurationError,
                "was modified",
            ):
                CONFIGURE.recover_incomplete_transaction(root)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "post-crash user edit\n",
            )
            self.assertTrue((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_prepared_recovery_uses_verified_staged_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, staged_old, _ = self.prepared_fallback_state(root)
            expected_mode = staged_old.stat().st_mode & 0o777

            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_mode & 0o777, expected_mode)
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_prepared_fallback_recovery_is_idempotent_after_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, staged_old, _ = self.prepared_fallback_state(root)
            real_replace = CONFIGURE._replace_exact
            interrupted = False

            def interrupt_after_fallback_replace(
                source: Path,
                target: Path,
                expected_identity: tuple[int, int],
            ) -> None:
                nonlocal interrupted
                real_replace(source, target, expected_identity)
                if source == staged_old and target == path:
                    interrupted = True
                    raise KeyboardInterrupt()

            with mock.patch.object(
                CONFIGURE,
                "_replace_exact",
                side_effect=interrupt_after_fallback_replace,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    CONFIGURE.recover_incomplete_transaction(root)

            self.assertTrue(interrupted)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertTrue((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

            self.assertTrue(CONFIGURE.recover_incomplete_transaction(root))
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_prepared_recovery_refuses_hardlinked_staged_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, staged_old, _ = self.prepared_fallback_state(root)
            outside = root / "outside.toml"
            os.link(staged_old, outside)

            with self.assertRaisesRegex(
                CONFIGURE.ConfigurationError,
                "Staged recovery copy is unsafe",
            ):
                CONFIGURE.recover_incomplete_transaction(root)

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(staged_old.stat().st_nlink, 2)
            self.assertTrue((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_prepared_recovery_refuses_metadata_tampered_staged_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, staged_old, _ = self.prepared_fallback_state(root)
            tampered_mode = 0o444 if os.name == "nt" else 0o777
            staged_old.chmod(tampered_mode)
            observed_tampered_mode = staged_old.stat().st_mode & 0o777

            with self.assertRaisesRegex(
                CONFIGURE.ConfigurationError,
                "Staged recovery copy was modified",
            ):
                CONFIGURE.recover_incomplete_transaction(root)

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(
                staged_old.stat().st_mode & 0o777, observed_tampered_mode
            )
            self.assertTrue((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_journal_rejects_misdirected_transaction_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _ = self.prepared_fallback_state(root)
            journal = root / CONFIGURE.TRANSACTION_JOURNAL
            payload = json.loads(journal.read_text(encoding="utf-8"))
            transaction_id = payload["transaction_id"]
            (root / "sub").mkdir()
            payload["entries"][0]["staged_new"] = (
                f"sub/.codex-orchestration-txn-{transaction_id}-arbitrary"
            )

            with self.assertRaisesRegex(
                CONFIGURE.ConfigurationError,
                "temporary path is invalid",
            ):
                CONFIGURE._validate_transaction_payload(root, payload)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_main_requires_apply_then_recovers_before_new_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            base = project / ".codex"
            base.mkdir()
            interrupted_path = base / "interrupted.toml"
            interrupted_path.write_text("old\n", encoding="utf-8")

            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                real_write_journal = CONFIGURE._write_transaction_journal

                def crash_before_commit_marker(
                    transaction_root: Path,
                    payload: dict[str, object],
                ) -> None:
                    if payload.get("phase") == "committed":
                        os._exit(81)
                    real_write_journal(transaction_root, payload)

                CONFIGURE._write_transaction_journal = crash_before_commit_marker
                CONFIGURE.apply_changes_transactionally(
                    [(interrupted_path, "old\n", "new\n")],
                    transaction_root=base,
                )
                os._exit(0)

            _, status = os.waitpid(child, 0)
            self.assertEqual(os.WEXITSTATUS(status), 81)
            self.assertEqual(interrupted_path.read_text(encoding="utf-8"), "new\n")

            result, _, stderr = self.run_main(project)
            self.assertEqual(result, 2)
            self.assertIn("interrupted configuration transaction", stderr)

            result, _, _ = self.run_main(project, "--apply")
            self.assertEqual(result, 0)
            self.assertEqual(interrupted_path.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((base / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_late_link_to_original_never_receives_new_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            outside = root / "outside.toml"
            path.write_bytes(b"old\n")
            real_replace = CONFIGURE._replace_staged_atomically
            injected = False

            def link_then_replace(
                staged: Path,
                destination: Path,
                tombstone: Path,
                staged_identity: tuple[int, int],
                original_identity: tuple[int, int],
                expected_metadata: tuple[object, ...],
                expected_content_sha256: str,
                replacement_content_sha256: str,
            ) -> None:
                nonlocal injected
                if not injected:
                    os.link(destination, outside)
                    injected = True
                real_replace(
                    staged,
                    destination,
                    tombstone,
                    staged_identity,
                    original_identity,
                    expected_metadata,
                    expected_content_sha256,
                    replacement_content_sha256,
                )

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=link_then_replace,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "hard-link race|metadata changed",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")]
                    )

            self.assertTrue(injected)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(outside.read_text(encoding="utf-8"), "old\n")

    def test_restore_refuses_changed_original_link_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            tombstone = root / "tombstone"
            outside = root / "outside.toml"
            path.write_text("old\n", encoding="utf-8")
            original_identity = CONFIGURE._path_identity(path)
            CONFIGURE.stage_text(path, "", 0o600, tombstone)
            os.link(path, outside)

            with self.assertRaisesRegex(
                CONFIGURE.ConfigurationError,
                "link topology changed",
            ):
                CONFIGURE._restore_original_from_tombstone(
                    path,
                    tombstone,
                    original_identity,
                    None,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_nlink, 2)
            self.assertTrue(tombstone.exists())

    def test_concurrent_metadata_change_is_preserved_and_aborts_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_bytes(b"old\n")
            changed_mode = 0o444 if os.name == "nt" else 0o600
            path.chmod(0o666 if os.name == "nt" else 0o640)
            real_replace = CONFIGURE._replace_staged_atomically
            injected = False

            def chmod_then_replace(*args: object) -> None:
                nonlocal injected
                destination = args[1]
                if not injected:
                    Path(destination).chmod(changed_mode)
                    injected = True
                real_replace(*args)

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=chmod_then_replace,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "metadata changed",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")],
                        transaction_root=root,
                    )

            self.assertTrue(injected)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_mode & 0o777, changed_mode)

    def test_metadata_change_in_final_prepublication_window_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_bytes(b"old\n")
            changed_mode = 0o444 if os.name == "nt" else 0o600
            path.chmod(0o666 if os.name == "nt" else 0o640)
            real_signature = CONFIGURE._metadata_signature
            injected = False

            def chmod_while_staged_signature_is_read(
                candidate: Path,
            ) -> tuple[object, ...]:
                nonlocal injected
                if (
                    candidate != path
                    and candidate.name.endswith("-new")
                    and path.stat().st_nlink == 2
                    and not injected
                ):
                    path.chmod(changed_mode)
                    injected = True
                return real_signature(candidate)

            with mock.patch.object(
                CONFIGURE,
                "_metadata_signature",
                side_effect=chmod_while_staged_signature_is_read,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "metadata changed",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")],
                        transaction_root=root,
                    )

            self.assertTrue(injected)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(path.stat().st_mode & 0o777, changed_mode)
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_content_change_in_final_prepublication_window_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_bytes(b"old\n")
            original_stat = path.stat()
            real_signature = CONFIGURE._metadata_signature
            injected = False

            def rewrite_while_staged_signature_is_read(
                candidate: Path,
            ) -> tuple[object, ...]:
                nonlocal injected
                if (
                    candidate != path
                    and candidate.name.endswith("-new")
                    and path.stat().st_nlink == 2
                    and not injected
                ):
                    path.write_bytes(b"usr\n")
                    os.utime(
                        path,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                    injected = True
                return real_signature(candidate)

            with mock.patch.object(
                CONFIGURE,
                "_metadata_signature",
                side_effect=rewrite_while_staged_signature_is_read,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "content or metadata changed",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")],
                        transaction_root=root,
                    )

            self.assertTrue(injected)
            self.assertEqual(path.read_text(encoding="utf-8"), "usr\n")
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_staged_content_change_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_bytes(b"old\n")
            real_replace = CONFIGURE._replace_staged_atomically
            injected = False

            def mutate_staged_then_replace(*args: object) -> None:
                nonlocal injected
                staged = Path(args[0])
                if not injected:
                    before = staged.stat(follow_symlinks=False)
                    staged.write_text("bad\n", encoding="utf-8", newline="")
                    os.utime(
                        staged,
                        ns=(before.st_atime_ns, before.st_mtime_ns),
                    )
                    injected = True
                real_replace(*args)

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=mutate_staged_then_replace,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "metadata changed before publication",
                ) as raised:
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")],
                        transaction_root=root,
                    )

            self.assertTrue(injected)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertNotIn("bad", str(raised.exception))

    @unittest.skipUnless(os.name == "nt", "requires Windows security descriptors")
    def test_windows_existing_file_update_preserves_security_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            # Keep the fixture byte-exact on Windows; the production reader uses
            # newline="" so CRLF translation must not mask the intended branch.
            path.write_bytes(b"old\n")
            icacls = shutil.which("icacls")
            self.assertIsNotNone(icacls)
            protected = subprocess.run(
                [icacls, str(path), "/inheritance:d"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(protected.returncode, 0, protected.stderr)
            before = CONFIGURE._windows_security_signature(path)
            self.assertIsNotNone(before)
            inherited = root / "inherited.toml"
            inherited.write_bytes(b"fixture\n")
            self.assertNotEqual(
                CONFIGURE._windows_security_signature(inherited), before
            )
            CONFIGURE.apply_changes_transactionally(
                [(path, "old\n", "new\n")],
                transaction_root=root,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(CONFIGURE._windows_security_signature(path), before)
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_windows_named_security_information_preserves_acl_protection(self) -> None:
        base = 0x00000001 | 0x00000002 | 0x00000004
        self.assertEqual(
            CONFIGURE._windows_named_security_information(0, has_label=False),
            base | 0x20000000,
        )
        self.assertEqual(
            CONFIGURE._windows_named_security_information(0x1000, has_label=False),
            base | 0x80000000,
        )
        self.assertEqual(
            CONFIGURE._windows_named_security_information(0x3000, has_label=True),
            base | 0x00000010 | 0x80000000 | 0x40000000,
        )

    def test_windows_sddl_mismatch_summary_withholds_acl_contents(self) -> None:
        expected = (
            'O:S-1-5-21-111G:S-1-5-21-222D:AI'
            '(XA;ID;FA;;;S-1-5-21-333;(@USER.Dept=="D:SECRET"))'
        )
        actual = (
            'O:S-1-5-21-111G:S-1-5-21-222D:ARAI'
            '(XA;ID;FA;;;S-1-5-21-444;(@USER.Dept=="S:PRIVATE"))'
        )

        summary = CONFIGURE._windows_sddl_mismatch_summary(expected, actual)

        self.assertIn("owner_equal=True", summary)
        self.assertIn("group_equal=True", summary)
        self.assertIn("dacl_equal=False", summary)
        self.assertIn("expected_dacl_flags=AI", summary)
        self.assertIn("actual_dacl_flags=ARAI", summary)
        self.assertIn("expected_dacl_aces=1", summary)
        self.assertIn("actual_dacl_aces=1", summary)
        self.assertNotIn("S-1-5-21", summary)
        self.assertNotIn("SECRET", summary)
        self.assertNotIn("PRIVATE", summary)
        self.assertNotIn("@USER", summary)

        malformed = CONFIGURE._windows_sddl_mismatch_summary("D:SECRET", "D:AI")
        self.assertIn("expected_dacl_flags=unrecognized", malformed)
        self.assertNotIn("SECRET", malformed)

    def test_existing_read_only_file_update_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "read-only.toml"
            path.write_bytes(b"old\n")
            path.chmod(0o400)
            expected_mode = path.stat().st_mode & 0o777
            try:
                CONFIGURE.apply_changes_transactionally(
                    [(path, "old\n", "new\n")], transaction_root=root
                )
                self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
                self.assertEqual(path.stat().st_mode & 0o777, expected_mode)
                self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())
            finally:
                path.chmod(0o600)

    @unittest.skipUnless(os.name == "nt", "requires Windows security descriptors")
    def test_windows_inherited_dacl_survives_existing_file_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inherited_root = root / "inherited"
            inherited_root.mkdir()
            icacls = shutil.which("icacls")
            self.assertIsNotNone(icacls)
            prepared = subprocess.run(
                [
                    icacls,
                    str(inherited_root),
                    "/inheritance:r",
                    "/grant:r",
                    f"{getpass.getuser()}:(OI)(CI)F",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            path = inherited_root / "agent.toml"
            path.write_bytes(b"old\n")
            before = CONFIGURE._windows_security_signature(path)
            self.assertIsNotNone(before)
            assert before is not None
            self.assertIn("D:AI", before)
            self.assertIn("ID", before)

            CONFIGURE.apply_changes_transactionally(
                [(path, "old\n", "new\n")],
                transaction_root=inherited_root,
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual(CONFIGURE._windows_security_signature(path), before)
            self.assertFalse(
                (inherited_root / CONFIGURE.TRANSACTION_JOURNAL).exists()
            )

    @unittest.skipUnless(os.name == "nt", "requires Windows security descriptors")
    def test_windows_security_descriptor_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            path.write_bytes(b"old\n")
            with mock.patch.object(
                CONFIGURE,
                "_set_windows_security_descriptor",
                side_effect=CONFIGURE.ConfigurationError("injected descriptor failure"),
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError, "injected descriptor failure"
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "new\n")], transaction_root=root
                    )
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_concurrent_configurator_is_rejected_by_directory_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            ready_read, ready_write = os.pipe()
            release_read, release_write = os.pipe()
            child = os.fork()
            if child == 0:  # pragma: no cover - assertions run in parent
                os.close(ready_read)
                os.close(release_write)
                with CONFIGURE._transaction_directory_lock(root):
                    os.write(ready_write, b"1")
                    os.read(release_read, 1)
                os._exit(0)

            os.close(ready_write)
            os.close(release_read)
            try:
                self.assertEqual(os.read(ready_read, 1), b"1")
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "transaction is active",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "", "new\n")],
                        transaction_root=root,
                    )
            finally:
                os.write(release_write, b"1")
                os.close(release_write)
                os.close(ready_read)
                _, status = os.waitpid(child, 0)
                self.assertEqual(os.WEXITSTATUS(status), 0)

    def test_delete_rollback_preserves_original_inode_and_xattrs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deleted = root / "delete.toml"
            failing = root / "failing.toml"
            deleted.write_bytes(b"delete-old\n")
            failing.write_bytes(b"failing-old\n")
            deleted.chmod(0o640)
            before = deleted.stat()
            xattr_name = "user.codex_orchestration_delete_rollback"
            xattr_supported = False
            if hasattr(os, "setxattr"):
                try:
                    os.setxattr(deleted, xattr_name, b"preserve")
                    xattr_supported = True
                except OSError:
                    pass

            real_replace = CONFIGURE._replace_staged_atomically
            injected = False

            def fail_second_commit_once(
                staged: Path,
                destination: Path,
                tombstone: Path,
                staged_identity: tuple[int, int],
                original_identity: tuple[int, int],
                expected_metadata: tuple[object, ...],
                expected_content_sha256: str,
                replacement_content_sha256: str,
            ) -> None:
                nonlocal injected
                if destination == failing and not injected:
                    injected = True
                    raise OSError("injected commit failure")
                real_replace(
                    staged,
                    destination,
                    tombstone,
                    staged_identity,
                    original_identity,
                    expected_metadata,
                    expected_content_sha256,
                    replacement_content_sha256,
                )

            with mock.patch.object(
                CONFIGURE,
                "_replace_staged_atomically",
                side_effect=fail_second_commit_once,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "committed files were restored",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [
                            (deleted, "delete-old\n", ""),
                            (failing, "failing-old\n", "failing-new\n"),
                        ]
                    )

            after = deleted.stat()
            self.assertTrue(injected)
            self.assertEqual(deleted.read_text(encoding="utf-8"), "delete-old\n")
            self.assertEqual(
                (after.st_dev, after.st_ino),
                (before.st_dev, before.st_ino),
            )
            self.assertEqual(after.st_mode & 0o777, before.st_mode & 0o777)
            if xattr_supported:
                self.assertEqual(os.getxattr(deleted, xattr_name), b"preserve")

    def test_atomic_deletion_rejects_a_late_external_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "agent.toml"
            outside = root / "outside.toml"
            path.write_bytes(b"old\n")
            real_sha256_file = CONFIGURE._sha256_file
            path_hash_reads = 0
            injected = False

            def link_after_final_source_hash(candidate: Path) -> str | None:
                nonlocal path_hash_reads, injected
                result = real_sha256_file(candidate)
                if candidate == path:
                    path_hash_reads += 1
                    if path_hash_reads == 2:
                        os.link(path, outside)
                        injected = True
                return result

            with mock.patch.object(
                CONFIGURE,
                "_sha256_file",
                side_effect=link_after_final_source_hash,
            ):
                with self.assertRaisesRegex(
                    CONFIGURE.ConfigurationError,
                    "rollback was incomplete",
                ):
                    CONFIGURE.apply_changes_transactionally(
                        [(path, "old\n", "")],
                        transaction_root=root,
                    )

            self.assertTrue(injected)
            self.assertEqual(outside.read_text(encoding="utf-8"), "old\n")
            self.assertTrue((root / CONFIGURE.TRANSACTION_JOURNAL).exists())

    def test_malformed_existing_config_fails_before_agent_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, executor, _ = self.paths(root)
            config.parent.mkdir(parents=True)
            original = 'model = "unterminated\n'
            config.write_text(original, encoding="utf-8")

            result, _, stderr = self.run_main(root, "--apply")
            self.assertEqual(result, 2)
            self.assertIn("not valid TOML", stderr)
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertFalse(executor.exists())

    def test_non_utf8_managed_file_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, executor, _ = self.paths(root)
            config.parent.mkdir(parents=True)
            config.write_bytes(b"\xff\xfe")

            result, _, stderr = self.run_main(root, "--apply")

            self.assertEqual(result, 2)
            self.assertIn("not valid UTF-8", stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertFalse(executor.exists())

    def test_malformed_catalog_effort_fails_without_type_error(self) -> None:
        malformed_catalog = {
            "executor-test": {
                "slug": "executor-test",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [{"effort": []}],
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            result, _, stderr = self.run_main(
                Path(temporary),
                "--executor-effort",
                "high",
                catalog=malformed_catalog,
            )

            self.assertEqual(result, 2)
            self.assertIn("catalog efforts: none", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_explicit_empty_cli_values_are_rejected(self) -> None:
        cases = (
            (("--executor-model", ""), "--executor-model"),
            (("--executor-effort", ""), "--executor-effort"),
            (("--executor-provider", ""), "--executor-provider"),
            (("--advisor-model", ""), "--advisor-model"),
            (("--advisor-effort", ""), "--advisor-effort"),
            (("--advisor-provider", ""), "--advisor-provider"),
        )
        for arguments, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                result, _, stderr = self.run_main(Path(temporary), *arguments)
                self.assertEqual(result, 2)
                self.assertIn(label, stderr)
                self.assertIn("non-empty", stderr)

    def test_retired_orchestrator_flags_are_rejected(self) -> None:
        stderr = StringIO()
        argv = [
            str(SCRIPT_PATH),
            "--executor-model",
            "executor-test",
            "--orchestrator-model",
            "wrong",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            CONFIGURE.parse_args()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
