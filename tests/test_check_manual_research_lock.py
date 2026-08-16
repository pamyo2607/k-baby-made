from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_manual_research_lock.py"
BASE_LOCK = {
    "campaignId": "manual-20260816-001",
    "owner": "pamyo2607",
    "baseSha": "e001d29657273e5cf9ec488ef216cced303c9f50",
    "createdAt": "2026-08-16T00:00:00+09:00",
    "expiresAt": "2099-08-17T00:00:00+09:00",
}


class ManualResearchLockTests(unittest.TestCase):
    def run_gate(self, lock_contents: str | None) -> tuple[subprocess.CompletedProcess[str], str, str]:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            lock_file = temp / "manual-research-lock.json"
            output_file = temp / "github-output.txt"
            summary_file = temp / "github-summary.md"
            if lock_contents is not None:
                lock_file.write_text(lock_contents, encoding="utf-8")
            env = os.environ.copy()
            env["GITHUB_OUTPUT"] = str(output_file)
            env["GITHUB_STEP_SUMMARY"] = str(summary_file)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--lock-file", str(lock_file)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            output = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
            summary = summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""
            return result, output, summary

    def test_absent_lock_opens_gate(self) -> None:
        result, output, summary = self.run_gate(None)
        self.assertEqual(result.returncode, 0)
        self.assertIn("locked=false", output)
        self.assertIn("lock_state=absent", output)
        self.assertIn("OPEN", summary)

    def test_active_lock_blocks_without_failing(self) -> None:
        result, output, summary = self.run_gate(json.dumps(BASE_LOCK))
        self.assertEqual(result.returncode, 0)
        self.assertIn("locked=true", output)
        self.assertIn("lock_state=active", output)
        self.assertIn("BLOCKED — ACTIVE LOCK", summary)

    def test_expired_lock_still_blocks(self) -> None:
        payload = dict(BASE_LOCK)
        payload["createdAt"] = "2020-01-01T00:00:00Z"
        payload["expiresAt"] = "2020-01-02T00:00:00Z"
        result, output, summary = self.run_gate(json.dumps(payload))
        self.assertEqual(result.returncode, 0)
        self.assertIn("locked=true", output)
        self.assertIn("lock_state=expired", output)
        self.assertIn("never cleared automatically", result.stdout)
        self.assertIn("human verifies", summary)

    def test_invalid_json_fails_closed(self) -> None:
        result, output, summary = self.run_gate("{not-json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("locked=true", output)
        self.assertIn("lock_state=malformed", output)
        self.assertIn("BLOCKED — MALFORMED LOCK", summary)

    def test_missing_required_field_fails_closed(self) -> None:
        payload = dict(BASE_LOCK)
        del payload["owner"]
        result, output, _summary = self.run_gate(json.dumps(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("locked=true", output)
        self.assertIn("missing required fields: owner", result.stderr)

    def test_backwards_ttl_fails_closed(self) -> None:
        payload = dict(BASE_LOCK)
        payload["expiresAt"] = payload["createdAt"]
        result, output, _summary = self.run_gate(json.dumps(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("locked=true", output)
        self.assertIn("expiresAt must be later", result.stderr)


if __name__ == "__main__":
    unittest.main()
