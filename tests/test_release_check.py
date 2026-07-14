from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "release_check.py"
SPEC = importlib.util.spec_from_file_location("release_check", SCRIPT)
assert SPEC and SPEC.loader
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleaseCheckTests(unittest.TestCase):
    def test_checkout_release_metadata_is_consistent(self) -> None:
        self.assertEqual(RELEASE.run_check(REPO_ROOT, require_tag=False), "0.5.1")

    def test_unreleased_checkout_is_not_tag_ready(self) -> None:
        with self.assertRaisesRegex(RELEASE.ReleaseCheckError, "not tagged"):
            RELEASE.run_check(REPO_ROOT, require_tag=True)


if __name__ == "__main__":
    unittest.main()
