from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/update_ing_revalidation_campaign.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class UpdateIngCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        data = self.root / "data"
        write_json(data / "campaign-ing-revalidation.json", {
            "campaignId": "test-ing",
            "cycleRunId": "test-cycle",
            "targetIdsSnapshot": ["A", "B"],
            "attemptedIds": [],
            "remainingIds": ["A", "B"],
        })
        write_json(data / "master-products.json", [
            {
                "id": "A",
                "status": "보류",
                "reason": "공식 근거 추가 확인 필요",
                "officialUrls": ["https://official.example/product/a"],
                "checkedAt": "2026-08-24",
                "duplicateOf": "",
            },
            {
                "id": "B",
                "status": "제외",
                "reason": "공식 판매 종료를 확인함",
                "officialUrls": ["https://official.example/product/b"],
                "checkedAt": "2026-08-24",
                "duplicateOf": "",
            },
        ])
        write_json(data / "research-last-run.json", {
            "state": {
                "lastRun": "2026-08-24T14:12:03+00:00",
                "pendingSelectedIds": ["A", "B"],
                "pendingCursorNext": 0,
            },
            "statusTransitions": [{"id": "B", "from": "보류", "to": "제외"}],
            "products": [
                {"id": "A", "checked": True, "changed": True, "errors": ["age_basis"]},
                {"id": "B", "checked": True, "changed": True, "errors": []},
            ],
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_records_coverage_without_misreporting_pending_as_resolved(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(
            (self.root / "data/ing-revalidation-proof.json").read_text(encoding="utf-8")
        )
        campaign = json.loads(
            (self.root / "data/campaign-ing-revalidation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(proof["attemptedCount"], 2)
        self.assertEqual(proof["remainingCount"], 0)
        self.assertEqual(proof["resolvedCount"], 1)
        self.assertFalse(proof["resolutionComplete"])
        self.assertTrue(proof["verificationComplete"])
        self.assertEqual(proof["missingDirectEvidenceIds"], [])
        self.assertEqual(proof["duplicateProcessingCount"], 0)
        self.assertEqual(campaign["attemptedIds"], ["A", "B"])
        self.assertEqual(campaign["remainingIds"], [])
        self.assertTrue(campaign["coverageComplete"])
        self.assertFalse(campaign["resolutionComplete"])
        self.assertTrue(campaign["verificationComplete"])
        self.assertTrue(
            (self.root / "data/revalidation-audits/20260824T141203Z.json").exists()
        )

    def test_duplicate_audit_ids_fail_without_writing_proof(self) -> None:
        audit_path = self.root / "data/research-last-run.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["state"]["pendingSelectedIds"] = ["A", "A"]
        audit["products"] = [audit["products"][0], audit["products"][0]]
        write_json(audit_path, audit)

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unique", result.stderr)
        self.assertFalse((self.root / "data/ing-revalidation-proof.json").exists())

    def test_missing_direct_evidence_blocks_verification_completion(self) -> None:
        source_path = self.root / "data/master-products.json"
        products = json.loads(source_path.read_text(encoding="utf-8"))
        products[0]["officialUrls"] = []
        write_json(source_path, products)

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(
            (self.root / "data/ing-revalidation-proof.json").read_text(encoding="utf-8")
        )
        self.assertFalse(proof["verificationComplete"])
        self.assertEqual(proof["missingDirectEvidenceIds"], ["A"])


if __name__ == "__main__":
    unittest.main()
