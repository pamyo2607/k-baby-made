from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_from_sheet import fetch_sheet_rows, reconcile_rows  # noqa: E402


class BootstrapFromSheetTests(unittest.TestCase):
    def test_sheet_fetch_retries_after_timeout(self) -> None:
        calls: list[int] = []

        class Response:
            encoding = ""
            text = "ID,브랜드,정확한 제품명\n" + "\n".join(
                f"P-{index},브랜드{index},제품{index}" for index in range(200)
            )

            def raise_for_status(self) -> None:
                return None

        def request_get(_url: str, *, timeout: int, headers: dict) -> Response:
            calls.append(timeout)
            if len(calls) == 1:
                import requests
                raise requests.ReadTimeout("temporary timeout")
            return Response()

        rows, errors = fetch_sheet_rows(
            request_get=request_get,
            sleeper=lambda _value: None,
            attempts=((1, 0), (2, 0)),
        )
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(rows or []), 200)
        self.assertEqual(len(errors), 1)

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
