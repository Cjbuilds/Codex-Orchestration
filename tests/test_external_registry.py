from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    REPO_ROOT
    / "plugins"
    / "codex-orchestration"
    / "skills"
    / "codex-orchestration"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import external_registry as REGISTRY  # noqa: E402


DIGEST = "a" * 64


def genuine_registry(home: Path) -> dict[str, object]:
    value = REGISTRY.empty_registry(home)
    value["providers"] = {
        "openrouter": {
            "adapter": "openrouter",
            "adapter_version": 1,
            "lane": "native",
            "endpoint": "https://openrouter.ai/api/v1",
            "endpoint_sha256": DIGEST,
            "auth_kind": "secure_store",
            "state": "ROLE_STAGED",
            "qualified": False,
            "capability_checked_at": None,
            "capability_source": None,
            "owned_config_keys": [
                "model_providers.openrouter.name",
                "model_providers.openrouter.base_url",
            ],
            "config_snapshot_sha256": None,
        }
    }
    value["roles"] = {
        "kimi_researcher": {
            "purpose": "Review a bounded research packet.",
            "provider": "openrouter",
            "model": "moonshotai/kimi-k3",
            "default_effort": "medium",
            "supported_efforts": ["medium"],
            "effort_source": "bundled-openrouter-template",
            "agent_name": "codex_orchestration_kimi_researcher_abcd1234",
            "agent_file": str(home / "agents" / "kimi.toml"),
            "agent_sha256": DIGEST,
            "effort_agents": {
                "medium": {
                    "name": "codex_orchestration_kimi_researcher_abcd1234",
                    "file": str(home / "agents" / "kimi.toml"),
                    "sha256": DIGEST,
                }
            },
            "state": "ROLE_STAGED",
        }
    }
    value["cli_trust"] = {}
    return value


class ExternalRegistryTests(unittest.TestCase):
    def test_genuine_registry_is_accepted_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            value = genuine_registry(Path(raw))
            self.assertIs(REGISTRY.validate_registry(value), value)
            encoded = REGISTRY.canonical_bytes(value)
            self.assertTrue(encoded.endswith(b"\n"))
            self.assertEqual(json.loads(encoded), value)

    def test_unknown_secret_capable_and_future_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            baseline = genuine_registry(Path(raw))
            mutations = [
                lambda value: value.update(api_key="do-not-store"),
                lambda value: value.update(future=True),
                lambda value: value["providers"]["openrouter"].update(
                    bearer="do-not-store"
                ),
                lambda value: value["roles"]["kimi_researcher"].update(
                    prompt="private chat"
                ),
            ]
            for mutate in mutations:
                value = deepcopy(baseline)
                mutate(value)
                with self.assertRaises(REGISTRY.RegistryError):
                    REGISTRY.validate_registry(value)

    def test_exact_integer_schema_and_state_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            baseline = genuine_registry(Path(raw))
            for schema in (True, 1.0, "1", 0, 2):
                value = deepcopy(baseline)
                value["schema"] = schema
                with self.assertRaises(REGISTRY.RegistryError):
                    REGISTRY.validate_registry(value)
            value = deepcopy(baseline)
            value["roles"]["kimi_researcher"]["state"] = "FUTURE"
            with self.assertRaises(ValueError):
                REGISTRY.validate_registry(value)

    def test_role_provider_effort_and_agent_name_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            baseline = genuine_registry(Path(raw))
            mutations = [
                lambda role: role.update(provider="missing"),
                lambda role: role.update(default_effort="max"),
                lambda role: role.update(supported_efforts=["medium", "medium"]),
                lambda role: role.update(agent_name="Bad-Agent"),
                lambda role: role.update(agent_sha256="short"),
            ]
            for mutate in mutations:
                value = deepcopy(baseline)
                mutate(value["roles"]["kimi_researcher"])
                with self.assertRaises(REGISTRY.RegistryError):
                    REGISTRY.validate_registry(value)

    def test_atomic_write_requires_compare_digest_and_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / REGISTRY.REGISTRY_FILENAME
            first = genuine_registry(root)
            digest = REGISTRY.write_registry(path, first)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded, loaded_digest = REGISTRY.read_registry(path)
            self.assertEqual(loaded, first)
            self.assertEqual(loaded_digest, digest)

            with self.assertRaisesRegex(REGISTRY.RegistryError, "expected digest"):
                REGISTRY.write_registry(path, first)
            changed = deepcopy(first)
            changed["providers"]["openrouter"]["state"] = "RESTART_REQUIRED"
            REGISTRY.write_registry(path, changed, expected_sha256=digest)
            self.assertEqual(REGISTRY.read_registry(path)[0], changed)

    def test_stale_digest_corrupt_json_symlink_and_hardlink_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "registry.json"
            value = genuine_registry(root)
            REGISTRY.write_registry(path, value)
            with self.assertRaisesRegex(REGISTRY.RegistryError, "changed"):
                REGISTRY.write_registry(path, value, expected_sha256="b" * 64)

            path.write_text("{", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(REGISTRY.RegistryError, "valid UTF-8 JSON"):
                REGISTRY.read_registry(path)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            symlink = root / "registry"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(REGISTRY.RegistryError, "unsafe"):
                REGISTRY.read_registry(symlink)

        if hasattr(os, "link"):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path = root / "registry"
                REGISTRY.write_registry(path, genuine_registry(root))
                os.link(path, root / "second-link")
                with self.assertRaisesRegex(REGISTRY.RegistryError, "hard linked"):
                    REGISTRY.read_registry(path)


if __name__ == "__main__":
    unittest.main()
