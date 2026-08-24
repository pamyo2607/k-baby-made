from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_ultra_parallel as runner  # noqa: E402


class CampaignBatchSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_limit = runner.PENDING_LIMIT
        runner.PENDING_LIMIT = 2

    def tearDown(self) -> None:
        runner.PENDING_LIMIT = self.original_limit

    def test_selects_only_unattempted_campaign_ids_without_wraparound(self) -> None:
        candidates = [{"id": value} for value in ["A", "B", "C", "D"]]
        campaign = {
            "targetIdsSnapshot": ["A", "B", "C", "D"],
            "attemptedIds": ["A", "B", "C"],
            "remainingIds": ["D"],
        }

        selected, available, start, next_cursor, mode = runner.select_pending_batch(
            candidates, {"pendingCursorNext": 3}, campaign
        )

        self.assertEqual([item["id"] for item in selected], ["D"])
        self.assertEqual(available, 1)
        self.assertEqual(start, 3)
        self.assertEqual(next_cursor, 4)
        self.assertEqual(mode, "campaign-unattempted")

    def test_accepts_set_partition_when_attempted_ids_are_not_target_prefix(self) -> None:
        candidates = [{"id": value} for value in ["A", "B", "C", "D"]]
        campaign = {
            "targetIdsSnapshot": ["A", "B", "C", "D"],
            "attemptedIds": ["A", "C"],
            "remainingIds": ["B", "D"],
        }

        selected, available, start, next_cursor, mode = runner.select_pending_batch(
            candidates, {"pendingCursorNext": 2}, campaign
        )

        self.assertEqual([item["id"] for item in selected], ["B", "D"])
        self.assertEqual(available, 2)
        self.assertEqual(start, 2)
        self.assertEqual(next_cursor, 4)
        self.assertEqual(mode, "campaign-unattempted")

    def test_covered_campaign_returns_empty_batch_without_retry(self) -> None:
        candidates = [{"id": value} for value in ["A", "B"]]
        campaign = {
            "targetIdsSnapshot": ["A", "B"],
            "attemptedIds": ["B", "A"],
            "remainingIds": [],
        }

        selected, available, start, next_cursor, mode = runner.select_pending_batch(
            candidates, {"pendingCursorNext": 1}, campaign
        )

        self.assertEqual(selected, [])
        self.assertEqual(available, 0)
        self.assertEqual(start, 2)
        self.assertEqual(next_cursor, 2)
        self.assertEqual(mode, "campaign-covered")

    def test_missing_unattempted_campaign_id_fails_closed(self) -> None:
        campaign = {
            "targetIdsSnapshot": ["A", "B"],
            "attemptedIds": ["A"],
            "remainingIds": ["B"],
        }

        with self.assertRaisesRegex(ValueError, "absent from pending candidates"):
            runner.select_pending_batch(
                [{"id": "A"}], {"pendingCursorNext": 1}, campaign
            )

    def test_invalid_campaign_falls_back_to_rotating_retry(self) -> None:
        candidates = [{"id": value} for value in ["A", "B", "C"]]
        invalid_campaign = {
            "targetIdsSnapshot": ["A", "B", "C"],
            "attemptedIds": ["A", "A"],
            "remainingIds": ["B", "C"],
        }

        selected, available, start, next_cursor, mode = runner.select_pending_batch(
            candidates, {"pendingCursorNext": 1}, invalid_campaign
        )

        self.assertEqual([item["id"] for item in selected], ["B", "C"])
        self.assertEqual(available, 3)
        self.assertEqual(start, 1)
        self.assertEqual(next_cursor, 0)
        self.assertEqual(mode, "rotating-retry")


if __name__ == "__main__":
    unittest.main()
