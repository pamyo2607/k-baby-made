#!/usr/bin/env python3
"""Downgrade included rows that do not meet the ultra-quality evidence gate."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from validate_data import included_errors

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/ultra-quality-enforcement.json"
FAILURE_TO_MISSING = {
    "finished product is not confirmed Korean manufacture": "countryOfManufacture",
    "official 0-35 month age is absent": "officialAge",
    "manufacturer/importer is absent": "manufacturerOrImporter",
    "regulatory regime is absent": "regulatoryRegime",
    "current Korean sale evidence is absent": "currentSale",
    "exact KC number is absent": "exactKcNumber",
    "active Safety Korea same-model detail is absent": "safetyKoreaSameModel",
}


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    downgraded = []
    for product in products:
        if product.get("status") != "포함":
            continue
        missing = included_errors(product)
        if not missing:
            continue
        previous_reason = str(product.get("reason", "")).strip()
        product["status"] = "보류"
        product["revalidationState"] = "최극상 증거 게이트 재조사 필요"
        product["revalidationResolved"] = False
        product["revalidationMissingFields"] = list(dict.fromkeys(
            FAILURE_TO_MISSING.get(value, "officialEvidence") for value in missing
        ))
        product["checkedAt"] = date.today().isoformat()
        product["reason"] = (
            f"{date.today().isoformat()} 최극상 포함 조건 미충족 항목 "
            f"{', '.join(missing)} 때문에 보류로 전환했다. 기존 근거: {previous_reason}"
        )
        history = str(product.get("historySummary", "")).strip()
        product["historySummary"] = (
            f"{history}\n{date.today().isoformat()} 최극상 검증 게이트로 보류 전환"
        ).strip()
        downgraded.append({"id": product.get("id"), "missing": missing})

    SOURCE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(
        json.dumps({
            "checkedAt": date.today().isoformat(),
            "qualityMode": "ultra",
            "downgradedCount": len(downgraded),
            "downgraded": downgraded,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"downgradedCount": len(downgraded)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
