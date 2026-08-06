#!/usr/bin/env python3
"""Fail CI if any included product violates the ultra-quality evidence gate."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/ultra-quality-report.json"
KC = re.compile(r"^[A-Z]{1,3}\d[A-Z0-9-]{5,}[A-Z0-9]$", re.I)
GENERIC = [
    "기존 조사 결과 유지",
    "공식 근거 확인 완료",
    "KC 인증 확인",
    "원본 Master DB",
    "원본 압축 DB 복원",
]


def present(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"확인 중", "-", "미확인", "unknown"})


def normalize(text: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def validate(product: dict) -> list[str]:
    errors: list[str] = []
    number = str(product.get("kcNumber", "")).strip()
    safety = str(product.get("safetyKoreaUrl", "")).strip()
    urls = [str(value).strip() for value in product.get("evidenceUrls", []) if str(value).strip()]
    reason = str(product.get("reason", ""))

    if not KC.fullmatch(number):
        errors.append("valid_kc_number")
    if product.get("kcStatus") != "적합":
        errors.append("kc_status_fit")
    if "safetykorea.kr" not in safety or "/release/certDetail" not in safety:
        errors.append("safety_detail_url")
    for field in ["kcDate", "kcType", "kcAuthority", "kcItem", "kcModel", "manufacturer"]:
        if not present(product.get(field)):
            errors.append(field)
    if "대한민국" not in str(product.get("origin", "")) and "한국" not in str(product.get("origin", "")):
        errors.append("korean_origin")
    if not present(product.get("age")) or not present(product.get("ageBasis")):
        errors.append("age_evidence")
    if not present(product.get("saleStatus")):
        errors.append("active_sale_status")
    if not present(product.get("checkedAt")):
        errors.append("checked_at")
    if len(urls) < 2:
        errors.append("two_evidence_urls")
    if not any("safetykorea.kr" in url for url in urls):
        errors.append("safety_evidence")
    sale_urls = [url for url in urls if "safetykorea.kr" not in url]
    if not sale_urls:
        errors.append("sales_evidence")
    elif any(not urlparse(url).scheme.startswith("http") for url in sale_urls):
        errors.append("valid_sales_url")
    if number and number not in reason:
        errors.append("specific_reason_with_kc")
    if any(value in reason for value in GENERIC):
        errors.append("generic_reason")
    return sorted(set(errors))


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    included = [item for item in products if item.get("status") == "포함"]
    violations = [
        {"id": item.get("id"), "brand": item.get("brand"), "name": item.get("name"), "errors": validate(item)}
        for item in included
        if validate(item)
    ]

    ids = [str(item.get("id", "")).strip() for item in products]
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if key and count > 1)

    product_keys = [
        normalize(item.get("brand")) + "|" + normalize(item.get("name"))
        for item in products
    ]
    duplicate_products = sorted(
        key for key, count in Counter(product_keys).items() if key != "|" and count > 1
    )

    report = {
        "checkedAt": date.today().isoformat(),
        "qualityMode": "ultra",
        "totalProducts": len(products),
        "includedCount": len(included),
        "includedViolationCount": len(violations),
        "duplicateIdCount": len(duplicate_ids),
        "duplicateProductKeyCount": len(duplicate_products),
        "violations": violations,
        "duplicateIds": duplicate_ids,
        "duplicateProductKeys": duplicate_products,
        "passed": not violations and not duplicate_ids and not duplicate_products,
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
