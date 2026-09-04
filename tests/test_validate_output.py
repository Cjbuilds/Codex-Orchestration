from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "plugins/codex-orchestration/skills/codex-orchestration/scripts/validate_output.py"
SPEC = importlib.util.spec_from_file_location("validate_output", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class ValidateOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.packet = {
            "batch_id": "release",
            "artifacts": {"source": "commit-a", "report": "sha256-b"},
            "criteria": ["correctness", "coverage"],
        }
        self.receipt = {
            "decision": "VALIDATION_APPROVED",
            "batch_id": "release",
            "artifacts": dict(self.packet["artifacts"]),
            "checks": [
                {"criterion": "correctness", "artifacts": ["source"], "status": "pass", "evidence": "Reviewed source diff and reported regression results."},
                {"criterion": "coverage", "artifacts": ["source", "report"], "status": "pass", "evidence": "Report covers the requested outcome and cites the inspected source."},
            ],
            "findings": [],
        }

    def test_complete_receipt_and_revise_are_distinct(self) -> None:
        before = copy.deepcopy((self.packet, self.receipt))
        self.assertEqual(CHECKER.validate_output(self.packet, self.receipt), "VALIDATION_APPROVED")
        self.assertEqual((self.packet, self.receipt), before)
        self.receipt["decision"] = "VALIDATION_REVISE"
        self.receipt["checks"][0]["status"] = "fail"
        self.receipt["findings"] = ["source:12 loses the previous snapshot; preserve it before writing."]
        self.assertEqual(CHECKER.validate_output(self.packet, self.receipt), "VALIDATION_REVISE")

    def test_stale_incomplete_and_contradictory_receipts_fail(self) -> None:
        mutations = {
            "wrong batch": lambda r: r.update(batch_id="other"),
            "stale revision": lambda r: r["artifacts"].update(source="older"),
            "unknown field": lambda r: r.update(model="gpt-6-astra"),
            "missing criterion": lambda r: r["checks"].pop(),
            "duplicate criterion": lambda r: r["checks"].append(copy.deepcopy(r["checks"][0])),
            "unknown criterion": lambda r: r["checks"][0].update(criterion="new"),
            "empty evidence": lambda r: r["checks"][0].update(evidence="  "),
            "missing artifact evidence": lambda r: r["checks"][1].update(artifacts=["source"]),
            "unknown artifact": lambda r: r["checks"][1].update(artifacts=["external"]),
            "duplicate artifact": lambda r: r["checks"][0].update(artifacts=["source", "source"]),
            "approve fail": lambda r: r["checks"][0].update(status="fail"),
            "approve finding": lambda r: r.update(findings=["Fix source:12"]),
            "revise no finding": lambda r: r.update(decision="VALIDATION_REVISE"),
            "wrong signal": lambda r: r.update(decision="PLAN_APPROVED"),
            "nonstring criterion": lambda r: r["checks"][0].update(criterion=[]),
            "nonstring decision": lambda r: r.update(decision={}),
            "check extra field": lambda r: r["checks"][0].update(approved=True),
            "empty findings entry": lambda r: r.update(findings=[""]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                receipt = copy.deepcopy(self.receipt)
                mutate(receipt)
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_output(self.packet, receipt)

    def test_malformed_packets_fail(self) -> None:
        for change in ({"criteria": []}, {"criteria": ["x", "x"]}, {"artifacts": {}}, {"artifacts": {"source": True}}, {"batch_id": ""}, {"extra": 1}):
            with self.subTest(change=change):
                packet = {**self.packet, **change}
                with self.assertRaises(CHECKER.ValidationError):
                    CHECKER.validate_output(packet, self.receipt)

    def test_cli_exit_codes_and_untrusted_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            packet = Path(directory) / "packet.json"
            receipt = Path(directory) / "receipt.json"
            packet.write_text(json.dumps(self.packet))
            command = [sys.executable, str(SCRIPT), "--packet", str(packet), "--receipt", str(receipt)]
            receipt.write_text(json.dumps(self.receipt))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.receipt.update(decision="VALIDATION_REVISE", findings=["Fix source:12 before release."])
            self.receipt["checks"][0]["status"] = "fail"
            receipt.write_text(json.dumps(self.receipt))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            for raw in ('{"decision":"VALIDATION_APPROVED","decision":"VALIDATION_REVISE"}', '{"value":NaN}', 'VALIDATION_APPROVED\n{}', '[', '[' * 10_000 + ']' * 10_000):
                receipt.write_text(raw)
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("VALIDATION_BLOCKED", result.stderr)
            receipt.write_bytes(b" " * (CHECKER.MAX_INPUT_BYTES + 1))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
