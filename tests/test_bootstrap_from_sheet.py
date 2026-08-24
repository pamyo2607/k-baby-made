from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_from_sheet import reconcile_rows  # noqa: E402


class BootstrapFromSheetTests(unittest.TestCase):
    def test_sheet_only_rows_are_quarantined_without_canonical_insertion(self) -> None:
        existing = [
            {
                "id": "MASTER-0001",
                "brand": "기존브랜드",
                "name": "기존제품",
                "sequence": 1,
                "status": "보류",
            }
        ]
        recovered = [
            {
                "id": "MASTER-0001",
                "brand": "Sheet변경브랜드",
                "name": "Sheet변경제품",
                "sequence": 1,
                "status": "포함",
            },
            {
                "id": "KBM-20260816-0001",
                "brand": "신규브랜드",
                "name": "신규제품",
                "sequence": 2,
                "status": "보류",
            },
        ]

        products, quarantined, stats = reconcile_rows(existing, recovered, {})

        self.assertEqual(products, existing)
        self.assertEqual(
            [item["id"] for item in quarantined], ["KBM-20260816-0001"]
        )
        self.assertEqual(stats["matchedAndBackfilled"], 1)
        self.assertEqual(stats["duplicateSourceRows"], 0)
        self.assertEqual(stats["tombstonedSourceRows"], 0)

    def test_tombstoned_and_duplicate_source_rows_do_not_enter_quarantine(self) -> None:
        recovered = [
            {"id": "DELETED-001", "brand": "브랜드", "name": "삭제제품"},
            {"id": "NEW-001", "brand": "브랜드", "name": "신규제품"},
            {"id": "NEW-001", "brand": "브랜드", "name": "신규제품"},
        ]
        tombstones = {
            "records": [
                {
                    "deletedId": "DELETED-001",
                    "retainedId": "MASTER-0001",
                    "brand": "브랜드",
                    "name": "삭제제품",
                    "brandAliases": [],
                }
            ]
        }

        products, quarantined, stats = reconcile_rows([], recovered, tombstones)

        self.assertEqual(products, [])
        self.assertEqual([item["id"] for item in quarantined], ["NEW-001"])
        self.assertEqual(stats["duplicateSourceRows"], 1)
        self.assertEqual(stats["tombstonedSourceRows"], 1)


if __name__ == "__main__":
    unittest.main()
