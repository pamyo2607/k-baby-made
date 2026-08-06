#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver discovery and sale rechecks."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import naver_pending_revalidation as pending
import naver_product_discovery as naver
import naver_product_discovery_paced as paced
import run_ultra_parallel as pipeline

_original_revalidate = pipeline.safe_revalidate
_original_resolve = naver.resolve_product_url
_original_title_matches = naver.title_matches
naver.DIRECT_HINTS = tuple(dict.fromkeys(naver.DIRECT_HINTS + ("/catalog/",)))
GENERIC_BRAND_TOKENS = {
    "국내", "국내생산", "국산", "생산", "아기", "유아", "신생아", "공식",
    "공식몰", "인기", "추천", "특가", "할인", "무료배송", "kc", "인증",
}


def resolve_product_url(url: str) -> str:
    parsed = urlparse(url)
    nv_mid = parse_qs(parsed.query).get("nv_mid", [""])[0]
    if nv_mid.isdigit():
        return f"https://search.shopping.naver.com/catalog/{nv_mid}"
    return _original_resolve(url)


def strict_title_matches(category: str, title: str) -> bool:
    if not _original_title_matches(category, title):
        return False
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", title.lower())
    return len(normalized) >= 8


def infer_brand(title: str) -> str:
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", title or ""):
        lowered = token.lower()
        if lowered not in GENERIC_BRAND_TOKENS and not lowered.isdigit():
            return token[:40]
    return "브랜드 확인 중"


def revalidate(product: dict) -> dict:
    try:
        result = _original_revalidate(product)
    except Exception:
        result = {"checked": False, "changed": False, "errors": ["primary_revalidation_exception"]}
    if result.get("checked"):
        return result
    return pending.revalidate(product)


def discover(category: str, products: list[dict]) -> list[dict]:
    try:
        return paced.discover(category, products, pipeline.NEW_PER_CATEGORY)
    except Exception as exc:
        print(f"naver discovery failed for {category}: {type(exc).__name__}: {exc}")
        return []


naver.resolve_product_url = resolve_product_url
naver.title_matches = strict_title_matches
naver.infer_brand = infer_brand
pipeline.safe_revalidate = revalidate
pipeline.safe_discover = discover
pipeline.main()
