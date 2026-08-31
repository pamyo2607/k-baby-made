from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from initialize_top30_campaign import CATEGORIES, reconcile
from naver_product_discovery_paced import build_candidate
from revalidate_staged_candidates import candidate_source_priority, process


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Top30CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        write_json(self.data / "master-products.json", [])
        write_json(self.data / "discovered-candidate-staging.json", [{
            "id": "DISC-TOP30-0001",
            "category": "이유식·식기",
            "brand": "테스트브랜드",
            "name": "테스트 실리콘 흡착식판",
            "status": "보류",
            "officialUrls": ["https://official.example/products/top30-plate"],
            "saleUrls": ["https://official.example/products/top30-plate"],
            "revalidationMissingFields": ["officialEvidence"],
        }])
        write_json(self.data / "campaign-new50.json", {
            "campaignId": "old-new50",
            "targetCanonicalPromotions": 50,
            "promotedIncludedCanonicalIds": [],
            "remainingToTarget": 50,
            "waveIndex": 0,
            "deletedExistingDuplicates": 0,
        })
        write_json(self.data / "new-product-promotion-ledger.json", {
            "schemaVersion": 1,
            "campaignId": "old-new50",
            "promotions": [],
        })
        write_json(self.data / "deleted-duplicate-tombstones.json", {"records": []})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialization_is_cumulative_and_idempotent(self) -> None:
        first = reconcile(self.root, "20260831-top30-per-category", 30)
        second = reconcile(self.root, "20260831-top30-per-category", 30)
        checkpoint = json.loads(
            (self.data / "campaign-new50.json").read_text(encoding="utf-8")
        )
        self.assertEqual(first["targetCanonicalPromotions"], 180)
        self.assertEqual(second["remaining"], 180)
        self.assertEqual(checkpoint["categoryTargets"], {category: 30 for category in CATEGORIES})
        self.assertEqual(checkpoint["categoryRemaining"], {category: 30 for category in CATEGORIES})
        self.assertEqual(checkpoint["promotedIncludedCanonicalIds"], [])
        self.assertEqual(len(json.loads(
            (self.data / "discovered-candidate-staging.json").read_text(encoding="utf-8")
        )), 1)

    def test_exact_retail_candidate_is_prioritized_over_aggregator(self) -> None:
        aggregator = {
            "id": "DISC-AGGREGATOR",
            "name": "아기 치발기",
            "officialUrls": ["https://itemscout.io/register/sourcing/1688/product/123"],
        }
        retail = {
            "id": "DISC-RETAIL",
            "name": "국민 아기 치발기 누적 판매 1위",
            "saleStatus": "현재 구매 가능",
            "ageRange": "3개월 이상",
            "officialUrls": ["https://brand.naver.com/example/products/456"],
        }
        self.assertLess(candidate_source_priority(retail), candidate_source_priority(aggregator))

    def test_discovery_candidate_persists_naver_rank_basis(self) -> None:
        candidate = build_candidate(
            "턱받이",
            "국산 아기 턱받이 베스트",
            "https://brand.naver.com/example/products/789",
            "아기 턱받이 국내생산",
            2,
        )
        self.assertEqual(candidate["discoveryResultRank"], 2)
        self.assertIn("수요 프록시", candidate["rankEvidence"])

    def test_strict_ready_candidate_gets_immutable_gate_and_promotes(self) -> None:
        campaign = "20260831-top30-per-category"
        reconcile(self.root, campaign, 30)

        def validator(candidate: dict) -> tuple[dict, dict]:
            candidate.update({
                "status": "포함",
                "countryOfManufacture": "대한민국 🇰🇷",
                "saleStatus": "국내 공식몰에서 현재 주문 가능",
                "ageRange": "6개월 이상",
                "ageEvidence": "공식 제품 설명의 6개월 이상",
                "officialModel": "TOP30-PLATE-001",
                "manufacturer": "테스트제조 주식회사",
                "regulatoryRegime": "식품위생법 · 식품용 기구 및 용기·포장",
                "regulatoryNote": "식품 접촉 제품 기준 적용",
                "kcApplicable": "어린이제품 KC 비대상 · 식품 접촉 제품",
                "kcNumber": "",
                "certifications": [],
                "certificationEvidenceLevel": "not-applicable",
                "certStatusSummary": "KC 비대상",
                "reason": "공식몰에서 제조국과 월령과 제조사와 현재 판매를 확인했다.",
                "checkedAt": "2026-08-31",
                "revalidationMissingFields": [],
                "revalidationResolved": True,
            })
            return candidate, {
                "id": candidate["id"],
                "checked": True,
                "changed": True,
                "errors": [],
                "evidencePagesTried": candidate["officialUrls"],
            }

        result = process(
            self.root,
            validator,
            now=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
            workers=1,
        )
        self.assertEqual(result["promotionReadyCandidateIds"], ["DISC-TOP30-0001"])
        staging = json.loads(
            (self.data / "discovered-candidate-staging.json").read_text(encoding="utf-8")
        )
        gate = staging[0]["promotionGate"]
        audit = self.root / gate["auditRef"]
        self.assertTrue(audit.exists())
        self.assertEqual(gate["auditSha256"], __import__("hashlib").sha256(audit.read_bytes()).hexdigest())

        promoted = subprocess.run([
            sys.executable,
            str(SCRIPTS / "promote_verified_candidates.py"),
            "--root", str(self.root),
            "--canonical", str(self.data / "master-products.json"),
            "--staging", str(self.data / "discovered-candidate-staging.json"),
            "--ledger", str(self.data / "new-product-promotion-ledger.json"),
            "--checkpoint", str(self.data / "campaign-new50.json"),
            "--tombstones", str(self.data / "deleted-duplicate-tombstones.json"),
            "--campaign-id", campaign,
            "--wave-index", "1",
            "--apply",
        ], check=False, capture_output=True, text=True)
        self.assertEqual(promoted.returncode, 0, promoted.stderr)
        canonical = json.loads(
            (self.data / "master-products.json").read_text(encoding="utf-8")
        )
        checkpoint = json.loads(
            (self.data / "campaign-new50.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(canonical), 1)
        self.assertEqual(checkpoint["categoryPromotedCounts"]["이유식·식기"], 1)
        self.assertEqual(checkpoint["categoryRemaining"]["이유식·식기"], 29)
        self.assertEqual(checkpoint["remainingToTarget"], 179)


if __name__ == "__main__":
    unittest.main()
