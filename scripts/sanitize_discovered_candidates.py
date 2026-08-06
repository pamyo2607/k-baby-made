#!/usr/bin/env python3
"""Remove irrelevant or duplicate auto-discovered candidates before publication.

Only DISC-* pending candidates are eligible for removal. Existing curated rows
and every included or excluded decision are preserved unchanged.
"""
from __future__ import annotations

import base64
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
REPORT = ROOT / "data/discovered-candidate-sanitization.json"
GENERIC = {
    "ai", "bing", "chatgpt", "google", "help", "home", "main", "news",
    "query", "search", "shop", "shopping", "검색", "뉴스", "메인", "쇼핑",
}
BLOCKED_DOMAINS = {
    "bing.com", "www.bing.com", "google.com", "www.google.com",
    "youtube.com", "www.youtube.com", "instagram.com", "www.instagram.com",
    "facebook.com", "www.facebook.com", "wikipedia.org", "en.wikipedia.org",
    "imdb.com", "www.imdb.com", "yahoo.com", "www.yahoo.com",
}
DIRECT_HINTS = (
    "/product", "/products", "/item", "/goods", "/shopdetail",
    "/view", "/detail", "/vp/", "/i/item", "/p/",
)
CATEGORY_TERMS = {
    "완구": ("완구", "장난감", "딸랑이", "모빌", "촉감", "래틀", "놀이"),
    "구강치발기": ("치발기", "구강", "잇몸", "티더", "teether"),
    "턱받이": ("턱받이", "빕", "bib"),
    "수유용품": ("수유", "젖병", "분유", "빨대컵", "수유쿠션", "유축"),
    "이유식용품": ("이유식", "흡착식판", "스푼", "유아식기", "아기식기", "이유식기"),
    "위생용품": ("위생", "물티슈", "기저귀", "손수건", "목욕", "세정", "면봉"),
}


def normalize(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def product_key(item: dict) -> str:
    return f"{normalize(item.get('brand'))}|{normalize(item.get('name'))}"


def has_korean(value: object) -> bool:
    return bool(re.search(r"[가-힣]", str(value or "")))


def decode_bing_target(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"bing.com", "www.bing.com"} or not parsed.path.startswith("/ck/"):
        return url
    raw = parse_qs(parsed.query).get("u", [""])[0]
    raw = unquote(raw)
    if raw.startswith("a1"):
        raw = raw[2:]
    if not raw:
        return url
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="strict")
        return decoded if decoded.startswith(("http://", "https://")) else url
    except (ValueError, UnicodeDecodeError):
        return url


def direct_product_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    domain = parsed.netloc.lower()
    if domain in BLOCKED_DOMAINS or any(domain.endswith("." + blocked) for blocked in BLOCKED_DOMAINS):
        return False
    path = parsed.path.lower()
    blocked_paths = ("/search", "/category", "/categories", "/guide", "/board", "/event")
    if any(value in path for value in blocked_paths):
        return False
    return any(value in path for value in DIRECT_HINTS) or bool(parsed.query)


def category_matches(item: dict) -> bool:
    category = str(item.get("category", ""))
    text = f"{item.get('brand', '')} {item.get('name', '')}".lower()
    terms = CATEGORY_TERMS.get(category, ())
    return bool(terms) and any(term.lower() in text for term in terms)


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


def candidate_url(item: dict) -> str:
    for raw in item.get("evidenceUrls", []):
        decoded = decode_bing_target(str(raw))
        if direct_product_url(decoded):
            return decoded
    return ""


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
        resolved_url = candidate_url(item)
        reasons: list[str] = []
        if generic_candidate(item):
            reasons.append("generic_or_short_title")
        if not has_korean(f"{item.get('brand', '')} {item.get('name', '')}"):
            reasons.append("no_korean_product_identity")
        if not category_matches(item):
            reasons.append("category_term_mismatch")
        if not resolved_url:
            reasons.append("no_direct_korean_sales_candidate_url")
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
                "reasons": sorted(set(reasons)),
            })
            continue

        item["evidenceUrls"] = [resolved_url] + [
            str(value) for value in item.get("evidenceUrls", [])
            if decode_bing_target(str(value)) != resolved_url
            and "bing.com/ck/" not in str(value)
        ]
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
