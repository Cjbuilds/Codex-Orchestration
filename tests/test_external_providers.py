from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
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

import external_providers as PROVIDERS  # noqa: E402


class ExternalProviderTests(unittest.TestCase):
    def test_bundled_openrouter_and_fable_templates_are_strict(self) -> None:
        openrouter = PROVIDERS.load_provider("openrouter")
        fable = PROVIDERS.load_provider("claude-fable")
        self.assertEqual(openrouter["wire_api"], "responses")
        self.assertEqual(openrouter["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(
            openrouter["models"]["moonshotai/kimi-k3"]["context_window"],
            1_048_576,
        )
        self.assertTrue(openrouter["experimental"])
        self.assertFalse(openrouter["qualified"])
        self.assertEqual(fable["lane"], "subscription")
        self.assertEqual(fable["runtime_identity"], "cli_metadata")

    def test_kimi_effort_is_explicit_and_never_clamped(self) -> None:
        provider = PROVIDERS.load_provider("openrouter")
        model = "moonshotai/kimi-k3"
        self.assertEqual(PROVIDERS.resolve_effort(provider, model, "auto"), "medium")
        self.assertEqual(PROVIDERS.resolve_effort(provider, model, "medium"), "medium")
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "unsupported"):
            PROVIDERS.resolve_effort(provider, model, "max")
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "not in provider"):
            PROVIDERS.resolve_effort(provider, "moonshotai/missing", "max")

    def test_experimental_unqualified_provider_fails_closed(self) -> None:
        provider = PROVIDERS.load_provider("openrouter")
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "Gate 0"):
            PROVIDERS.require_qualified(provider)

    def test_unsafe_endpoints_and_template_extensions_are_rejected(self) -> None:
        baseline = PROVIDERS.load_provider("openrouter")
        bad_urls = (
            "http://openrouter.ai/api/v1",
            "https://user:pass@openrouter.ai/api/v1",
            "https://openrouter.ai/api/v1?token=x",
            "https://openrouter.ai/api/v1#fragment",
            "file:///tmp/provider",
        )
        for url in bad_urls:
            value = deepcopy(baseline)
            value["base_url"] = url
            with self.subTest(url=url):
                with self.assertRaises(PROVIDERS.ProviderError):
                    PROVIDERS.validate_provider(value, expected_id="openrouter")

        value = deepcopy(baseline)
        value["auth_command"] = "/tmp/project-helper"
        with self.assertRaises(PROVIDERS.ProviderError):
            PROVIDERS.validate_provider(value, expected_id="openrouter")

    def test_provider_filename_id_and_model_shapes_are_bound(self) -> None:
        baseline = PROVIDERS.load_provider("openrouter")
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "do not match"):
            PROVIDERS.validate_provider(baseline, expected_id="other")

        value = deepcopy(baseline)
        model = value["models"]["moonshotai/kimi-k3"]
        model["supported_efforts"] = ["medium", "medium"]
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "efforts"):
            PROVIDERS.validate_provider(value, expected_id="openrouter")

        value = deepcopy(baseline)
        value["models"]["moonshotai/kimi-k3"][
            "auto_compact_token_limit"
        ] = 1_048_576
        with self.assertRaisesRegex(PROVIDERS.ProviderError, "below"):
            PROVIDERS.validate_provider(value, expected_id="openrouter")

    def test_provider_loader_cannot_escape_bundled_directory(self) -> None:
        for provider_id in ("../outside", "/tmp/outside", "Bad.Provider", ""):
            with self.subTest(provider_id=provider_id):
                with self.assertRaises(PROVIDERS.ProviderError):
                    PROVIDERS.load_provider(provider_id)


if __name__ == "__main__":
    unittest.main()
