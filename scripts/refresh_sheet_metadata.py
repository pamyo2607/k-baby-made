#!/usr/bin/env python3
"""Refresh Sheet-derived metadata after canonical status or promotion changes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def refresh(root: Path) -> dict:
    data = root / "data"
    canonical_path = data / "master-products.json"
    order_path = data / "sheet-sync-order.json"
    report_path = data / "bootstrap-report.json"
    quarantine_path = data / "sheet-recovery-quarantine.json"

    products = json.loads(canonical_path.read_text(encoding="utf-8"))
    if not isinstance(products, list):
        raise SystemExit("canonical products must be an array")
    canonical_ids = [str(item.get("id", "")).strip() for item in products]
    if not all(canonical_ids) or len(canonical_ids) != len(set(canonical_ids)):
        raise SystemExit("canonical IDs must be non-empty and unique")

    order = json.loads(order_path.read_text(encoding="utf-8"))
    previous_ids = [
        str(value).strip()
        for value in order.get("ids", [])
        if str(value).strip()
    ]
    canonical_set = set(canonical_ids)
    ordered_ids = [value for value in previous_ids if value in canonical_set]
    seen = set(ordered_ids)
    ordered_ids.extend(value for value in canonical_ids if value not in seen)
    if len(ordered_ids) != len(canonical_ids) or set(ordered_ids) != canonical_set:
        raise SystemExit("failed to refresh Sheet sync order")

    try:
        existing_rows = int(order.get("existingRows", 0) or 0)
    except (TypeError, ValueError):
        existing_rows = 0
    existing_rows = min(max(existing_rows, 0), len(products))
    order.update({
        "strategy": "기존 동기화 순서를 보존하고 검증 승격 제품을 canonical 순서로 누적",
        "existingRows": existing_rows,
        "appendedRows": len(products) - existing_rows,
        "totalRows": len(products),
        "ids": ordered_ids,
        "status": "deduplicated-and-promotion-synced",
    })
    write_json(order_path, order)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
    quarantine_products = quarantine.get("products", [])
    if not isinstance(quarantine_products, list):
        raise SystemExit("Sheet recovery quarantine products must be an array")
    report.update({
        "addedFromSheet": 0,
        "quarantinedFromSheet": len(quarantine_products),
        "productsAfter": len(products),
        "included": sum(item.get("status") == "포함" for item in products),
        "pending": sum(item.get("status") == "보류" for item in products),
        "excluded": sum(item.get("status") == "제외" for item in products),
    })
    write_json(report_path, report)

    result = {
        "status": "refreshed",
        "canonicalCount": len(products),
        "sheetOrderCount": len(ordered_ids),
        "quarantinedFromSheet": len(quarantine_products),
        "included": report["included"],
        "pending": report["pending"],
        "excluded": report["excluded"],
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    refresh(args.root.resolve())


if __name__ == "__main__":
    main()
