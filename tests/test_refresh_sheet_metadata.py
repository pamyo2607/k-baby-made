from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFRESH = ROOT / "scripts/refresh_sheet_metadata.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class RefreshSheetMetadataTests(unittest.TestCase):
    def test_refresh_appends_promotions_and_updates_report_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            write_json(data / "master-products.json", [
                {"id": "OLD-001", "status": "보류"},
                {"id": "NEW-001", "status": "포함"},
            ])
            write_json(data / "sheet-sync-order.json", {
                "version": 1,
                "existingRows": 1,
                "appendedRows": 0,
                "totalRows": 1,
                "ids": ["OLD-001"],
            })
            write_json(data / "sheet-recovery-quarantine.json", {
                "status": "quarantined",
                "records": 1,
                "products": [{"id": "SHEET-ONLY-001"}],
            })
            write_json(data / "bootstrap-report.json", {
                "addedFromSheet": 0,
                "quarantinedFromSheet": 1,
                "productsAfter": 1,
                "included": 0,
                "pending": 1,
                "excluded": 0,
            })

            result = subprocess.run([
                sys.executable,
                str(REFRESH),
                "--root", str(root),
            ], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

            order = json.loads(
                (data / "sheet-sync-order.json").read_text(encoding="utf-8")
            )
            report = json.loads(
                (data / "bootstrap-report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(order["ids"], ["OLD-001", "NEW-001"])
            self.assertEqual(order["totalRows"], 2)
            self.assertEqual(order["appendedRows"], 1)
            self.assertEqual(report["productsAfter"], 2)
            self.assertEqual(report["quarantinedFromSheet"], 1)
            self.assertEqual(report["included"], 1)
            self.assertEqual(report["pending"], 1)
            self.assertEqual(report["excluded"], 0)


if __name__ == "__main__":
    unittest.main()
