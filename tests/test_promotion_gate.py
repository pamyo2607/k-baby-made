from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / "scripts/promote_verified_candidates.py"
VERIFY = ROOT / "scripts/verify_promoted_candidates.py"
CAMPAIGN = "20260816-1200-new50"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class PromotionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.canonical = self.data / "master-products.json"
        self.staging = self.data / "discovered-candidate-staging.json"
        self.ledger = self.data / "new-product-promotion-ledger.json"
        self.checkpoint = self.data / "campaign-new50.json"
        self.tombstones = self.data / "deleted-duplicate-tombstones.json"
        self.audit = self.data / "wave-001-audit.json"
        audit_document = {
            "campaignId": CAMPAIGN,
            "waveIndex": 1,
            "promotionReadyCandidateIds": ["DISC-TEST-0001"],
        }
        write_json(self.audit, audit_document)
        audit_sha = hashlib.sha256(self.audit.read_bytes()).hexdigest()
        candidate = {
            "id": "DISC-TEST-0001",
            "category": "이유식·식기",
            "subtype": "유아식기",
            "brand": "테스트브랜드",
            "name": "테스트 실리콘 흡착식판",
            "officialModel": "TEST-PLATE-001",
            "status": "보류",
            "countryOfManufacture": "대한민국",
            "saleStatus": "국내 공식몰에서 현재 주문 가능",
            "ageRange": "6개월 이상",
            "ageEvidence": "공식 제품 설명의 권장 사용연령 6개월 이상",
            "manufacturer": "테스트제조 주식회사",
            "importer": "해당 없음",
            "regulatoryRegime": "식품위생법 · 식품용 기구 및 용기·포장",
            "regulatoryNote": "식품 접촉 제품 기준 적용",
            "kcApplicable": "어린이제품 KC 비대상 · 식품 접촉 제품",
            "kcNumber": "",
            "certifications": [],
            "certStatusSummary": "KC 비대상",
            "certificationEvidenceLevel": "not-applicable",
            "officialUrls": ["https://official.example/products/test-plate-001"],
            "saleUrls": ["https://official.example/products/test-plate-001"],
            "reason": "공식몰에서 제조국, 연령, 제조사, 현재 판매를 확인했다.",
            "checkedAt": "2026-08-16",
            "revalidationMissingFields": [],
            "promotionGate": {
                "schemaVersion": 1,
                "promotionReady": True,
                "evidenceComplete": True,
                "stageDecision": "included",
                "dedupeDecision": "new",
                "campaignId": CAMPAIGN,
                "waveIndex": 1,
                "auditRef": "data/wave-001-audit.json",
                "auditSha256": audit_sha,
            },
        }
        write_json(self.canonical, [])
        write_json(self.staging, [candidate])
        write_json(self.checkpoint, {
            "campaignId": CAMPAIGN,
            "targetCanonicalPromotions": 50,
            "promotedIncludedCanonicalIds": [],
            "remainingToTarget": 50,
            "waveIndex": 0,
            "deletedExistingDuplicates": 0,
        })
        write_json(self.tombstones, {"records": []})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def promote_command(self, *extra: str) -> list[str]:
        return [
            sys.executable,
            str(PROMOTE),
            "--root", str(self.root),
            "--canonical", str(self.canonical),
            "--staging", str(self.staging),
            "--ledger", str(self.ledger),
            "--checkpoint", str(self.checkpoint),
            "--tombstones", str(self.tombstones),
            "--campaign-id", CAMPAIGN,
            "--wave-index", "1",
            "--promoted-at", "2026-08-16T12:00:00+00:00",
            *extra,
        ]

    def snapshot(self) -> dict[Path, bytes | None]:
        paths = (self.canonical, self.staging, self.ledger, self.checkpoint)
        return {path: path.read_bytes() if path.exists() else None for path in paths}

    def test_dry_run_reports_stable_id_without_writing(self) -> None:
        before = self.snapshot()
        result = subprocess.run(
            self.promote_command(), check=False, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"mode": "dry-run"', result.stdout)
        self.assertIn("KBM-20260816-0001", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_apply_verify_and_explicit_rerun_are_idempotent(self) -> None:
        applied = subprocess.run(
            self.promote_command("--apply"), check=False, capture_output=True, text=True
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        products = json.loads(self.canonical.read_text(encoding="utf-8"))
        staged = json.loads(self.staging.read_text(encoding="utf-8"))
        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in products], ["KBM-20260816-0001"])
        self.assertEqual(staged, [])
        self.assertEqual(
            ledger["promotions"][0]["candidateId"], "DISC-TEST-0001"
        )
        verify = subprocess.run([
            sys.executable,
            str(VERIFY),
            "--root", str(self.root),
            "--canonical", str(self.canonical),
            "--staging", str(self.staging),
            "--ledger", str(self.ledger),
            "--checkpoint", str(self.checkpoint),
            "--tombstones", str(self.tombstones),
            "--campaign-id", CAMPAIGN,
        ], check=False, capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn('"status": "passed"', verify.stdout)
        after_first_apply = self.snapshot()
        rerun = subprocess.run(
            self.promote_command(
                "--apply", "--candidate-id", "DISC-TEST-0001"
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertIn('"status": "no-op"', rerun.stdout)
        self.assertEqual(self.snapshot(), after_first_apply)

    def test_incomplete_explicit_gate_fails_without_any_write(self) -> None:
        candidates = json.loads(self.staging.read_text(encoding="utf-8"))
        candidates[0]["promotionGate"]["evidenceComplete"] = False
        write_json(self.staging, candidates)
        before = self.snapshot()
        result = subprocess.run(
            self.promote_command("--apply"), check=False, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidenceComplete", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_product_url_fails_without_any_write(self) -> None:
        existing = {
            "sequence": 1,
            "id": "EXISTING-001",
            "brand": "다른브랜드",
            "name": "다른 제품",
            "officialUrls": ["https://official.example/products/test-plate-001"],
        }
        write_json(self.canonical, [existing])
        before = self.snapshot()
        result = subprocess.run(
            self.promote_command("--apply"), check=False, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate url", result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
