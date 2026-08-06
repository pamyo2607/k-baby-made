#!/usr/bin/env python3
"""Complete a fresh current-sale check for pending products through Naver.

A marketplace search result is used only to confirm a current Korean listing and
recover a direct product URL. It never by itself promotes a product to included.
KC, Korean manufacture, age and exact identity still require the ultra gate.
"""
from __future__ import annotations

import re
from datetime import date

import naver_product_discovery as naver

GENERIC = {
    "아기", "유아", "신생아", "제품", "세트", "국내", "공식", "완구",
    "치발기", "턱받이", "수유용품", "이유식용품", "위생용품", "컬러",
}


def tokens(value: str) -> set[str]:
    return {
        item.lower()
        for item in re.findall(r"[가-힣A-Za-z0-9]{2,}", value or "")
        if item.lower() not in GENERIC
    }


def identity_score(product: dict, title: str) -> tuple[int, float]:
    expected = tokens(f"{product.get('brand', '')} {product.get('name', '')}")
    actual = title.lower()
    overlap = sum(token in actual for token in expected)
    ratio = overlap / max(1, len(expected))
    return overlap, ratio


def exact_listing(product: dict) -> tuple[str, str]:
    category = str(product.get("category", ""))
    query = f"{product.get('brand', '')} {product.get('name', '')}".strip()
    if not query or category not in naver.CATEGORY_TERMS:
        return "", ""
    candidates = naver.collect_query(category, query)
    ranked: list[tuple[float, int, str, str]] = []
    for title, href, _ in candidates:
        overlap, ratio = identity_score(product, title)
        if overlap < 2 and ratio < 0.5:
            continue
        ranked.append((ratio, overlap, title, href))
    ranked.sort(reverse=True)
    for _, _, title, href in ranked[:12]:
        final_url = naver.resolve_product_url(href)
        if final_url:
            return title, final_url
    return "", ""


def revalidate(product: dict) -> dict:
    today = date.today().isoformat()
    previous_reason = str(product.get("reason", "")).strip()
    title, final_url = exact_listing(product)
    product["checkedAt"] = today

    if final_url:
        evidence = [str(value) for value in product.get("evidenceUrls", [])]
        product["evidenceUrls"] = list(dict.fromkeys([final_url] + evidence))
        product["saleStatus"] = "네이버에서 정확 제품명 현재 판매 상품 상세 확인"
        product["researchStatus"] = "현재 판매 재확인 완료 · KC·제조국·월령 최극상 검증 대기"
        entry = (
            f"{today} 네이버에서 제품명·브랜드 식별 토큰이 일치하는 현재 판매 상품 "
            f"'{title}'과 상세 URL {final_url}을 확인했다. 이 근거만으로 포함 처리하지 않고 "
            "KC·대한민국 제조·0~35개월·동일 모델 공식 근거를 추가 검증한다."
        )
        product["reason"] = f"{previous_reason}\n{entry}".strip()
        history = str(product.get("history", "")).strip()
        product["history"] = f"{history}\n{entry}".strip()
        return {
            "id": product.get("id"),
            "checked": True,
            "changed": True,
            "quality": "ultra",
            "errors": [],
            "saleListingConfirmed": True,
            "replacementSalesUrl": final_url,
        }

    product["saleStatus"] = "네이버 정확 제품명 현재 판매 확인 실패 · 보류"
    product["researchStatus"] = "현재 판매 근거 재탐색 완료 · 확인 실패로 보류 유지"
    entry = (
        f"{today} 브랜드와 정확 제품명으로 네이버 국내 판매 상품을 재조회했으나 "
        "제품 동일성이 충분한 실제 상품 상세 URL을 확보하지 못했다. 판매 종료로 단정하지 않고 보류를 유지한다."
    )
    product["reason"] = f"{previous_reason}\n{entry}".strip()
    history = str(product.get("history", "")).strip()
    product["history"] = f"{history}\n{entry}".strip()
    return {
        "id": product.get("id"),
        "checked": True,
        "changed": True,
        "quality": "ultra",
        "errors": ["current_sale_listing_not_confirmed"],
        "saleListingConfirmed": False,
    }
