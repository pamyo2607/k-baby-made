from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_ultra_naver as runner  # noqa: E402


class SafetyKoreaSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_search = runner.pipeline.rr.ddg_results

    def tearDown(self) -> None:
        runner.pipeline.rr.ddg_results = self.original_search

    def test_accepts_only_official_cert_detail_numbers(self) -> None:
        expected = "CB063R1234-24001"
        runner.pipeline.rr.ddg_results = lambda query: [
            (
                f"제품 인증 {expected}",
                "https://www.safetykorea.kr/release/certDetail"
                f"?certNum={expected}&certUid=123",
            ),
            (
                "비공식 복사본 AB12345-67890",
                "https://example.com/release/certDetail?certNum=AB12345-67890",
            ),
            (
                "공식 검색 목록 ZU12345-67890",
                "https://www.safetykorea.kr/release/itemSearch",
            ),
        ]

        numbers = runner.discover_safety_numbers({
            "brand": "테스트브랜드",
            "name": "테스트브랜드 아기 딸랑이",
        })

        self.assertEqual(numbers, [expected])

    def test_search_failure_is_fail_closed(self) -> None:
        def fail(_query: str):
            raise TimeoutError("search unavailable")

        runner.pipeline.rr.ddg_results = fail
        self.assertEqual(
            runner.discover_safety_numbers({"brand": "브랜드", "name": "제품명"}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
