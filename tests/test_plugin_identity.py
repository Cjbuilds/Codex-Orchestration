from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "plugins" / "codex-orchestration" / "skills" / "codex-orchestration" / "scripts" / "plugin_identity.py"
SPEC = importlib.util.spec_from_file_location("plugin_identity", MODULE_PATH)
assert SPEC and SPEC.loader
IDENTITY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IDENTITY
SPEC.loader.exec_module(IDENTITY)


class IdentityFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = self.root / ("codex.exe" if os.name == "nt" else "codex")
        shutil.copyfile(sys.executable, self.client)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        (self.plugin / "plugin.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
        (self.plugin / "skills").mkdir()
        (self.plugin / "skills" / "SKILL.md").write_text("identity", encoding="utf-8")
        self.codex_home = self.root / "codex-home"
        self.cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "market"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, self.cache)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, *, plugin_id="codex-orchestration@market", name="codex-orchestration", marketplace="market", root=None, version="1.0.0", enabled=True, installed=True, source_type="local", marketplace_type="git", marketplace_source="https://example.invalid/repo"):
        return {
            "pluginId": plugin_id,
            "name": name,
            "marketplaceName": marketplace,
            "version": version,
            "installed": installed,
            "enabled": enabled,
            "source": {"source": source_type, "path": str(root or self.plugin)},
            "marketplaceSource": {"sourceType": marketplace_type, "source": marketplace_source},
        }

    def guard(self, selector="setup", inventories=None, saved=None):
        values = inventories or [[self.record()], [self.record()]]
        values = [
            value
            if isinstance(value, dict)
            else {"installed": value, "available": []}
            for value in values
        ]
        patcher = mock.patch.object(IDENTITY, "run_inventory", side_effect=values)
        patcher.start()
        self.addCleanup(patcher.stop)
        return IDENTITY.PluginIdentityGuard(
            self.client,
            self.cache,
            selector,
            saved_plugin_id=saved,
            codex_home=self.codex_home,
        )


class DigestTests(IdentityFixture):
    def canonical(self, raw):
        records, packages = IDENTITY._inventory({"installed": raw, "available": []})
        try:
            return records, IDENTITY._digest(records)
        finally:
            for package in packages.values():
                package.close()

    def test_inventory_order_does_not_affect_digest(self):
        second = self.root / "second"
        shutil.copytree(self.plugin, second)
        a = self.record()
        b = self.record(plugin_id="codex-orchestration@other", marketplace="other", root=second)
        self.assertEqual(self.canonical([a, b])[1], self.canonical([b, a])[1])

    def test_inventory_identity_changes_alter_full_digest(self):
        baseline = self.canonical([self.record()])[1]
        variants = [
            [],
            [self.record(enabled=False)],
            [self.record(version="1.0.1")],
            [self.record(marketplace_source="https://example.invalid/changed")],
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(baseline, self.canonical(variant)[1])
        (self.plugin / "skills" / "SKILL.md").write_text("changed", encoding="utf-8")
        self.assertNotEqual(baseline, self.canonical([self.record()])[1])
        extra = self.root / "extra"
        shutil.copytree(self.plugin, extra)
        added = self.record(plugin_id="codex-orchestration@extra", marketplace="extra", root=extra)
        self.assertNotEqual(baseline, self.canonical([self.record(), added])[1])

    def test_unrelated_valid_installed_plugin_is_not_opened_digested_or_selected(self):
        unrelated = self.record(
            plugin_id="different-plugin@other",
            name="different-plugin",
            marketplace="other",
            root=self.root / "missing-unrelated-source",
            source_type="unsupported-unrelated-source",
        )
        records, packages = IDENTITY._inventory(
            {"installed": [self.record(), unrelated], "available": []}
        )
        try:
            self.assertEqual(len(records), 1)
            self.assertEqual(set(packages), {"codex-orchestration@market"})
            self.assertEqual(
                IDENTITY._digest(records), self.canonical([self.record()])[1]
            )
            selected = IDENTITY._select(
                records, "setup", self.cache, None, self.codex_home
            )
            self.assertEqual(selected["plugin_id"], "codex-orchestration@market")
        finally:
            for package in packages.values():
                package.close()

    def test_operation_digest_has_independent_inputs(self):
        selected = {"plugin_id": "codex-orchestration@market", "version": "1"}
        client = {"path": "codex", "sha256": "a"}
        base = {"namespace": "codex-orchestration", "selector": "setup", "client": client, "selected": selected}
        digest = IDENTITY._digest(base)
        for key, value in (("namespace", "other"), ("selector", "repair")):
            changed = dict(base)
            changed[key] = value
            self.assertNotEqual(digest, IDENTITY._digest(changed))
        changed_client = dict(base)
        changed_client["client"] = {**client, "sha256": "b"}
        changed_selected = dict(base)
        changed_selected["selected"] = {**selected, "version": "2"}
        self.assertNotEqual(digest, IDENTITY._digest(changed_client))
        self.assertNotEqual(digest, IDENTITY._digest(changed_selected))


class SelectionTests(IdentityFixture):
    def select(self, records, selector, saved=None, executing=None, codex_home=None):
        canonical, packages = IDENTITY._inventory({"installed": records, "available": []})
        try:
            return IDENTITY._select(
                canonical,
                selector,
                Path(executing or self.cache),
                saved,
                self.codex_home if codex_home is None else codex_home,
            )
        finally:
            for package in packages.values():
                package.close()

    def test_enabled_executing_success(self):
        self.assertEqual(self.select([self.record()], "setup")["plugin_id"], "codex-orchestration@market")

    def test_alternate_marketplace_maps_to_exact_cache(self):
        alternate_cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "alternate"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, alternate_cache)
        alternate = self.record(
            plugin_id="codex-orchestration@alternate",
            marketplace="alternate",
        )
        selected = self.select(
            [self.record(), alternate], "setup", executing=alternate_cache
        )
        self.assertEqual(selected["plugin_id"], "codex-orchestration@alternate")

    def test_status_selects_saved_namespace_owner_and_exact_executing_identity(self):
        alternate_source = self.root / "alternate-source"
        shutil.copytree(self.plugin, alternate_source)
        alternate_cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "alternate"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, alternate_cache)
        saved = self.record(enabled=False)
        executing = self.record(
            plugin_id="codex-orchestration@alternate",
            marketplace="alternate",
            root=alternate_source,
        )
        canonical, packages = IDENTITY._inventory(
            {"installed": [saved, executing], "available": []}
        )
        try:
            selected, active = IDENTITY._select_identities(
                canonical,
                "status",
                alternate_cache,
                "codex-orchestration@market",
                self.codex_home,
            )
            self.assertEqual(selected["plugin_id"], "codex-orchestration@market")
            self.assertEqual(active["plugin_id"], "codex-orchestration@alternate")

            selected, active = IDENTITY._select_identities(
                canonical, "status", alternate_cache, None, self.codex_home
            )
            self.assertEqual(selected["plugin_id"], active["plugin_id"])
            self.assertEqual(active["plugin_id"], "codex-orchestration@alternate")
        finally:
            for package in packages.values():
                package.close()

    def test_setup_requires_explicit_home_and_exact_versioned_path(self):
        canonical, packages = IDENTITY._inventory(
            {"installed": [self.record()], "available": []}
        )
        try:
            with self.assertRaises(IDENTITY.SelectionError):
                IDENTITY._select(canonical, "setup", self.cache, None, None)
            with self.assertRaises(IDENTITY.SelectionError):
                IDENTITY._select(
                    canonical, "setup", self.cache.parent / "2.0.0", None, self.codex_home
                )
        finally:
            for package in packages.values():
                package.close()

    def test_malformed_version_cache_components_fail(self):
        for version in ("../1.0.0", "1/2", "1\\2", "bad:version", ".", "-bad"):
            with self.subTest(version=version), self.assertRaises(
                IDENTITY.InventoryFormatError
            ):
                self.select([self.record(version=version)], "setup")

    def test_enabled_executing_missing_disabled_and_source_mismatch_fail(self):
        with self.assertRaises(IDENTITY.SelectionError):
            self.select([], "setup", executing=self.cache)
        with self.assertRaises(IDENTITY.SelectionError):
            self.select([self.record(enabled=False)], "setup")
        with self.assertRaises(IDENTITY.SelectionError):
            self.select([self.record()], "setup", executing=self.root / "wrong")

    def test_single_uninstalled_target_is_not_source_opened_and_is_missing(self):
        uninstalled = self.record(
            installed=False,
            root=self.root / "missing-uninstalled-source",
            source_type="unsupported-uninstalled-source",
        )
        records, packages = IDENTITY._inventory(
            {"installed": [uninstalled], "available": []}
        )
        self.assertEqual(records, [])
        self.assertEqual(packages, {})
        with self.assertRaisesRegex(
            IDENTITY.SelectionError,
            "^Enabled executing plugin identity is missing or ambiguous\\.$",
        ):
            IDENTITY._select(
                records, "setup", self.cache, None, self.codex_home
            )

    def test_enabled_executing_ambiguity_fails(self):
        canonical, packages = IDENTITY._inventory(
            {"installed": [self.record()], "available": []}
        )
        try:
            with self.assertRaises(IDENTITY.SelectionError):
                IDENTITY._select(
                    [canonical[0], dict(canonical[0])],
                    "setup",
                    self.cache,
                    None,
                    self.codex_home,
                )
        finally:
            for package in packages.values():
                package.close()

    def test_repair_saved_identity_must_match(self):
        self.assertEqual(self.select([self.record()], "repair", "codex-orchestration@market")["plugin_id"], "codex-orchestration@market")
        with self.assertRaises(IDENTITY.SelectionError):
            self.select([self.record()], "repair", "codex-orchestration@other")

    def test_saved_and_legacy_disable_resolve_disabled(self):
        saved = self.record(enabled=False)
        legacy = self.record(plugin_id=IDENTITY.LEGACY_PLUGIN_ID, marketplace="codex-orchestration", enabled=False)
        legacy_cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "codex-orchestration"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, legacy_cache)
        self.assertEqual(self.select([saved], "disable-schema7", saved="codex-orchestration@market")["plugin_id"], "codex-orchestration@market")
        self.assertEqual(self.select([legacy], "disable-legacy", executing=legacy_cache)["plugin_id"], IDENTITY.LEGACY_PLUGIN_ID)

    def test_disable_rejects_alternate_executing_identity_for_saved_and_legacy_state(self):
        alternate_cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "alternate"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, alternate_cache)
        alternate = self.record(
            plugin_id="codex-orchestration@alternate",
            marketplace="alternate",
        )
        legacy = self.record(
            plugin_id=IDENTITY.LEGACY_PLUGIN_ID,
            marketplace="codex-orchestration",
            enabled=False,
        )
        for selector, saved in (
            ("disable-schema7", "codex-orchestration@market"),
            ("disable-legacy", None),
        ):
            with self.subTest(selector=selector), self.assertRaisesRegex(
                IDENTITY.SelectionError,
                "Executing plugin identity does not match the saved disable target",
            ):
                self.select(
                    [self.record(enabled=False), legacy, alternate],
                    selector,
                    saved=saved,
                    executing=alternate_cache,
                )

    def test_disable_missing_and_duplicate_fail(self):
        with self.assertRaises(IDENTITY.SelectionError):
            self.select([], "disable-schema7", saved="codex-orchestration@market")
        with self.assertRaises(IDENTITY.InventoryFormatError):
            self.select([self.record(), self.record()], "disable-schema7", saved="codex-orchestration@market")


class FormatAndCommandTests(IdentityFixture):
    def test_duplicate_target_ids_fail_before_state_filtering_or_source_opening(self):
        states = ((True, True), (True, False), (False, True), (False, False))
        for first_state in states:
            for second_state in states:
                with self.subTest(first=first_state, second=second_state):
                    records = [
                        self.record(
                            installed=installed,
                            enabled=enabled,
                            root=self.root / "missing-duplicate-source",
                            source_type="unsupported-duplicate-source",
                        )
                        for installed, enabled in (first_state, second_state)
                    ]
                    with self.assertRaisesRegex(
                        IDENTITY.InventoryFormatError,
                        "^Plugin inventory contains a duplicate plugin ID\\.$",
                    ):
                        IDENTITY._inventory(
                            {"installed": records, "available": []}
                        )

    def test_malformed_shapes_and_fields_fail(self):
        bad_values = [None, {}, {"plugins": {}}, [1], {"installed": [1], "available": []}, {"installed": [{"installed": True, "name": 1}], "available": []}]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(IDENTITY.InventoryFormatError):
                IDENTITY._inventory(value)
        malformed = self.record()
        malformed["pluginId"] = "codex-orchestration@a@b"
        with self.assertRaises(IDENTITY.InventoryFormatError):
                IDENTITY._inventory({"installed": [malformed], "available": []})
        malformed = self.record()
        malformed["enabled"] = 1
        with self.assertRaises(IDENTITY.InventoryFormatError):
                IDENTITY._inventory({"installed": [malformed], "available": []})
        for plugin_id in (
            "codex-orchestration@bad marketplace",
            "codex-orchestration@bad/control",
            "codex-orchestration@" + ("m" * 129),
        ):
            with self.subTest(plugin_id=plugin_id):
                malformed = self.record(plugin_id=plugin_id, marketplace=plugin_id.split("@", 1)[1])
                with self.assertRaises(IDENTITY.InventoryFormatError):
                    IDENTITY._inventory({"installed": [malformed], "available": []})
        with self.assertRaises(IDENTITY.InventoryFormatError):
            IDENTITY._inventory(
                {
                    "installed": [self.record(source_type="cache")],
                    "available": [],
                }
            )

    def _popen(self, stdout_body=b"", stderr_body=b"", returncode=0, timeout=False):
        class Process:
            def __init__(self, argv, **kwargs):
                self.argv = argv
                self.kwargs = kwargs
                kwargs["stdout"].write(stdout_body)
                kwargs["stderr"].write(stderr_body)
                kwargs["stdout"].flush()
                kwargs["stderr"].flush()
            def wait(self, timeout=None):
                if timeout and Process.should_timeout:
                    Process.should_timeout = False
                    raise subprocess.TimeoutExpired(self.argv, timeout)
                return returncode
            def kill(self):
                pass
        Process.should_timeout = timeout
        return Process

    def test_nonzero_sanitizes_raw_output(self):
        raw = "SECRET-RAW-BODY"
        with mock.patch.object(IDENTITY.subprocess, "Popen", self._popen(stderr_body=raw.encode(), returncode=7)):
            with self.assertRaises(IDENTITY.InventoryCommandError) as caught:
                IDENTITY.run_inventory(self.client)
        self.assertNotIn(raw, str(caught.exception))

    def test_timeout_and_output_bound_fail_closed(self):
        with mock.patch.object(IDENTITY.subprocess, "Popen", self._popen(timeout=True)):
            with self.assertRaises(IDENTITY.InventoryCommandError):
                IDENTITY.run_inventory(self.client, timeout=0.01)
        with mock.patch.object(IDENTITY, "MAX_OUTPUT_BYTES", 4), mock.patch.object(IDENTITY.subprocess, "Popen", self._popen(stdout_body=b"12345")):
            with self.assertRaises(IDENTITY.InventoryCommandError):
                IDENTITY.run_inventory(self.client)

    def test_malformed_json_is_sanitized(self):
        raw = b"not-json-secret"
        with mock.patch.object(IDENTITY.subprocess, "Popen", self._popen(stdout_body=raw)):
            with self.assertRaises(IDENTITY.InventoryFormatError) as caught:
                IDENTITY.run_inventory(self.client)
        self.assertNotIn(raw.decode(), str(caught.exception))

    def test_inventory_command_is_argv_shell_false_and_inherits_environment(self):
        process = self._popen(stdout_body=b"[]")
        calls = []
        def inspect(argv, **kwargs):
            calls.append((argv, kwargs))
            return process(argv, **kwargs)
        with mock.patch.object(IDENTITY.subprocess, "Popen", inspect):
            self.assertEqual(IDENTITY.run_inventory(self.client), [])
        argv, kwargs = calls[0]
        self.assertEqual(argv, [str(self.client), "plugin", "list", "--json"])
        self.assertIs(kwargs["shell"], False)
        self.assertNotIn("env", kwargs)

        calls.clear()
        home = self.root / "selected-home"
        with mock.patch.dict(os.environ, {"IDENTITY_SENTINEL": "preserved"}):
            with mock.patch.object(IDENTITY.subprocess, "Popen", inspect):
                self.assertEqual(
                    IDENTITY.run_inventory(self.client, codex_home=home), []
                )
        _, kwargs = calls[0]
        self.assertEqual(kwargs["env"]["CODEX_HOME"], str(home))
        self.assertEqual(kwargs["env"]["IDENTITY_SENTINEL"], "preserved")

    def test_guard_rejects_relative_codex_binary(self):
        with self.assertRaises(IDENTITY.InventoryCommandError):
            IDENTITY.PluginIdentityGuard("codex", self.plugin, "setup")

    def test_symlink_and_hardlink_payloads_fail_closed(self):
        original = self.plugin / "skills" / "SKILL.md"
        hardlink = self.plugin / "skills" / "hardlink.md"
        os.link(original, hardlink)
        with self.assertRaises(IDENTITY.PackageIdentityError):
            IDENTITY._Package(self.plugin)
        hardlink.unlink()
        target = self.root / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        linked = self.plugin / "skills" / "linked.txt"
        try:
            linked.symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation is unavailable")
        with self.assertRaises(IDENTITY.PackageIdentityError):
            IDENTITY._Package(self.plugin)


class GuardTests(IdentityFixture):
    def test_status_guard_exposes_saved_and_executing_identities(self):
        alternate_source = self.root / "alternate-source"
        shutil.copytree(self.plugin, alternate_source)
        alternate_cache = (
            self.codex_home
            / "plugins"
            / "cache"
            / "alternate"
            / "codex-orchestration"
            / "1.0.0"
        )
        shutil.copytree(self.plugin, alternate_cache)
        records = [
            self.record(enabled=False),
            self.record(
                plugin_id="codex-orchestration@alternate",
                marketplace="alternate",
                root=alternate_source,
            ),
        ]
        inventory = {"installed": records, "available": []}
        with mock.patch.object(
            IDENTITY, "run_inventory", side_effect=[inventory, inventory, inventory]
        ):
            with IDENTITY.PluginIdentityGuard(
                self.client,
                alternate_cache,
                "status",
                saved_plugin_id="codex-orchestration@market",
                codex_home=self.codex_home,
            ) as guard:
                self.assertEqual(
                    guard.selected_plugin_id, "codex-orchestration@market"
                )
                self.assertEqual(
                    guard.executing_plugin_id, "codex-orchestration@alternate"
                )
                guard.assert_unchanged("status publication")

    def test_schema_seven_disable_guard_accepts_disabled_saved_identity(self):
        inventory = {
            "installed": [self.record(enabled=False)],
            "available": [],
        }
        with mock.patch.object(
            IDENTITY, "run_inventory", side_effect=[inventory, inventory, inventory]
        ):
            with IDENTITY.PluginIdentityGuard(
                self.client,
                self.cache,
                "disable-schema7",
                saved_plugin_id="codex-orchestration@market",
                codex_home=self.codex_home,
            ) as guard:
                self.assertEqual(
                    guard.selected_plugin_id, "codex-orchestration@market"
                )
                self.assertEqual(guard.executing_plugin_id, guard.selected_plugin_id)
                guard.assert_unchanged("disable publication")

    def test_colliding_plugin_id_with_contradictory_name_is_rejected(self):
        contradictory = self.record(name="different-plugin")
        inventory = {
            "installed": [self.record(), contradictory],
            "available": [],
        }
        with mock.patch.object(IDENTITY, "run_inventory", return_value=inventory):
            with self.assertRaisesRegex(
                IDENTITY.InventoryFormatError,
                "^Plugin inventory identity fields are inconsistent\\.$",
            ):
                with IDENTITY.PluginIdentityGuard(
                    self.client,
                    self.cache,
                    "setup",
                    codex_home=self.codex_home,
                ):
                    pass

    def test_different_payload_at_exact_cache_coordinate_is_rejected_before_entry(self):
        (self.cache / "skills" / "SKILL.md").write_text(
            "stale cache payload", encoding="utf-8"
        )
        inventory = {"installed": [self.record()], "available": []}
        with mock.patch.object(
            IDENTITY, "run_inventory", side_effect=[inventory, inventory]
        ) as run_inventory:
            with self.assertRaisesRegex(
                IDENTITY.SelectionError,
                "^Executing plugin cache payload does not match the selected source payload\\.$",
            ):
                with IDENTITY.PluginIdentityGuard(
                    self.client,
                    self.cache,
                    "setup",
                    codex_home=self.codex_home,
                ):
                    pass
        self.assertEqual(run_inventory.call_count, 1)

    def test_identical_source_and_executing_payloads_are_accepted(self):
        with self.guard() as guard:
            self.assertNotEqual(
                guard._package.root_identity,
                guard._executing_package.root_identity,
            )
            self.assertEqual(
                guard._package.content_fingerprint(),
                guard._executing_package.content_fingerprint(),
            )

    def test_sourceless_bytecode_outside_pycache_rejects_payload_equivalence(self):
        for relative in (Path("cache_only.pyc"), Path("skills/cache_only.pyo")):
            with self.subTest(relative=relative):
                payload = self.cache / relative
                payload.write_bytes(b"cache-only bytecode")
                inventory = {"installed": [self.record()], "available": []}
                with mock.patch.object(
                    IDENTITY, "run_inventory", side_effect=[inventory]
                ):
                    with self.assertRaisesRegex(
                        IDENTITY.SelectionError,
                        "^Executing plugin cache payload does not match the selected source payload\\.$",
                    ):
                        with IDENTITY.PluginIdentityGuard(
                            self.client,
                            self.cache,
                            "setup",
                            codex_home=self.codex_home,
                        ):
                            pass
                payload.unlink()

    def test_pycache_directories_and_descendants_remain_excluded(self):
        bytecode = self.cache / "skills" / "__pycache__"
        bytecode.mkdir()
        (bytecode / "plugin_identity.cpython-313.pyc").write_bytes(b"runtime metadata")
        nested = bytecode / "nested"
        nested.mkdir()
        (nested / "ignored.pyo").write_bytes(b"runtime metadata")
        with self.guard() as guard:
            self.assertIsNotNone(guard._bytecode_isolation)
            self.assertIsNotNone(guard._bytecode_isolation.cache_root)
            self.assertEqual(
                Path(sys.pycache_prefix or ""),
                guard._bytecode_isolation.cache_root,
            )
            self.assertFalse(
                guard._bytecode_isolation.cache_root.is_relative_to(self.cache)
            )
            self.assertEqual(
                guard._package.content_fingerprint(),
                guard._executing_package.content_fingerprint(),
            )

    def test_pycache_bytecode_drift_is_caught_without_cross_installation_matching(self):
        bytecode = self.cache / "skills" / "__pycache__"
        bytecode.mkdir()
        payload = bytecode / "routing_state.cpython-313.pyc"
        payload.write_bytes(b"runtime metadata")
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            if os.name != "nt":
                payload.write_bytes(b"runtime metadata changed")
                with self.assertRaises(IDENTITY.IdentityDriftError):
                    guard.assert_unchanged("bytecode mutation")
            else:
                retained = next(
                    retained
                    for relative, retained in guard._executing_package.ignored_items
                    if relative.endswith("routing_state.cpython-313.pyc")
                )
                original = retained.snapshot

                def drifted(*, include_hash):
                    value = original(include_hash=include_hash)
                    if include_hash:
                        value["sha256"] = "f" * 64
                    return value

                with mock.patch.object(retained, "snapshot", side_effect=drifted):
                    with self.assertRaises(IDENTITY.IdentityDriftError):
                        guard.assert_unchanged("bytecode mutation")

    def test_capture_and_recheck(self):
        with self.guard(inventories=[[self.record()], [self.record()], [self.record()]]) as guard:
            self.assertEqual(guard.selected_plugin_id, "codex-orchestration@market")
            self.assertEqual(len(guard.full_inventory_sha256), 64)
            self.assertEqual(len(guard.operation_identity_sha256), 64)
            guard.assert_unchanged("mutation")

    def test_two_phase_capture_catches_list_to_handle_drift(self):
        with self.assertRaises(IDENTITY.IdentityDriftError):
            with self.guard(inventories=[[self.record()], [self.record(version="2.0.0")]]):
                pass

    def test_cache_identity_changes_only_operation_digest(self):
        with self.guard() as first:
            full_digest = first.full_inventory_sha256
            operation_digest = first.operation_identity_sha256
        old_cache = self.cache.with_name("1.0.0-old")
        self.cache.rename(old_cache)
        shutil.copytree(self.plugin, self.cache)
        with self.guard() as second:
            self.assertEqual(second.full_inventory_sha256, full_digest)
            self.assertNotEqual(second.operation_identity_sha256, operation_digest)

    def test_package_file_drift_caught(self):
        with self.guard(inventories=[[self.record()], [self.record()], [self.record()]]) as guard:
            if os.name != "nt":
                (self.plugin / "skills" / "SKILL.md").write_text("drift", encoding="utf-8")
                with self.assertRaises(IDENTITY.IdentityDriftError):
                    guard.assert_unchanged("mutation")
            else:
                payload = next(item for relative, item in guard._package.items if relative.endswith("SKILL.md"))
                original = payload.snapshot
                def drifted(*, include_hash):
                    value = original(include_hash=include_hash)
                    if include_hash:
                        value["sha256"] = "0" * 64
                    return value
                with mock.patch.object(payload, "snapshot", side_effect=drifted):
                    with self.assertRaises(IDENTITY.IdentityDriftError):
                        guard.assert_unchanged("mutation")

    def test_executing_cache_file_drift_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            if os.name != "nt":
                (self.cache / "skills" / "SKILL.md").write_text(
                    "cache drift", encoding="utf-8"
                )
                with self.assertRaises(IDENTITY.IdentityDriftError):
                    guard.assert_unchanged("mutation")
            else:
                payload = next(
                    retained
                    for relative, retained in guard._executing_package.items
                    if relative.endswith("SKILL.md")
                )
                original = payload.snapshot

                def drifted(*, include_hash):
                    value = original(include_hash=include_hash)
                    if include_hash:
                        value["sha256"] = "e" * 64
                    return value

                with mock.patch.object(payload, "snapshot", side_effect=drifted):
                    with self.assertRaises(IDENTITY.IdentityDriftError):
                        guard.assert_unchanged("mutation")

    @unittest.skipUnless(os.name == "posix", "POSIX same-name replacement semantics")
    def test_posix_same_name_file_replacement_is_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            target = self.plugin / "skills" / "SKILL.md"
            replacement = target.with_name("replacement.md")
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
            with self.assertRaisesRegex(
                IDENTITY.IdentityDriftError, "pathname identity drifted"
            ):
                guard.assert_unchanged("same-name file replacement")

    @unittest.skipUnless(os.name == "posix", "POSIX same-name replacement semantics")
    def test_posix_same_name_nested_directory_replacement_is_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            target = self.plugin / "skills"
            moved = self.root / "skills-original"
            target.rename(moved)
            shutil.copytree(moved, target)
            with self.assertRaisesRegex(
                IDENTITY.IdentityDriftError, "pathname identity drifted"
            ):
                guard.assert_unchanged("same-name directory replacement")

    @unittest.skipUnless(os.name == "posix", "POSIX same-name replacement semantics")
    def test_posix_same_name_package_root_replacement_is_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            moved = self.plugin.with_name("plugin-original")
            self.plugin.rename(moved)
            shutil.copytree(moved, self.plugin)
            with self.assertRaisesRegex(
                IDENTITY.IdentityDriftError, "pathname identity drifted"
            ):
                guard.assert_unchanged("same-name package replacement")

    @unittest.skipUnless(os.name == "posix", "POSIX same-name replacement semantics")
    def test_posix_same_name_client_replacement_is_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            replacement = self.client.with_name("replacement-codex")
            shutil.copyfile(self.client, replacement)
            os.replace(replacement, self.client)
            with self.assertRaisesRegex(
                IDENTITY.IdentityDriftError, "pathname identity drifted"
            ):
                guard.assert_unchanged("same-name client replacement")

    @unittest.skipUnless(os.name == "posix", "POSIX no-follow replacement semantics")
    def test_posix_symlink_replacement_is_not_followed(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            target = self.plugin / "skills" / "SKILL.md"
            moved = target.with_name("original.md")
            target.rename(moved)
            target.symlink_to(moved)
            with self.assertRaisesRegex(
                IDENTITY.IdentityDriftError, "could not be reopened safely"
            ):
                guard.assert_unchanged("symlink replacement")

    def test_setup_rejects_cache_alias(self):
        alias = self.root / "cache-alias"
        try:
            alias.symlink_to(self.cache, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlink creation is unavailable")
        values = [
            {"installed": [self.record()], "available": []},
            {"installed": [self.record()], "available": []},
        ]
        with mock.patch.object(IDENTITY, "run_inventory", side_effect=values):
            with self.assertRaises(IDENTITY.PackageIdentityError):
                with IDENTITY.PluginIdentityGuard(
                    self.client,
                    alias,
                    "setup",
                    codex_home=self.codex_home,
                ):
                    pass

    def test_package_name_addition_or_removal_is_caught(self):
        with self.guard(
            inventories=[[self.record()], [self.record()], [self.record()]]
        ) as guard:
            if os.name != "nt":
                (self.plugin / "skills" / "added.md").write_text(
                    "added", encoding="utf-8"
                )
                with self.assertRaises(IDENTITY.IdentityDriftError):
                    guard.assert_unchanged("publication")
            else:
                current = guard._package._enumerate_names()
                with mock.patch.object(
                    guard._package,
                    "_enumerate_names",
                    return_value=current + ("skills/added.md",),
                ):
                    with self.assertRaises(IDENTITY.IdentityDriftError):
                        guard.assert_unchanged("publication")

    def test_full_inventory_rechecks_unselected_package_through_retained_handles(self):
        sibling = self.root / "sibling"
        shutil.copytree(self.plugin, sibling)
        active = self.record()
        other = self.record(
            plugin_id="codex-orchestration@other",
            marketplace="other",
            root=sibling,
            enabled=False,
        )
        inventories = [[active, other], [active, other], [active, other]]
        with self.guard(inventories=inventories) as guard:
            self.assertEqual(set(guard._packages), {
                "codex-orchestration@market",
                "codex-orchestration@other",
            })
            sibling_package = guard._packages["codex-orchestration@other"]
            if os.name != "nt":
                (sibling / "skills" / "SKILL.md").write_text(
                    "sibling drift", encoding="utf-8"
                )
                with self.assertRaises(IDENTITY.IdentityDriftError):
                    guard.assert_unchanged("mutation")
            else:
                payload = next(
                    retained
                    for relative, retained in sibling_package.items
                    if relative.endswith("SKILL.md")
                )
                original = payload.snapshot

                def drifted(*, include_hash):
                    value = original(include_hash=include_hash)
                    if include_hash:
                        value["sha256"] = "f" * 64
                    return value

                with mock.patch.object(payload, "snapshot", side_effect=drifted):
                    with self.assertRaises(IDENTITY.IdentityDriftError):
                        guard.assert_unchanged("mutation")

    @unittest.skipUnless(os.name == "nt", "Windows handle sharing contract")
    def test_windows_retained_share_read_blocks_write_rename_and_delete(self):
        with self.guard(inventories=[[self.record()], [self.record()]]) as guard:
            payload = self.plugin / "skills" / "SKILL.md"
            with self.assertRaises(PermissionError):
                os.open(payload, os.O_WRONLY)
            with self.assertRaises(PermissionError):
                os.rename(payload, payload.with_suffix(".moved"))
            with self.assertRaises(PermissionError):
                os.unlink(payload)
            self.assertTrue(guard.selected_plugin_id)

    @unittest.skipUnless(os.name == "nt", "Windows retained-handle instrumentation")
    def test_windows_rechecks_use_retained_handle_primitives(self):
        with self.guard(inventories=[[self.record()], [self.record()], [self.record()]]) as guard:
            with mock.patch.object(IDENTITY, "_win_handle_identity", wraps=IDENTITY._win_handle_identity) as file_id, mock.patch.object(IDENTITY.os, "fstat", wraps=os.fstat) as fstat_call, mock.patch.object(IDENTITY.os, "read", wraps=os.read) as read_call, mock.patch.object(IDENTITY._kernel32, "CreateFileW", wraps=IDENTITY._kernel32.CreateFileW) as create_file:
                guard.assert_unchanged("publication")
            self.assertGreater(file_id.call_count, 0)
            self.assertGreater(fstat_call.call_count, 0)
            self.assertGreater(read_call.call_count, 0)
            self.assertEqual(create_file.call_count, 0)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("read_bytes(", source)
        self.assertNotIn("Path.stat(", source)


if __name__ == "__main__":
    unittest.main()
