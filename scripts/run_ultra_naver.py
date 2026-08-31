#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver discovery and sale rechecks."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import naver_product_discovery as naver
import naver_product_discovery_paced as paced
import run_ultra_parallel as pipeline

_original_revalidate = pipeline.safe_revalidate
_original_resolve = naver.resolve_product_url
_original_title_matches = naver.title_matches
_original_select_pending_batch = pipeline.select_pending_batch
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
    # The rich canonical verifier owns the decision. A marketplace-only
    # fallback must not be counted as a successful official revalidation.
    return _original_revalidate(product)


def discover(category: str, products: list[dict]) -> list[dict]:
    return paced.discover(category, products, pipeline.NEW_PER_CATEGORY)


def select_pending_batch(
    candidates: list[dict], previous_state: dict, campaign: dict | None
) -> tuple[list[dict], int, int, int, str]:
    """Keep retrying unresolved campaign rows after first-pass coverage.

    The original selector correctly prioritizes never-attempted campaign IDs.
    Once every target has been attempted it returns an empty batch even when
    targets are still pending. That stranded the ING campaign at
    coverage-complete-proof-incomplete. In that state rotate through the fixed
    campaign snapshot and retry only rows that are still canonical pending.
    """
    if (
        isinstance(campaign, dict)
        and campaign.get("coverageComplete") is True
        and campaign.get("resolutionComplete") is not True
    ):
        target_ids = [str(value) for value in campaign.get("targetIdsSnapshot", [])]
        candidate_by_id = {
            str(item.get("id", "")): item
            for item in candidates
            if str(item.get("id", ""))
        }
        if target_ids and len(target_ids) == len(set(target_ids)):
            unresolved_ids = [value for value in target_ids if value in candidate_by_id]
            target_count = len(target_ids)
            cursor_start = int(previous_state.get("pendingCursorNext", 0) or 0)
            cursor_start %= target_count

            selected_ids: list[str] = []
            last_offset = -1
            for offset in range(target_count):
                product_id = target_ids[(cursor_start + offset) % target_count]
                if product_id in candidate_by_id:
                    selected_ids.append(product_id)
                    last_offset = offset
                    if len(selected_ids) >= pipeline.PENDING_LIMIT:
                        break

            selected = [candidate_by_id[value] for value in selected_ids]
            cursor_next = (
                (cursor_start + last_offset + 1) % target_count
                if last_offset >= 0
                else cursor_start
            )
            return (
                selected,
                len(unresolved_ids),
                cursor_start,
                cursor_next,
                "campaign-unresolved-retry",
            )

    return _original_select_pending_batch(candidates, previous_state, campaign)


naver.resolve_product_url = resolve_product_url
naver.title_matches = strict_title_matches
naver.infer_brand = infer_brand
pipeline.safe_revalidate = revalidate
pipeline.safe_discover = discover
pipeline.select_pending_batch = select_pending_batch
pipeline.main()
