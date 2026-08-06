#!/usr/bin/env python3
"""Bounded parallel executor for the ultra-strict research pipeline.

Evidence rules remain fail-closed. Parallelism only reduces wall-clock time.
Network failures never promote a product to included status.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
from datetime import date

import requests

import research_runner as rr

PENDING_LIMIT = 50
NEW_PER_CATEGORY = 20
QUALITY = "ultra"
MAX_REVALIDATE_WORKERS = 10
MAX_DISCOVERY_WORKERS = 6
TIMEOUT = 18
RETRY_PAUSES = (0, 4, 12, 30)
THREAD_LOCAL = threading.local()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KBabyMadeEvidenceBot/2.1; +https://github.com/pamyo2607/k-baby-made)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}


def session() -> requests.Session:
    current = getattr(THREAD_LOCAL, "session", None)
    if current is None:
        current = requests.Session()
        current.headers.update(HEADERS)
        THREAD_LOCAL.session = current
    return current


def bounded_fetch(url: str) -> tuple[int, str, str]:
    """Retry transient failures while keeping one run bounded."""
    for pause in RETRY_PAUSES:
        if pause:
            time.sleep(pause)
        try:
            response = session().get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding or response.encoding
                return 200, response.text, response.url
            if response.status_code not in {403, 408, 429, 500, 502, 503, 504}:
                return response.status_code, "", response.url
        except requests.RequestException:
            continue
    return 0, "", url


def safe_revalidate(product: dict) -> dict:
    try:
        return rr.revalidate(product, QUALITY)
    except Exception as exc:  # fail closed and retain the row for later review
        product["status"] = "보류"
        product["checkedAt"] = date.today().isoformat()
        product["researchStatus"] = "자동 재검증 오류 · 수동 확인 필요"
        return {
            "id": product.get("id"),
            "checked": False,
            "changed": True,
            "quality": QUALITY,
            "errors": [f"exception:{type(exc).__name__}"],
        }


def safe_discover(category: str, products: list[dict]) -> list[dict]:
    try:
        return rr.discover(category, products, NEW_PER_CATEGORY)
    except Exception:
        return []


def normalized_key(item: dict) -> str:
    return re.sub(
        r"\s+",
        "",
        f"{item.get('brand', '')}|{item.get('name', '')}",
    ).lower()


def main() -> None:
    rr.fetch = bounded_fetch
    products = json.loads(rr.SOURCE.read_text(encoding="utf-8"))
    state = (
        json.loads(rr.STATE.read_text(encoding="utf-8"))
        if rr.STATE.exists()
        else {"categoryIndex": 0}
    )

    candidates = [item for item in products if item.get("status") == "보류"]
    candidates.sort(key=lambda item: (
        not bool(item.get("kcNumber")),
        item.get("checkedAt", ""),
    ))
    selected = candidates[:PENDING_LIMIT]
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(MAX_REVALIDATE_WORKERS, len(selected) or 1)),
        thread_name_prefix="revalidate",
    ) as executor:
        audits = list(executor.map(safe_revalidate, selected))

    categories = list(rr.CATEGORIES)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_DISCOVERY_WORKERS,
        thread_name_prefix="discover",
    ) as executor:
        batches = list(executor.map(
            lambda category: safe_discover(category, products),
            categories,
        ))

    seen_urls = {
        url
        for item in products
        for url in item.get("evidenceUrls", [])
    }
    seen_keys = {normalized_key(item) for item in products}
    per_category = {category: 0 for category in categories}
    discovered: list[dict] = []
    for batch in batches:
        for item in batch:
            category = str(item.get("category", ""))
            url = next(iter(item.get("evidenceUrls", [])), "")
            key = normalized_key(item)
            if (
                not url
                or url in seen_urls
                or key in seen_keys
                or per_category.get(category, 0) >= NEW_PER_CATEGORY
            ):
                continue
            discovered.append(item)
            seen_urls.add(url)
            seen_keys.add(key)
            per_category[category] = per_category.get(category, 0) + 1
    products.extend(discovered)

    new_state = {
        "lastRun": date.today().isoformat(),
        "qualityMode": QUALITY,
        "executionMode": "bounded-parallel",
        "scheduleTarget": "5분 최단 주기",
        "currentCategories": categories,
        "nextCategory": rr.CATEGORIES[0],
        "categoryIndex": 0,
        "pendingSelected": len(audits),
        "verifiedThisRun": sum(bool(item.get("changed")) for item in audits),
        "includedThisRun": sum(item.get("status") == "포함" for item in products),
        "newCandidates": len(discovered),
        "newCandidatesByCategory": per_category,
        "errors": sum(bool(item.get("errors")) for item in audits),
        "totalProducts": len(products),
    }
    rr.SOURCE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rr.STATE.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rr.AUDIT.write_text(
        json.dumps(
            {"state": new_state, "products": audits},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(new_state, ensure_ascii=False))


if __name__ == "__main__":
    main()
