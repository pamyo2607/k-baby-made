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
STAGING = ROOT / "data/discovered-candidate-staging.json"
STATE = ROOT / "data/continuous-research-state.json"
AUDIT = ROOT / "data/research-last-run.json"
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
    "구강·치발기": ("치발기", "구강", "잇몸", "티더", "teether"),
    "턱받이": ("턱받이", "빕", "bib"),
    "수유용품": ("수유", "젖병", "분유", "빨대컵", "수유쿠션", "유축"),
    "이유식·식기": ("이유식", "흡착식판", "스푼", "유아식기", "아기식기", "이유식기"),
    "위생·기저귀": ("위생", "물티슈", "기저귀", "손수건", "목욕", "세정", "면봉"),
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
    for raw in list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", [])):
        decoded = decode_bing_target(str(raw))
        if direct_product_url(decoded):
            return decoded
    return ""


def update_run_metrics(
    state: dict,
    kept: list[dict],
    cumulative_counts: dict[str, int],
    removed_count: int,
    canonical_count: int,
) -> None:
    """Keep per-run candidate metrics separate from cumulative inventory."""
    discovered_ids = {
        str(value) for value in state.get("candidateIdsDiscoveredThisRun", [])
    }
    accepted = [
        item for item in kept if str(item.get("id", "")) in discovered_ids
    ]
    accepted_ids = [str(item.get("id", "")) for item in accepted]
    accepted_counts = Counter(str(item.get("category", "")) for item in accepted)
    categories = [str(value) for value in state.get("currentCategories", [])]
    per_run_counts = {
        category: int(accepted_counts.get(category, 0)) for category in categories
    }
    raw_found = int(
        state.get(
            "rawCandidatesFoundThisRun",
            state.get("rawNewCandidates", len(discovered_ids)),
        )
    )

    state["rawCandidatesFoundThisRun"] = raw_found
    state["rawNewCandidates"] = raw_found
    state["newCandidatesAcceptedBeforeSanitizationThisRun"] = int(
        state.get("newCandidatesAcceptedBeforeSanitizationThisRun", len(discovered_ids))
    )
    state["newCandidatesRejectedBySanitizationThisRun"] = max(
        0, len(discovered_ids) - len(accepted_ids)
    )
    state["newCandidatesThisRun"] = len(accepted_ids)
    state["newCandidates"] = len(accepted_ids)
    state["newCandidatesByCategory"] = per_run_counts
    state["candidateIdsAcceptedThisRun"] = accepted_ids
    state["removedInvalidCandidates"] = removed_count
    state["candidateTotalAfterRun"] = sum(cumulative_counts.values())
    state["candidateTotalsByCategoryAfterRun"] = {
        category: int(cumulative_counts.get(category, 0)) for category in categories
    }
    state["totalProducts"] = canonical_count


def update_state_files(
    kept: list[dict],
    cumulative_counts: dict[str, int],
    removed_count: int,
    canonical_count: int,
) -> None:
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        update_run_metrics(state, kept, cumulative_counts, removed_count, canonical_count)
        STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if AUDIT.exists():
        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        state = audit.setdefault("state", {})
        update_run_metrics(state, kept, cumulative_counts, removed_count, canonical_count)
        AUDIT.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidates = json.loads(STAGING.read_text(encoding="utf-8")) if STAGING.exists() else []
    existing_keys = {
        product_key(item)
        for item in products
        if not str(item.get("id", "")).startswith("DISC-")
    }
    seen_discovered: set[str] = set()
    kept: list[dict] = []
    removed: list[dict] = []

    for item in candidates:
        pid = str(item.get("id", ""))
        is_candidate = pid.startswith("DISC-") and item.get("status") == "보류"
        if not is_candidate:
            removed.append({
                "id": pid,
                "category": item.get("category"),
                "brand": item.get("brand"),
                "name": item.get("name"),
                "key": product_key(item),
                "reasons": ["invalid_staging_row"],
            })
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

        item["officialUrls"] = [resolved_url] + [
            str(value)
            for value in list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", []))
            if decode_bing_target(str(value)) != resolved_url
            and "bing.com/ck/" not in str(value)
        ]
        item["saleUrls"] = list(dict.fromkeys(
            [resolved_url] + [str(value) for value in item.get("saleUrls", [])]
        ))
        seen_discovered.add(key)
        kept.append(item)

    remaining_counts = Counter(
        item.get("category")
        for item in kept
        if str(item.get("id", "")).startswith("DISC-")
        and item.get("status") == "보류"
    )
    valid_counts = {str(key): int(value) for key, value in remaining_counts.items()}
    STAGING.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    state = (
        json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    )
    discovered_ids = {
        str(value) for value in state.get("candidateIdsDiscoveredThisRun", [])
    }
    accepted_run_ids = [
        str(item.get("id", ""))
        for item in kept
        if str(item.get("id", "")) in discovered_ids
    ]
    update_state_files(kept, valid_counts, len(removed), len(products))
    report = {
        "canonicalCountUnchanged": len(products),
        "inputCount": len(candidates),
        "outputCount": len(kept),
        "removedCount": len(removed),
        "candidateIdsDiscoveredThisRun": sorted(discovered_ids),
        "candidateIdsAcceptedThisRun": accepted_run_ids,
        "newCandidatesRejectedThisRun": max(
            0, len(discovered_ids) - len(accepted_run_ids)
        ),
        "remainingCandidateCount": sum(valid_counts.values()),
        "remainingCandidatesByCategory": valid_counts,
        "removed": removed,
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
