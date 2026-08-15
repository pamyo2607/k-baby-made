#!/usr/bin/env python3
"""Emit compact Google Sheets RowData payloads for the reviewed ID-based sync."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CSV_PATH = DATA / "master-db-419-final.csv"
ORDER_PATH = DATA / "sheet-sync-order.json"
PRODUCTS_PATH = DATA / "master-products.json"
OVERRIDES_PATH = DATA / "codex-revalidation-overrides.json"


def cell(value: object) -> dict:
    if value is None:
        value = ""
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def row(values: list[object]) -> dict:
    return {"values": [cell(value) for value in values]}


def master_rows(start: int, count: int) -> list[dict]:
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    fields = list(csv_rows[0])
    by_id = {item["ID"]: item for item in csv_rows}
    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))["ids"]
    output: list[dict] = []
    for physical_index, product_id in enumerate(order[start:start + count], start=start):
        item = by_id[product_id]
        values: list[object] = [item[field] for field in fields]
        values[0] = physical_index + 1
        output.append(row(values))
    return output


def queue_rows() -> list[dict]:
    products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))["ids"]
    master_row = {product_id: index + 2 for index, product_id in enumerate(order)}
    pending = [item for item in products if item.get("status") == "보류" and not item.get("duplicateOf")]
    pending.sort(key=lambda item: (
        len(item.get("revalidationMissingFields", [])),
        str(item.get("checkedAt", "")),
        item["id"],
    ))
    output = [row([
        "순위", "Master행", "ID", "제품군", "세부유형", "브랜드", "제품명", "현재판정", "우선순위",
        "국내판매상태", "대상월령", "완제품제조국", "KC번호", "적용안전제도", "미확인항목",
        "기존근거URL", "SafetyKorea검색어", "공식몰검색어", "재검증상태", "시도횟수", "최종확인일",
        "최종판정", "상세사유", "검증근거URL",
    ])]
    for rank, item in enumerate(pending, 1):
        fields = item.get("revalidationMissingFields", [])
        urls = [str(url) for url in item.get("officialUrls", []) if url]
        output.append(row([
            rank,
            master_row[item["id"]],
            item["id"],
            item.get("category", ""),
            item.get("subtype", ""),
            item.get("brand", ""),
            item.get("name", ""),
            item.get("status", ""),
            len(fields),
            item.get("saleStatus", ""),
            item.get("ageRange", ""),
            item.get("countryOfManufacture", ""),
            item.get("kcNumber", ""),
            item.get("regulatoryRegime", ""),
            " · ".join(fields),
            "\n".join(urls),
            f"{item.get('brand', '')} {item.get('name', '')} {item.get('kcNumber', '')} Safety Korea".strip(),
            f"{item.get('brand', '')} {item.get('name', '')} 공식".strip(),
            item.get("revalidationState", "공식 근거 추가 확인 중"),
            0,
            item.get("checkedAt", ""),
            "보류",
            item.get("reason", ""),
            "\n".join(urls),
        ]))
    return output


def history_rows() -> list[dict]:
    products = {item["id"]: item for item in json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))}
    changes = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))["changes"]
    output = [row([
        "2026-08-15 16:31",
        "운영 DB 복구·기준 정정",
        "라이브 459행 복구, 4개 중복 구조화, 공식 우선 재검증 16건 반영",
        "대한민국 완제품 제조 · 현재 판매 · 0~35개월 · 제품별 적용 법령 · 동일 모델 공식 근거",
        "Master DB · 재검증 대기열 · 웹앱",
    ])]
    for change in changes:
        item = products[change["id"]]
        output.append(row([
            "2026-08-15 16:31",
            "제품 재검증",
            f"{item['id']} · {item.get('name', '')} · {change.get('historyEntry', '')}",
            "공식 동일 제품 근거 우선 · 근거 부족은 보류",
            item.get("category", ""),
        ]))
    return output


def emit(mode: str, start: int, count: int) -> None:
    if mode == "master":
        payload = master_rows(start, count)
    elif mode == "queue":
        payload = queue_rows()[start:start + count]
    elif mode == "history":
        payload = history_rows()[start:start + count]
    else:
        raise SystemExit(f"unknown mode: {mode}")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("master", "queue", "history"))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=459)
    args = parser.parse_args()
    emit(args.mode, args.start, args.count)


if __name__ == "__main__":
    main()
