#!/usr/bin/env python3
"""Bounded parallel executor for the ultra-strict research pipeline.

The evidence gate remains fail-closed. This runner adds alternate evidence URL
probing and a Bing fallback when the primary search endpoint is unavailable.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

import research_runner as rr

PENDING_LIMIT = int(os.environ.get("KBABY_PENDING_LIMIT", "50"))
NEW_TOTAL_LIMIT = int(os.environ.get("KBABY_NEW_CANDIDATE_LIMIT", "30"))
NEW_PER_CATEGORY = max(
    1,
    (NEW_TOTAL_LIMIT + len(rr.CATEGORIES) - 1) // len(rr.CATEGORIES),
)
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
PLACEHOLDER_BRANDS = {"", "브랜드 확인 중", "확인 중", "미상"}
STAGING = rr.ROOT / "data/discovered-candidate-staging.json"
TOMBSTONES = rr.ROOT / "data/deleted-duplicate-tombstones.json"
ING_CAMPAIGN = rr.ROOT / "data/campaign-ing-revalidation.json"
FIELD_PRIORITY = {
    "safetyKoreaSameModel": 0,
    "countryOfManufacture": 1,
    "manufacturerOrImporter": 2,
    "officialAge": 3,
    "currentSale": 4,
    "sameProductIdentity": 5,
    "exactKcNumber": 6,
    "officialEvidence": 7,
    "regulatoryRegime": 8,
}


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
        for url in (
            list(product.get("saleUrls", []))
            + list(product.get("officialUrls", []))
            + list(product.get("revalidationEvidenceUrls", []))
            + list(product.get("evidenceUrls", []))
        )
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
        evidence = [str(value) for value in product.get("officialUrls", [])]
        reordered = [final_url, url] + [
            value for value in evidence if value not in {url, final_url}
        ]
        product["officialUrls"] = list(dict.fromkeys(reordered))
        product["saleUrls"] = list(dict.fromkeys(
            [final_url, url] + [str(value) for value in product.get("saleUrls", [])]
        ))
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
    original_product = deepcopy(product)
    try:
        if not select_reachable_sales_url(product):
            return {
                "id": product.get("id"),
                "checked": False,
                "changed": False,
                "quality": QUALITY,
                "errors": ["all_sales_urls_unreachable"],
            }
        result = rr.revalidate(product, QUALITY)
        if not result.get("checked"):
            product.clear()
            product.update(original_product)
        return result
    except Exception:
        product.clear()
        product.update(original_product)
        raise


def safe_discover(category: str, products: list[dict]) -> list[dict]:
    return rr.discover(category, products, NEW_PER_CATEGORY)


def normalized_key(item: dict) -> str:
    return re.sub(
        r"\s+",
        "",
        f"{item.get('brand', '')}|{item.get('name', '')}",
    ).lower()


def exact_candidate_shape(item: dict) -> bool:
    """Fail closed before a discovered row can enter the staging inventory.

    This is intentionally not permission to enter the canonical database.
    Staged candidates need a concrete identity and individual detail URL;
    promotion remains a separate official-evidence decision.
    """
    candidate_id = str(item.get("id", ""))
    category = str(item.get("category", ""))
    brand = str(item.get("brand", "")).strip()
    name = str(item.get("name", "")).strip()
    if (
        not candidate_id.startswith("DISC-")
        or item.get("status") != "보류"
        or category not in rr.CATEGORIES
        or brand in PLACEHOLDER_BRANDS
        or len(re.sub(r"[^0-9a-z가-힣]+", "", name.lower())) < 8
    ):
        return False

    urls = [
        str(value).strip()
        for value in list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", []))
    ]
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if any(value in path for value in ("/search", "/category", "/categories", "/event")):
            continue
        if any(value in path for value in (
            "/product", "/products", "/item", "/goods", "/detail",
            "/view", "/catalog/", "/shopdetail", "/shopview",
        )) or bool(parsed.query):
            return True
    return False


def pending_priority(item: dict) -> tuple:
    """Put one-field, near-resolution rows first without starving the rest.

    The persistent cursor below advances through this deterministic order on
    every run. This prevents the first 50 KC-bearing rows from being retried
    forever while hundreds of later pending rows are never reached.
    """
    fields = [
        str(value) for value in item.get("revalidationMissingFields", [])
        if str(value)
    ]
    field_rank = min(
        (FIELD_PRIORITY.get(value, len(FIELD_PRIORITY)) for value in fields),
        default=len(FIELD_PRIORITY),
    )
    return (
        len(fields) != 1,
        field_rank,
        len(fields),
        str(item.get("checkedAt", "")),
        str(item.get("id", "")),
    )


def tombstone_dedupe_rows() -> list[dict]:
    if not TOMBSTONES.exists():
        return []
    document = json.loads(TOMBSTONES.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for item in document.get("records", []):
        brands = [item.get("brand", ""), *item.get("brandAliases", [])]
        for brand in dict.fromkeys(str(value).strip() for value in brands if str(value).strip()):
            rows.append({
                "id": item.get("deletedId", ""),
                "brand": brand,
                "name": item.get("name", ""),
                "officialUrls": list(item.get("evidenceUrls", [])),
                "evidenceUrls": list(item.get("evidenceUrls", [])),
            })
    return rows


def select_pending_batch(
    candidates: list[dict], previous_state: dict, campaign: dict | None
) -> tuple[list[dict], int, int, int, str]:
    """Prefer never-attempted immutable campaign IDs before rotating retries."""
    candidate_by_id = {str(item.get("id", "")): item for item in candidates}
    if isinstance(campaign, dict):
        target_ids = [str(value) for value in campaign.get("targetIdsSnapshot", [])]
        attempted_ids = [str(value) for value in campaign.get("attemptedIds", [])]
        remaining_ids = [str(value) for value in campaign.get("remainingIds", [])]
        if (
            target_ids
            and all(target_ids)
            and len(target_ids) == len(set(target_ids))
            and len(attempted_ids) == len(set(attempted_ids))
            and len(remaining_ids) == len(set(remaining_ids))
            and not set(attempted_ids).intersection(remaining_ids)
            and set(attempted_ids).union(remaining_ids) == set(target_ids)
        ):
            remaining_candidates = [
                candidate_by_id[value]
                for value in remaining_ids
                if value in candidate_by_id
            ]
            if remaining_candidates:
                selected = remaining_candidates[:PENDING_LIMIT]
                cursor_start = len(attempted_ids)
                cursor_next = cursor_start + len(selected)
                return (
                    selected,
                    len(remaining_candidates),
                    cursor_start,
                    cursor_next,
                    "campaign-unattempted",
                )

    pending_available = len(candidates)
    cursor_start = int(previous_state.get("pendingCursorNext", 0) or 0)
    if pending_available:
        cursor_start %= pending_available
    else:
        cursor_start = 0
    selected = candidates[cursor_start:cursor_start + PENDING_LIMIT]
    if len(selected) < min(PENDING_LIMIT, pending_available):
        selected.extend(candidates[:min(
            cursor_start,
            min(PENDING_LIMIT, pending_available) - len(selected),
        )])
    cursor_next = (
        (cursor_start + len(selected)) % pending_available
        if pending_available
        else 0
    )
    return selected, pending_available, cursor_start, cursor_next, "rotating-retry"


def main() -> None:
    if not 0 <= PENDING_LIMIT <= 50:
        raise SystemExit(f"KBABY_PENDING_LIMIT must be between 0 and 50: {PENDING_LIMIT}")
    if not 0 <= NEW_TOTAL_LIMIT <= 30:
        raise SystemExit(
            "KBABY_NEW_CANDIDATE_LIMIT must be between 0 and 30: "
            f"{NEW_TOTAL_LIMIT}"
        )

    rr.fetch = bounded_fetch
    rr.ddg_results = multi_search
    products = json.loads(rr.SOURCE.read_text(encoding="utf-8"))
    staged = json.loads(STAGING.read_text(encoding="utf-8")) if STAGING.exists() else []
    dedupe_universe = products + staged + tombstone_dedupe_rows()

    previous_state = (
        json.loads(rr.STATE.read_text(encoding="utf-8"))
        if rr.STATE.exists()
        else {}
    )
    candidates = [item for item in products if item.get("status") == "보류"]
    candidates.sort(key=pending_priority)
    campaign = (
        json.loads(ING_CAMPAIGN.read_text(encoding="utf-8"))
        if ING_CAMPAIGN.exists()
        else None
    )
    selected, pending_available, cursor_start, cursor_next, selection_mode = (
        select_pending_batch(candidates, previous_state, campaign)
    )
    selected_ids = [str(item.get("id", "")) for item in selected]
    before_status = {
        str(item.get("id", "")): str(item.get("status", ""))
        for item in selected
    }
    before_records = {
        str(item.get("id", "")): json.dumps(
            item, ensure_ascii=False, sort_keys=True
        )
        for item in selected
    }
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
            lambda category: safe_discover(category, dedupe_universe),
            categories,
        ))

    raw_candidates_found = sum(len(batch) for batch in batches)
    seen_urls = {
        url
        for item in dedupe_universe
        for url in list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", []))
    }
    seen_keys = {normalized_key(item) for item in dedupe_universe}
    per_category = {category: 0 for category in categories}
    discovered: list[dict] = []
    for batch in batches:
        for item in sorted(batch, key=lambda value: str(value.get("id", ""))):
            category = str(item.get("category", ""))
            url = next(
                iter(list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", []))),
                "",
            )
            key = normalized_key(item)
            if (
                not exact_candidate_shape(item)
                or not url
                or url in seen_urls
                or key in seen_keys
                or per_category.get(category, 0) >= NEW_PER_CATEGORY
                or len(discovered) >= NEW_TOTAL_LIMIT
            ):
                continue
            discovered.append(item)
            seen_urls.add(url)
            seen_keys.add(key)
            per_category[category] = per_category.get(category, 0) + 1
    staged.extend(discovered)

    successful_revalidations = sum(
        bool(item.get("checked")) and not item.get("errors") for item in audits
    )
    completed_revalidations = sum(bool(item.get("checked")) for item in audits)
    error_count = sum(bool(item.get("errors")) for item in audits)
    changed_ids = [
        str(item.get("id", ""))
        for item in selected
        if before_records.get(str(item.get("id", "")))
        != json.dumps(item, ensure_ascii=False, sort_keys=True)
    ]
    transitions: list[dict[str, str]] = []
    transition_counts: Counter[str] = Counter()
    for item in selected:
        product_id = str(item.get("id", ""))
        previous = before_status.get(product_id, "")
        current = str(item.get("status", ""))
        if previous == current:
            continue
        transition = f"{previous}->{current}"
        transition_counts[transition] += 1
        transitions.append({"id": product_id, "from": previous, "to": current})

    candidate_totals = Counter(
        str(item.get("category", ""))
        for item in staged
        if str(item.get("id", "")).startswith("DISC-")
        and item.get("status") == "보류"
    )
    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    included_transitions = sum(item["to"] == "포함" for item in transitions)
    canonical_text = json.dumps(products, ensure_ascii=False, indent=2) + "\n"
    staging_text = json.dumps(staged, ensure_ascii=False, indent=2) + "\n"
    new_state = {
        "metricsVersion": 2,
        "lastRun": run_at,
        "qualityMode": QUALITY,
        "executionMode": "alternate-url-bounded-parallel",
        "selectionMode": selection_mode,
        "scheduleTarget": "매시간 1회",
        "pendingLimit": PENDING_LIMIT,
        "newCandidateLimit": NEW_TOTAL_LIMIT,
        "currentCategories": categories,
        "nextCategory": rr.CATEGORIES[0],
        "categoryIndex": 0,
        "pendingAvailableBeforeRun": pending_available,
        "canonicalPendingBeforeRun": len(candidates),
        "pendingCursorStart": cursor_start,
        "pendingCursorNext": cursor_next,
        "pendingSelected": len(audits),
        "pendingSelectedIds": selected_ids,
        "revalidationAttempts": len(audits),
        "revalidationCompleted": completed_revalidations,
        "successfulRevalidations": successful_revalidations,
        "effectiveRevalidations": successful_revalidations,
        "revalidationErrorCount": error_count,
        "recordsChangedThisRun": len(changed_ids),
        "recordsChangedIds": changed_ids,
        "statusTransitionsThisRun": dict(sorted(transition_counts.items())),
        "researchOutcome": (
            "status-resolved" if transitions
            else "partial-evidence-only" if changed_ids
            else "no-verified-progress"
        ),
        "includedThisRun": included_transitions,
        "includedTotalAfterRun": sum(
            item.get("status") == "포함" for item in products
        ),
        "rawCandidatesFoundThisRun": raw_candidates_found,
        "rawNewCandidates": raw_candidates_found,
        "newCandidatesStagedBeforeSanitizationThisRun": len(discovered),
        "newCandidatesAcceptedBeforeSanitizationThisRun": len(discovered),
        "newCandidates": len(discovered),
        "candidateIdsDiscoveredThisRun": [
            str(item.get("id", "")) for item in discovered
        ],
        "newCandidatesByCategory": per_category,
        "candidateTotalAfterRun": sum(candidate_totals.values()),
        "candidateTotalsByCategoryAfterRun": {
            category: candidate_totals.get(category, 0) for category in categories
        },
        "errors": error_count,
        "totalProducts": len(products),
        "canonicalShaAfterRun": hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
        "stagingShaAfterRun": hashlib.sha256(staging_text.encode("utf-8")).hexdigest(),
    }
    rr.SOURCE.write_text(
        canonical_text,
        encoding="utf-8",
    )
    STAGING.write_text(
        staging_text,
        encoding="utf-8",
    )
    rr.STATE.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rr.AUDIT.write_text(
        json.dumps(
            {
                "state": new_state,
                "statusTransitions": transitions,
                "products": audits,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(new_state, ensure_ascii=False))


if __name__ == "__main__":
    main()
