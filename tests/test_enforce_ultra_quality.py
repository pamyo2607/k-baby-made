from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from enforce_ultra_quality import normalize_excluded_display


class EnforceUltraQualityTests(unittest.TestCase):
    def test_excluded_display_is_canonicalized_without_touching_reason(self) -> None:
        product = {
            "id": "TEST-EXCLUDED",
            "status": "제외",
            "statusDisplay": "기준 제외 · 중복 후보",
            "strict419Status": "중복 의심",
            "reason": "공식 근거로 대상 범위에서 제외",
        }
        self.assertTrue(normalize_excluded_display(product))
        self.assertEqual(product["statusDisplay"], "기준 제외")
        self.assertEqual(product["strict419Status"], "기준 제외")
        self.assertEqual(product["reason"], "공식 근거로 대상 범위에서 제외")
        self.assertFalse(normalize_excluded_display(product))

    def test_non_excluded_product_is_not_changed(self) -> None:
        product = {"status": "보류", "statusDisplay": "재검증 중"}
        before = dict(product)
        self.assertFalse(normalize_excluded_display(product))
        self.assertEqual(product, before)


if __name__ == "__main__":
    unittest.main()
