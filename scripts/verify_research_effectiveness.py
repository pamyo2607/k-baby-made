#!/usr/bin/env python3
"""Verify per-run research scope and metrics without treating totals as output."""
from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data/research-last-run.json"
SOURCE = ROOT / "data/master-products.json"
STAGING = ROOT / "data/discovered-candidate-staging.json"
REPORT = ROOT / "data/research-effectiveness-report.json"
CATEGORIES = ["완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀"]
MAX_PENDING = 50
MAX_NEW_CANDIDATES = 30
MAX_NEW_PER_CATEGORY = 5


def integer(state: dict, key: str, default: int = 0) -> int:
    try:
        return int(state.get(key, default))
    except (TypeError, ValueError):
        return -1


def exact_candidate_row(item: dict) -> bool:
    brand = str(item.get("brand", "")).strip()
    name = str(item.get("name", "")).strip()
    if (
        str(item.get("category", "")) not in CATEGORIES
        or brand in {"", "브랜드 확인 중", "확인 중", "미상"}
        or len(re.sub(r"[^0-9a-z가-힣]+", "", name.lower())) < 8
    ):
        return False
    for raw_url in list(item.get("officialUrls", [])) + list(item.get("evidenceUrls", [])):
        parsed = urlparse(str(raw_url))
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


def main() -> None:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    state = dict(audit.get("state", {}))
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    staged = json.loads(STAGING.read_text(encoding="utf-8")) if STAGING.exists() else []
    canonical_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    staging_sha = hashlib.sha256(STAGING.read_bytes()).hexdigest() if STAGING.exists() else ""
    recorded_canonical_sha = str(state.get("canonicalShaAfterRun", ""))
    recorded_staging_sha = str(state.get("stagingShaAfterRun", ""))
    snapshot_matches_canonical = bool(recorded_canonical_sha) and recorded_canonical_sha == canonical_sha
    snapshot_matches_staging = bool(recorded_staging_sha) and recorded_staging_sha == staging_sha
    candidate_by_id = {str(item.get("id", "")): item for item in staged}
    errors: list[str] = []

    pending_limit = integer(state, "pendingLimit", MAX_PENDING)
    pending_available = integer(state, "pendingAvailableBeforeRun")
    pending_selected = integer(state, "pendingSelected")
    selected_ids = [str(value) for value in state.get("pendingSelectedIds", [])]
    attempts = integer(state, "revalidationAttempts")
    completed = integer(state, "revalidationCompleted")
    successful = integer(state, "successfulRevalidations")
    audit_error_count = integer(state, "revalidationErrorCount")
    audited_products = list(audit.get("products", []))
    audited_ids = [str(item.get("id", "")) for item in audited_products]

    if not 0 <= pending_limit <= MAX_PENDING:
        errors.append(f"pendingLimit={pending_limit} exceeds {MAX_PENDING}")
    expected_selected = min(max(0, pending_available), max(0, pending_limit))
    if pending_selected != expected_selected:
        errors.append(
            f"pendingSelected={pending_selected} expected={expected_selected}"
        )
    if len(selected_ids) != pending_selected or len(set(selected_ids)) != len(selected_ids):
        errors.append("pendingSelectedIds must be unique and match pendingSelected")
    if attempts != pending_selected or len(audited_products) != attempts:
        errors.append("revalidation attempts/audit rows do not match pending selection")
    if audited_ids != selected_ids:
        errors.append("audit product IDs/order must match the selected pending IDs")
    if not 0 <= successful <= completed <= attempts:
        errors.append(
            "successful/completed/attempted revalidation counts are inconsistent"
        )
    actual_audit_errors = sum(bool(item.get("errors")) for item in audited_products)
    actual_completed = sum(bool(item.get("checked")) for item in audited_products)
    actual_successful = sum(
        bool(item.get("checked")) and not item.get("errors")
        for item in audited_products
    )
    if completed != actual_completed or successful != actual_successful:
        errors.append("successful/completed metrics do not match audit rows")
    if audit_error_count != actual_audit_errors:
        errors.append(
            f"revalidationErrorCount={audit_error_count} actual={actual_audit_errors}"
        )
    if integer(state, "effectiveRevalidations") != successful:
        errors.append("effectiveRevalidations must equal successfulRevalidations")
    changed_ids = [str(value) for value in state.get("recordsChangedIds", [])]
    if (
        integer(state, "recordsChangedThisRun") != len(changed_ids)
        or len(changed_ids) != len(set(changed_ids))
    ):
        errors.append("recordsChangedIds must be unique and match its per-run count")

    categories = [str(value) for value in state.get("currentCategories", [])]
    if categories != CATEGORIES:
        errors.append("all six research categories must be attempted exactly once")

    candidate_limit = integer(state, "newCandidateLimit", MAX_NEW_CANDIDATES)
    raw_found = integer(state, "rawCandidatesFoundThisRun")
    accepted_before_sanitize = integer(
        state, "newCandidatesAcceptedBeforeSanitizationThisRun"
    )
    accepted = integer(state, "newCandidatesThisRun", integer(state, "newCandidates"))
    rejected = integer(state, "newCandidatesRejectedBySanitizationThisRun")
    accepted_ids = [
        str(value) for value in state.get("candidateIdsAcceptedThisRun", [])
    ]
    discovered_ids = [
        str(value) for value in state.get("candidateIdsDiscoveredThisRun", [])
    ]
    accepted_by_category = {
        str(key): int(value)
        for key, value in dict(state.get("newCandidatesByCategory", {})).items()
    }

    if not 0 <= candidate_limit <= MAX_NEW_CANDIDATES:
        errors.append(
            f"newCandidateLimit={candidate_limit} exceeds {MAX_NEW_CANDIDATES}"
        )
    if not 0 <= accepted <= accepted_before_sanitize <= raw_found:
        errors.append("raw/pre-sanitize/final candidate counts are inconsistent")
    if accepted_before_sanitize > candidate_limit:
        errors.append(
            "newCandidatesAcceptedBeforeSanitizationThisRun="
            f"{accepted_before_sanitize} exceeds {candidate_limit}"
        )
    if accepted > candidate_limit:
        errors.append(f"newCandidatesThisRun={accepted} exceeds {candidate_limit}")
    if (
        len(discovered_ids) != accepted_before_sanitize
        or len(discovered_ids) != len(set(discovered_ids))
    ):
        errors.append(
            "candidateIdsDiscoveredThisRun must be unique and match pre-sanitize output"
        )
    if rejected != accepted_before_sanitize - accepted:
        errors.append("candidate sanitation rejection metric is inconsistent")
    if len(accepted_ids) != accepted or len(set(accepted_ids)) != len(accepted_ids):
        errors.append("candidateIdsAcceptedThisRun must be unique and match output")
    if not set(accepted_ids).issubset(discovered_ids):
        errors.append("accepted candidate IDs must be a subset of discovered IDs")
    if sum(accepted_by_category.values()) != accepted:
        errors.append("newCandidatesByCategory must sum to this run's output")
    if any(value > MAX_NEW_PER_CATEGORY for value in accepted_by_category.values()):
        errors.append(
            f"newCandidatesByCategory exceeds per-category cap {MAX_NEW_PER_CATEGORY}"
        )
    missing_candidates = [
        product_id
        for product_id in accepted_ids
        if product_id not in candidate_by_id
        or not product_id.startswith("DISC-")
        or candidate_by_id[product_id].get("status") != "보류"
        or not exact_candidate_row(candidate_by_id[product_id])
    ]
    if missing_candidates:
        errors.append(f"accepted candidate rows missing/invalid: {missing_candidates}")
    actual_accepted_by_category = Counter(
        str(candidate_by_id[product_id].get("category", ""))
        for product_id in accepted_ids
        if product_id in candidate_by_id
    )
    if any(
        accepted_by_category.get(category, 0)
        != actual_accepted_by_category.get(category, 0)
        for category in CATEGORIES
    ):
        errors.append("newCandidatesByCategory does not match accepted candidate rows")

    transitions = dict(state.get("statusTransitionsThisRun", {}))
    transition_rows = list(audit.get("statusTransitions", []))
    actual_transition_counts = Counter(
        f"{item.get('from', '')}->{item.get('to', '')}" for item in transition_rows
    )
    if transitions != dict(sorted(actual_transition_counts.items())):
        errors.append("status transition metrics do not match audit transition rows")
    included_transitions = sum(
        int(value) for key, value in transitions.items() if str(key).endswith("->포함")
    )
    if integer(state, "includedThisRun") != included_transitions:
        errors.append("includedThisRun must count status transitions, not total rows")
    expected_outcome = (
        "status-resolved" if transitions
        else "partial-evidence-only" if changed_ids
        else "no-verified-progress"
    )
    if recorded_canonical_sha and str(state.get("researchOutcome", "")) != expected_outcome:
        errors.append(
            f"researchOutcome={state.get('researchOutcome')} expected={expected_outcome}"
        )
    included_total = sum(item.get("status") == "포함" for item in products)
    if snapshot_matches_canonical and integer(state, "includedTotalAfterRun") != included_total:
        errors.append("includedTotalAfterRun does not match canonical data")

    cumulative_candidates = [
        item for item in staged
        if str(item.get("id", "")).startswith("DISC-") and item.get("status") == "보류"
    ]
    if any(str(item.get("id", "")).startswith("DISC-") for item in products):
        errors.append("unverified DISC candidate leaked into canonical products")
    staged_ids = [str(item.get("id", "")) for item in cumulative_candidates]
    if not all(staged_ids) or len(staged_ids) != len(set(staged_ids)):
        errors.append("staged candidate IDs must be nonblank and unique")
    cumulative_by_category = Counter(
        str(item.get("category", "")) for item in cumulative_candidates
    )
    if snapshot_matches_staging and integer(state, "candidateTotalAfterRun") != len(cumulative_candidates):
        errors.append("candidateTotalAfterRun does not match canonical data")
    if snapshot_matches_canonical and integer(state, "totalProducts") != len(products):
        errors.append("totalProducts does not match canonical data")

    report = {
        "passed": not errors,
        "scopeExecuted": not errors,
        "metricsVersion": state.get("metricsVersion"),
        "pendingLimit": pending_limit,
        "pendingAvailableBeforeRun": pending_available,
        "pendingSelectedThisRun": pending_selected,
        "revalidationAttemptsThisRun": attempts,
        "revalidationCompletedThisRun": completed,
        "successfulRevalidationsThisRun": successful,
        "revalidationErrorsThisRun": audit_error_count,
        "recordsChangedThisRun": integer(state, "recordsChangedThisRun"),
        "statusTransitionsThisRun": transitions,
        "researchOutcome": expected_outcome,
        "includedTransitionsThisRun": included_transitions,
        "includedTotalAfterRun": integer(state, "includedTotalAfterRun"),
        "snapshotMatchesCanonical": snapshot_matches_canonical,
        "snapshotMatchesStaging": snapshot_matches_staging,
        "newCandidateLimit": candidate_limit,
        "rawCandidatesFoundThisRun": raw_found,
        "candidatesAcceptedBeforeSanitizationThisRun": accepted_before_sanitize,
        "newCandidatesThisRun": accepted,
        "newCandidatesRejectedBySanitizationThisRun": rejected,
        "newCandidatesByCategoryThisRun": accepted_by_category,
        "candidateTotalAfterRun": len(cumulative_candidates),
        "candidateTotalsByCategoryAfterRun": {
            category: cumulative_by_category.get(category, 0)
            for category in CATEGORIES
        },
        "errors": errors,
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()
