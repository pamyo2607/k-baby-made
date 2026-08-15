#!/usr/bin/env python3
"""Fail CI if any included product violates the ultra-quality evidence gate."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from validate_data import included_errors

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/ultra-quality-report.json"
def normalize(text: object) -> str:
    import re
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    included = [item for item in products if item.get("status") == "포함"]
    violations = [
        {"id": item.get("id"), "brand": item.get("brand"), "name": item.get("name"), "errors": included_errors(item)}
        for item in included
        if included_errors(item)
    ]

    ids = [str(item.get("id", "")).strip() for item in products]
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if key and count > 1)

    product_keys = [normalize(item.get("brand")) + "|" + normalize(item.get("name")) for item in products]
    duplicate_products = sorted(
        key for key, count in Counter(product_keys).items() if key != "|" and count > 1
    )
    key_groups: dict[str, list[dict]] = {}
    for product, key in zip(products, product_keys):
        if key != "|":
            key_groups.setdefault(key, []).append(product)
    unresolved_duplicate_products = sorted(
        key
        for key, group in key_groups.items()
        if len(group) > 1
        and len({str(item.get("duplicateOf") or item.get("id")) for item in group}) > 1
    )

    report = {
        "checkedAt": date.today().isoformat(),
        "qualityMode": "ultra",
        "totalProducts": len(products),
        "includedCount": len(included),
        "includedViolationCount": len(violations),
        "duplicateIdCount": len(duplicate_ids),
        "duplicateProductKeyCount": len(duplicate_products),
        "unresolvedDuplicateProductKeyCount": len(unresolved_duplicate_products),
        "violations": violations,
        "duplicateIds": duplicate_ids,
        "duplicateProductKeys": duplicate_products,
        "unresolvedDuplicateProductKeys": unresolved_duplicate_products,
        # Same brand/name text can still be a legitimate distinct model, size,
        # package, or option. Structural duplicate links are validated by the
        # canonical validator; name-key collisions remain a diagnostic only.
        "duplicateProductKeyDiagnosticOnly": True,
        "passed": not violations and not duplicate_ids and not unresolved_duplicate_products,
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
