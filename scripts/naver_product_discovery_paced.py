#!/usr/bin/env python3
"""Paced cumulative Naver discovery resilient to runner-region throttling."""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
import re
import time
from datetime import date
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

import naver_product_discovery as base

SUFFIXES = ("", "스마트스토어", "국내생산", "KC 인증")


def collect_from_url(category: str, query: str, search_url: str) -> list[tuple[str, str, str]]:
    try:
        response = base.session().get(search_url, timeout=20)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        title = base.clean_title(anchor.get_text(" ", strip=True))
        href = html.unescape(str(anchor.get("href", "")).strip())
        if not base.title_matches(category, title):
            continue
        if not href.startswith(("https://", "http://")) or base.blocked_url(href):
            continue
        domain = urlparse(href).netloc.lower()
        has_mid = "nv_mid=" in href
        if domain not in base.BRIDGE_DOMAINS and not has_mid and not base.direct_product_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        found.append((title, href, query))
    return found


def collect_query(category: str, query: str) -> list[tuple[str, str, str]]:
    encoded = quote(query)
    urls = (
        f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query={encoded}",
        f"https://m.search.naver.com/search.naver?where=m&query={encoded}",
    )
    combined: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for url in urls:
        for item in collect_from_url(category, query, url):
            key = (item[0], item[1])
            if key not in seen:
                seen.add(key)
                combined.append(item)
        if combined:
            break
    return combined


def build_candidate(category: str, title: str, final_url: str, query: str) -> dict:
    brand = base.infer_brand(title)
    digest = hashlib.sha1(f"{category}|{final_url}".encode()).hexdigest()[:12].upper()
    today = date.today().isoformat()
    product_id = f"DISC-{digest}"
    return {
        "id": product_id,
        "category": category,
        "subtype": "",
        "brand": brand,
        "name": title,
        "status": "보류",
        "countryOfManufacture": "확인 중",
        "saleStatus": "네이버에서 특정 상품 상세 URL 확인 · 현재 판매 상태 재검증 대기",
        "ageRange": "확인 중",
        "ageEvidence": "",
        "kcApplicable": "제품별 법령 확인 필요",
        "kcNumber": "",
        "kcType": "",
        "certStatusSummary": "",
        "certDateSummary": "",
        "certTypeSummary": "",
        "certAuthoritySummary": "",
        "certifications": [],
        "officialModel": "",
        "manufacturer": "",
        "importer": "",
        "safetyKoreaSearchUrl": "",
        "checkedAt": today,
        "reason": (
            f"네이버 검색어 '{query}'에서 카테고리와 일치하는 특정 상품 상세 URL을 확인했다. "
            "국내 현재 판매와 0~35개월 대상과 대한민국 완제품 제조와 동일 제품 KC 번호와 "
            "Safety Korea 상세 근거를 모두 대조하기 전까지 보류한다."
        ),
        "officialUrls": [final_url],
        "saleUrls": [final_url],
        "quality": "실제 국내 상품 상세 후보 · 최극상 검증 전",
        "historySummary": f"{today} 네이버 특정 상품 후보 조사",
        "revalidationState": "최극상 전면 재검증 대기",
        "revalidationResolved": False,
        "revalidationMissingFields": [
            "currentSale", "officialAge", "countryOfManufacture",
            "manufacturerOrImporter", "regulatoryRegime", "officialEvidence",
        ],
        "dashboardGroup": category,
        "canonicalProductId": product_id,
        "duplicateOf": "",
        "discoveryProvider": "Naver",
    }


def discover(category: str, products: list[dict], requested_limit: int = 5) -> list[dict]:
    # The runner owns the cross-category total cap.  Existing cumulative DISC
    # rows must not be mistaken for this run's target or output.
    needed = max(0, requested_limit)
    if needed == 0:
        return []

    queries = [
        f"{base_query} {suffix}".strip()
        for base_query in base.CATEGORY_QUERIES.get(category, [category])
        for suffix in SUFFIXES
    ]
    raw: list[tuple[str, str, str]] = []
    raw_seen: set[tuple[str, str]] = set()
    for offset in range(0, len(queries), 4):
        chunk = queries[offset:offset + 4]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            batches = list(executor.map(lambda value: collect_query(category, value), chunk))
        for batch in batches:
            for item in batch:
                key = (item[0], item[1])
                if key not in raw_seen:
                    raw_seen.add(key)
                    raw.append(item)
        if len(raw) >= max(needed * 5, 40):
            break
        time.sleep(0.15)

    existing_urls = {
        base.canonicalize(str(url))
        for item in products
        for url in item.get("evidenceUrls", [])
        if str(url).startswith(("http://", "https://"))
    }
    existing_keys = {
        re.sub(r"[^0-9a-z가-힣]+", "", f"{item.get('brand', '')}|{item.get('name', '')}".lower())
        for item in products
    }
    output: list[dict] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()

    for offset in range(0, len(raw), 12):
        chunk = raw[offset:offset + 12]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(base.resolve_product_url, href): (title, query)
                for title, href, query in chunk
            }
            resolved = []
            for future in concurrent.futures.as_completed(futures):
                title, query = futures[future]
                try:
                    url = future.result()
                except Exception:
                    url = ""
                if url:
                    resolved.append((title, url, query))
        for title, final_url, query in resolved:
            brand = base.infer_brand(title)
            key = re.sub(r"[^0-9a-z가-힣]+", "", f"{brand}|{title}".lower())
            if (
                final_url in existing_urls
                or final_url in seen_urls
                or key in existing_keys
                or key in seen_keys
            ):
                continue
            output.append(build_candidate(category, title, final_url, query))
            seen_urls.add(final_url)
            seen_keys.add(key)
            if len(output) >= needed:
                return output
        if len(output) < needed:
            time.sleep(0.1)
    return output
