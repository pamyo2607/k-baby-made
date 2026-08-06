#!/usr/bin/env python3
"""Bounded parallel executor for the ultra-strict research pipeline.

The evidence gate remains fail-closed. This runner adds alternate evidence URL
probing and a Bing fallback when the primary search endpoint is unavailable.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
from datetime import date
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

import research_runner as rr

PENDING_LIMIT = 50
NEW_PER_CATEGORY = 20
QUALITY = "ultra"
MAX_REVALIDATE_WORKERS = 10
MAX_DISCOVERY_WORKERS = 6
TIMEOUT = 18
PROBE_TIMEOUT = 12
RETRY_PAUSES = (0, 4, 12, 30)
THREAD_LOCAL = threading.local()
CACHE_LOCK = threading.Lock()
FETCH_CACHE: dict[str, tuple[int, str, str]] = {}
ORIGINAL_DDG_RESULTS = rr.ddg_results
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KBabyMadeEvidenceBot/2.2; +https://github.com/pamyo2607/k-baby-made)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}
LOW_PRIORITY_DOMAINS = ("coupang.com", "gmarket.co.kr", "ssg.com", "danawa.com")


def session() -> requests.Session:
    current = getattr(THREAD_LOCAL, "session", None)
    if current is None:
        current = requests.Session()
        current.headers.update(HEADERS)
        THREAD_LOCAL.session = current
    return current


def cached(url: str) -> tuple[int, str, str] | None:
    with CACHE_LOCK:
        return FETCH_CACHE.get(url)


def store(url: str, value: tuple[int, str, str]) -> tuple[int, str, str]:
    with CACHE_LOCK:
        FETCH_CACHE[url] = value
    return value


def bounded_fetch(url: str) -> tuple[int, str, str]:
    hit = cached(url)
    if hit is not None:
        return hit
    for pause in RETRY_PAUSES:
        if pause:
            time.sleep(pause)
        try:
            response = session().get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding or response.encoding
                return store(url, (200, response.text, response.url))
            if response.status_code not in {403, 408, 429, 500, 502, 503, 504}:
                return response.status_code, "", response.url
        except requests.RequestException:
            continue
    return 0, "", url


def probe(url: str) -> tuple[int, str, str]:
    hit = cached(url)
    if hit is not None:
        return hit
    try:
        response = session().get(url, timeout=PROBE_TIMEOUT, allow_redirects=True)
        if response.status_code == 200:
            response.encoding = response.apparent_encoding or response.encoding
            return store(url, (200, response.text, response.url))
        return response.status_code, "", response.url
    except requests.RequestException:
        return 0, "", url


def candidate_sales_urls(product: dict) -> list[str]:
    urls = [
        str(url)
        for url in product.get("evidenceUrls", [])
        if "safetykorea.kr" not in str(url) and rr.direct_product_url(str(url))
    ]
    return sorted(
        dict.fromkeys(urls),
        key=lambda url: (
            any(domain in urlparse(url).netloc for domain in LOW_PRIORITY_DOMAINS),
            urls.index(url),
        ),
    )


def select_reachable_sales_url(product: dict) -> bool:
    urls = candidate_sales_urls(product)
    for url in urls:
        status, body, final_url = probe(url)
        if status != 200 or not body:
            continue
        evidence = [str(value) for value in product.get("evidenceUrls", [])]
        reordered = [final_url, url] + [
            value for value in evidence if value not in {url, final_url}
        ]
        product["evidenceUrls"] = list(dict.fromkeys(reordered))
        return True
    return False


def multi_search(query: str) -> list[tuple[str, str]]:
    primary = ORIGINAL_DDG_RESULTS(query)
    if primary:
        return primary
    try:
        response = session().get(
            "https://www.bing.com/search?q=" + quote(query),
            timeout=PROBE_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code != 200:
            return []
        response.encoding = response.apparent_encoding or response.encoding
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[tuple[str, str]] = []
        for anchor in soup.select("li.b_algo h2 a"):
            href = str(anchor.get("href", "")).strip()
            title = anchor.get_text(" ", strip=True)
            if href.startswith(("http://", "https://")) and title:
                results.append((title, href))
        return results
    except requests.RequestException:
        return []


def safe_revalidate(product: dict) -> dict:
    try:
        if not select_reachable_sales_url(product):
            product["researchStatus"] = "모든 판매 증거 URL 연결 실패 · 재시도 필요"
            return {
                "id": product.get("id"),
                "checked": False,
                "changed": False,
                "quality": QUALITY,
                "errors": ["all_sales_urls_unreachable"],
            }
        return rr.revalidate(product, QUALITY)
    except Exception as exc:
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
    rr.ddg_results = multi_search
    products = json.loads(rr.SOURCE.read_text(encoding="utf-8"))

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
        url for item in products for url in item.get("evidenceUrls", [])
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

    effective_revalidations = sum(
        bool(item.get("checked") or item.get("changed")) for item in audits
    )
    new_state = {
        "lastRun": date.today().isoformat(),
        "qualityMode": QUALITY,
        "executionMode": "alternate-url-bounded-parallel",
        "scheduleTarget": "5분 최단 주기",
        "currentCategories": categories,
        "nextCategory": rr.CATEGORIES[0],
        "categoryIndex": 0,
        "pendingSelected": len(audits),
        "effectiveRevalidations": effective_revalidations,
        "verifiedThisRun": sum(bool(item.get("changed")) for item in audits),
        "includedThisRun": sum(
            item.get("status") == "포함" for item in products
        ),
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
