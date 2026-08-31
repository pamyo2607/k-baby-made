#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver discovery and sale rechecks."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import naver_pending_revalidation as pending_sale
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
ERROR_PRIORITY = {
    "ultra_strict_match": 0,
    "safety_korea": 1,
    "kc_number_on_sales_page": 2,
    "law_specific_evidence": 3,
    "age_basis": 4,
    "sale_inactive": 5,
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


def prioritize_url(product: dict, url: str, final_url: str = "") -> None:
    ordered = [value for value in (final_url, url) if value]
    current_sale = [str(value) for value in product.get("saleUrls", [])]
    current_official = [str(value) for value in product.get("officialUrls", [])]
    product["saleUrls"] = list(dict.fromkeys(
        ordered + [value for value in current_sale if value not in ordered]
    ))
    product["officialUrls"] = list(dict.fromkeys(
        ordered + [value for value in current_official if value not in ordered]
    ))


def evidence_score(working: dict, result: dict) -> tuple[int, int, int, int]:
    status = str(working.get("status", ""))
    resolved_rank = 0 if status in {"포함", "제외"} else 1
    missing = [
        str(value) for value in working.get("revalidationMissingFields", [])
        if str(value)
    ]
    errors = [str(value) for value in result.get("errors", []) if str(value)]
    error_rank = min(
        (ERROR_PRIORITY.get(value, len(ERROR_PRIORITY)) for value in errors),
        default=-1,
    )
    checked_rank = 0 if result.get("checked") else 1
    return resolved_rank, len(missing), checked_rank, error_rank


def revalidate(product: dict) -> dict:
    """Evaluate all known direct evidence pages and keep the strongest result.

    The old runner stopped at the first reachable URL. That caused a product to
    remain pending when a marketplace page lacked age/KC text even though a
    second official product page contained stronger evidence. Every known
    direct URL is now evaluated independently under the same fail-closed gate.
    No marketplace-only result can promote a product to included.
    """
    original = deepcopy(product)
    urls = pipeline.candidate_sales_urls(original)
    best: tuple[tuple[int, int, int, int], dict, dict] | None = None
    tried: list[str] = []

    for url in urls[:10]:
        status, body, final_url = pipeline.probe(url)
        if status != 200 or not body:
            continue
        tried.append(final_url or url)
        working = deepcopy(original)
        prioritize_url(working, url, final_url)
        result = pipeline.rr.revalidate(working, pipeline.QUALITY)
        if not result.get("checked"):
            continue
        score = evidence_score(working, result)
        if best is None or score < best[0]:
            best = (score, working, result)
        if working.get("status") in {"포함", "제외"}:
            break

    if best is None:
        # Recover a fresh exact Korean listing only as a current-sale lead.
        # It is then sent through the same canonical ultra verifier rather than
        # being trusted as official manufacturing/KC evidence by itself.
        _, fresh_url = pending_sale.exact_listing(original)
        if fresh_url:
            status, body, final_url = pipeline.probe(fresh_url)
            if status == 200 and body:
                tried.append(final_url or fresh_url)
                working = deepcopy(original)
                prioritize_url(working, fresh_url, final_url)
                result = pipeline.rr.revalidate(working, pipeline.QUALITY)
                if result.get("checked"):
                    best = (evidence_score(working, result), working, result)

    if best is None:
        return _original_revalidate(product)

    _, working, result = best
    product.clear()
    product.update(working)
    result["changed"] = product != original
    result["evidencePagesTried"] = list(dict.fromkeys(tried))
    return result


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


def normalize_schedule_metadata() -> None:
    if pipeline.rr.STATE.exists():
        state = json.loads(pipeline.rr.STATE.read_text(encoding="utf-8"))
        state["scheduleTarget"] = "5분 주기"
        pipeline.rr.STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if pipeline.rr.AUDIT.exists():
        audit = json.loads(pipeline.rr.AUDIT.read_text(encoding="utf-8"))
        if isinstance(audit.get("state"), dict):
            audit["state"]["scheduleTarget"] = "5분 주기"
        pipeline.rr.AUDIT.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


naver.resolve_product_url = resolve_product_url
naver.title_matches = strict_title_matches
naver.infer_brand = infer_brand
pipeline.safe_revalidate = revalidate
pipeline.safe_discover = discover
pipeline.select_pending_batch = select_pending_batch
pipeline.main()
normalize_schedule_metadata()
