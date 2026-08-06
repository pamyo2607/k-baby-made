#!/usr/bin/env python3
"""Reconcile the cumulative database with the public K-Baby Made Google Sheet.

The sheet is a recovery source only. Existing reviewed values remain authoritative.
New sheet rows are appended as pending or excluded and blank fields in existing
rows are backfilled. Included decisions are never promoted from the sheet unless
they match the locally reviewed verified seed.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
SEED = ROOT / "data/verified-seed.json"
REPORT = ROOT / "data/bootstrap-report.json"
SHEET_ID = "1eXWn2qhdL2iX6nDi60Uov7sotgkoM0veieE2CTdBT8I"
MASTER_GID = "344727200"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&gid={MASTER_GID}"


def text(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name, "") or "").strip()
        if value:
            return value
    return ""


def normalize(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def product_key(item: dict) -> tuple[str, str]:
    return normalize(item.get("brand")), normalize(item.get("name"))


def urls(row: dict[str, str]) -> list[str]:
    found: list[str] = []
    for key, value in row.items():
        if "URL" not in key.upper() and "링크" not in key:
            continue
        for item in re.split(r"[\n\s]+", str(value or "")):
            item = item.strip()
            if item.startswith(("https://", "http://")) and item not in found:
                found.append(item)
    return found


def is_explicit_exclusion(row: dict[str, str]) -> bool:
    status = text(row, "현재 판정", "판정")
    origin = text(row, "완제품 제조국", "제조국")
    age = text(row, "대상 월령", "대상연령", "연령")
    reason = text(row, "판정·검증 사유", "판정 사유", "검증 사유")
    combined = f"{status} {origin} {age} {reason}"
    return (
        status in {"제외", "excluded"}
        or bool(re.search(r"중국|베트남|미국|독일|프랑스|일본|대만|말레이시아|싱가포르", origin))
        or bool(re.search(r"36\s*개월|37\s*개월|3\s*세\s*이상|0~35개월 대상 아님", combined))
        or bool(re.search(r"판매\s*종료|해외직구\s*전용|단일\s*제품.*아님|복수.*옵션", combined))
    )


def recovered_product(row: dict[str, str], index: int) -> dict:
    category = text(row, "제품군", "카테고리") or "기타"
    brand = text(row, "브랜드") or "브랜드 확인 중"
    name = text(row, "정확한 제품명", "제품명") or f"복구 제품 {index}"
    product_urls = urls(row)
    status = "제외" if is_explicit_exclusion(row) else "보류"
    return {
        "id": text(row, "ID", "제품 ID") or f"RECOVER-{index:04d}",
        "category": category,
        "subtype": text(row, "세부 유형", "완구 유형"),
        "brand": brand,
        "name": name,
        "status": status,
        "origin": text(row, "완제품 제조국", "제조국") or "확인 중",
        "saleStatus": text(row, "국내 판매 상태", "판매 상태") or "현재 판매 재확인 필요",
        "age": text(row, "대상 월령", "대상연령") or "확인 중",
        "ageBasis": text(row, "월령 근거", "대상연령 근거"),
        "kcNumber": text(row, "KC 인증번호", "KC 번호"),
        "kcStatus": text(row, "KC 인증상태", "인증상태"),
        "kcDate": text(row, "KC 인증일자", "인증일자"),
        "kcType": text(row, "KC 인증구분", "인증구분", "적용 안전제도"),
        "kcAuthority": text(row, "KC 인증기관", "인증기관"),
        "kcItem": text(row, "KC 품목명", "품목명"),
        "kcModel": text(row, "공식 모델명", "인증 모델명", "모델명"),
        "manufacturer": text(row, "제조사", "제조업체"),
        "safetyKoreaUrl": text(row, "Safety Korea 상세 URL", "Safety Korea URL", "인증 상세 URL"),
        "checkedAt": text(row, "확인일", "마지막 확인일"),
        "reason": text(row, "판정·검증 사유", "판정 사유", "검증 사유") or "공식 근거 재검증 대기",
        "evidenceUrls": product_urls,
        "quality": text(row, "데이터 품질") or "복구 데이터 · 엄격 재검증 필요",
        "history": text(row, "누적 이력", "검증 이력") or "Google Sheet 복구",
        "researchStatus": "전면 재검증 대기" if status == "보류" else "제외 근거 재확인 대기",
        "group": text(row, "대시보드 그룹") or category,
    }


def merge_backfill(existing: dict, recovered: dict) -> dict:
    """Preserve reviewed data and fill only blanks from the sheet."""
    merged = dict(existing)
    for key, value in recovered.items():
        if key == "evidenceUrls":
            current = [str(item) for item in merged.get(key, []) if str(item).strip()]
            incoming = [str(item) for item in value if str(item).strip()]
            merged[key] = list(dict.fromkeys(current + incoming))
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
    return merged


def main() -> None:
    existing: list[dict] = []
    if SOURCE.exists():
        existing = json.loads(SOURCE.read_text(encoding="utf-8"))

    response = requests.get(
        CSV_URL,
        timeout=45,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KBabyMadeRecovery/2.0)",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        },
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if len(rows) < 200:
        raise SystemExit(f"recovery source unexpectedly small: {len(rows)} rows")

    recovered_rows = [recovered_product(row, index) for index, row in enumerate(rows, 1)]
    verified = json.loads(SEED.read_text(encoding="utf-8")) if SEED.exists() else []
    verified_by_id = {str(item.get("id")): item for item in verified if item.get("id")}
    verified_by_key = {product_key(item): item for item in verified if all(product_key(item))}

    products = [dict(item) for item in existing]
    index_by_id = {
        str(item.get("id")): index
        for index, item in enumerate(products)
        if str(item.get("id", "")).strip()
    }
    index_by_key = {
        product_key(item): index
        for index, item in enumerate(products)
        if all(product_key(item))
    }

    added = 0
    merged_count = 0
    duplicate_source_rows = 0
    seen_source_ids: set[str] = set()
    seen_source_keys: set[tuple[str, str]] = set()

    for recovered in recovered_rows:
        pid = str(recovered.get("id", "")).strip()
        key = product_key(recovered)
        if (pid and pid in seen_source_ids) or (all(key) and key in seen_source_keys):
            duplicate_source_rows += 1
            continue
        if pid:
            seen_source_ids.add(pid)
        if all(key):
            seen_source_keys.add(key)

        seed = verified_by_id.get(pid) or verified_by_key.get(key)
        if seed:
            promoted = dict(recovered)
            promoted.update(seed)
            promoted["id"] = recovered["id"]
            recovered = promoted

        match_index = index_by_id.get(pid) if pid else None
        if match_index is None and all(key):
            match_index = index_by_key.get(key)

        if match_index is not None:
            products[match_index] = merge_backfill(products[match_index], recovered)
            merged_count += 1
            continue

        products.append(recovered)
        new_index = len(products) - 1
        if pid:
            index_by_id[pid] = new_index
        if all(key):
            index_by_key[key] = new_index
        added += 1

    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "status": "reconciled",
        "sourceRows": len(rows),
        "sourceUniqueRows": len(rows) - duplicate_source_rows,
        "duplicateSourceRows": duplicate_source_rows,
        "existingBefore": len(existing),
        "matchedAndBackfilled": merged_count,
        "addedFromSheet": added,
        "productsAfter": len(products),
        "included": sum(item.get("status") == "포함" for item in products),
        "pending": sum(item.get("status") == "보류" for item in products),
        "excluded": sum(item.get("status") == "제외" for item in products),
        "source": CSV_URL,
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
