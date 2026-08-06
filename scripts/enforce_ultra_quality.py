#!/usr/bin/env python3
"""Downgrade included rows that do not meet the ultra-quality evidence gate."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/ultra-quality-enforcement.json"
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


def issues(product: dict) -> list[str]:
    found: list[str] = []
    number = str(product.get("kcNumber", "")).strip()
    safety = str(product.get("safetyKoreaUrl", "")).strip()
    urls = [str(value).strip() for value in product.get("evidenceUrls", []) if str(value).strip()]
    reason = str(product.get("reason", ""))

    if not KC.fullmatch(number):
        found.append("valid_kc_number")
    if product.get("kcStatus") != "적합":
        found.append("kc_status_fit")
    if "safetykorea.kr" not in safety or "/release/certDetail" not in safety:
        found.append("safety_detail_url")
    for field in ["kcDate", "kcType", "kcAuthority", "kcItem", "kcModel", "manufacturer"]:
        if not present(product.get(field)):
            found.append(field)
    if "대한민국" not in str(product.get("origin", "")) and "한국" not in str(product.get("origin", "")):
        found.append("korean_origin")
    if not present(product.get("age")) or not present(product.get("ageBasis")):
        found.append("age_evidence")
    if not present(product.get("saleStatus")):
        found.append("active_sale_status")
    if not present(product.get("checkedAt")):
        found.append("checked_at")
    if len(urls) < 2:
        found.append("two_evidence_urls")
    if not any("safetykorea.kr" in url for url in urls):
        found.append("safety_evidence")
    sale_urls = [url for url in urls if "safetykorea.kr" not in url]
    if not sale_urls:
        found.append("sales_evidence")
    elif any(not urlparse(url).scheme.startswith("http") for url in sale_urls):
        found.append("valid_sales_url")
    if number and number not in reason:
        found.append("specific_reason_with_kc")
    if any(value in reason for value in GENERIC):
        found.append("generic_reason")
    return sorted(set(found))


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    downgraded = []
    for product in products:
        if product.get("status") != "포함":
            continue
        missing = issues(product)
        if not missing:
            continue
        previous_reason = str(product.get("reason", "")).strip()
        product["status"] = "보류"
        product["researchStatus"] = "최극상 증거 게이트 재조사 필요"
        product["checkedAt"] = date.today().isoformat()
        product["reason"] = (
            f"{date.today().isoformat()} 최극상 포함 조건 미충족 항목 "
            f"{', '.join(missing)} 때문에 보류로 전환했다. 기존 근거: {previous_reason}"
        )
        history = str(product.get("history", "")).strip()
        product["history"] = (
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
