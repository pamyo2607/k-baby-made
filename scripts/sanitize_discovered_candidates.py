#!/usr/bin/env python3
"""Remove generic or duplicate auto-discovered candidates before publication.

Only DISC-* pending candidates are eligible for removal. Existing curated rows
and all included or excluded decisions are preserved unchanged.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/discovered-candidate-sanitization.json"
GENERIC = {
    "ai",
    "bing",
    "chatgpt",
    "google",
    "home",
    "main",
    "news",
    "search",
    "shop",
    "shopping",
    "검색",
    "뉴스",
    "메인",
    "쇼핑",
}


def normalize(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def product_key(item: dict) -> str:
    return f"{normalize(item.get('brand'))}|{normalize(item.get('name'))}"


def generic_candidate(item: dict) -> bool:
    brand = normalize(item.get("brand"))
    name = normalize(item.get("name"))
    if not brand or not name:
        return True
    if brand in GENERIC or name in GENERIC:
        return True
    if len(name) < 4:
        return True
    if brand == name and len(name) < 8:
        return True
    return False


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    existing_keys = {
        product_key(item)
        for item in products
        if not str(item.get("id", "")).startswith("DISC-")
    }
    seen_discovered: set[str] = set()
    kept: list[dict] = []
    removed: list[dict] = []

    for item in products:
        pid = str(item.get("id", ""))
        is_candidate = pid.startswith("DISC-") and item.get("status") == "보류"
        if not is_candidate:
            kept.append(item)
            continue

        key = product_key(item)
        reasons: list[str] = []
        if generic_candidate(item):
            reasons.append("generic_or_short_title")
        if key in existing_keys:
            reasons.append("duplicates_existing_product")
        if key in seen_discovered:
            reasons.append("duplicates_discovered_candidate")

        if reasons:
            removed.append({
                "id": pid,
                "category": item.get("category"),
                "brand": item.get("brand"),
                "name": item.get("name"),
                "key": key,
                "reasons": reasons,
            })
            continue

        seen_discovered.add(key)
        kept.append(item)

    remaining_counts = Counter(
        item.get("category")
        for item in kept
        if str(item.get("id", "")).startswith("DISC-")
        and item.get("status") == "보류"
    )
    SOURCE.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "inputCount": len(products),
        "outputCount": len(kept),
        "removedCount": len(removed),
        "remainingCandidatesByCategory": dict(remaining_counts),
        "removed": removed,
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
