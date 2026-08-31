#!/usr/bin/env python3
"""Persist one immutable revalidation audit and update the ING campaign proof.

This script deliberately distinguishes attempted coverage from final resolution.
A pending row can be recorded as attempted, but it is never counted as resolved
until it has an included/excluded decision, a reason, and direct evidence URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


class CampaignUpdateError(RuntimeError):
    pass


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignUpdateError(f"cannot read {path}: {exc}") from exc


def document_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_replace(documents: dict[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for path, content in documents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                staged[path] = Path(handle.name)
        for path, temporary in staged.items():
            os.replace(temporary, path)
        for path, content in documents.items():
            if path.read_bytes() != content:
                raise CampaignUpdateError(f"readback mismatch: {path}")
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()


def direct_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    path = parsed.path.lower()
    return not any(
        blocked in path
        for blocked in (
            "/search",
            "/category",
            "/categories",
            "/certificationsearch",
            "/itemsearch",
        )
    )


def evidence_urls(product: dict) -> list[str]:
    values: list[object] = []
    for field in (
        "officialUrls",
        "saleUrls",
        "revalidationEvidenceUrls",
        "evidenceUrls",
    ):
        candidate = product.get(field, [])
        if isinstance(candidate, list):
            values.extend(candidate)
    safety_url = product.get("safetyKoreaSearchUrl")
    if safety_url:
        values.append(safety_url)
    for certification in product.get("certifications", []):
        if isinstance(certification, dict) and certification.get("url"):
            values.append(certification["url"])
    return list(dict.fromkeys(
        str(value).strip() for value in values if direct_url(value)
    ))


def run_slug(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignUpdateError(f"invalid audit run timestamp: {value!r}") from exc
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def update(root: Path) -> dict:
    data = root / "data"
    campaign_path = data / "campaign-ing-revalidation.json"
    current_audit_path = data / "research-last-run.json"
    canonical_path = data / "master-products.json"
    proof_path = data / "ing-revalidation-proof.json"

    campaign = read_json(campaign_path)
    current_audit = read_json(current_audit_path)
    canonical = read_json(canonical_path)
    if not isinstance(campaign, dict) or not isinstance(current_audit, dict):
        raise CampaignUpdateError("campaign and current audit must be objects")
    if not isinstance(canonical, list):
        raise CampaignUpdateError("canonical products must be an array")

    target_ids = [str(value) for value in campaign.get("targetIdsSnapshot", [])]
    if not target_ids or not all(target_ids) or len(target_ids) != len(set(target_ids)):
        raise CampaignUpdateError("campaign target IDs must be nonblank and unique")

    canonical_ids = [str(item.get("id", "")) for item in canonical if isinstance(item, dict)]
    if len(canonical_ids) != len(canonical) or len(canonical_ids) != len(set(canonical_ids)):
        raise CampaignUpdateError("canonical IDs must be nonblank and unique")
    canonical_by_id = {str(item["id"]): item for item in canonical}
    missing_canonical = [value for value in target_ids if value not in canonical_by_id]
    if missing_canonical:
        raise CampaignUpdateError(
            f"campaign targets missing from canonical: {missing_canonical}"
        )

    state = current_audit.get("state", {})
    audit_rows = current_audit.get("products", [])
    if not isinstance(state, dict) or not isinstance(audit_rows, list):
        raise CampaignUpdateError("research audit state/products structure mismatch")
    audit_ids = [
        str(item.get("id", "")) for item in audit_rows if isinstance(item, dict)
    ]
    selected_ids = [str(value) for value in state.get("pendingSelectedIds", [])]
    if (
        len(audit_ids) != len(audit_rows)
        or audit_ids != selected_ids
        or not all(audit_ids)
        or len(audit_ids) != len(set(audit_ids))
        or not set(audit_ids).issubset(target_ids)
    ):
        raise CampaignUpdateError(
            "audit IDs must be unique, ordered like pendingSelectedIds, and in the campaign"
        )

    run_at = str(state.get("lastRun", ""))
    slug = run_slug(run_at)
    immutable_relative = Path("data/revalidation-audits") / f"{slug}.json"
    immutable_path = root / immutable_relative
    immutable_doc = {
        "schemaVersion": 1,
        "campaignId": campaign.get("campaignId"),
        "cycleRunId": campaign.get("cycleRunId"),
        "runAt": run_at,
        "workflowRunId": os.environ.get("GITHUB_RUN_ID", ""),
        "workflowRunUrl": (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', 'pamyo2607/k-baby-made')}/"
            f"actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
            if os.environ.get("GITHUB_RUN_ID")
            else ""
        ),
        "sourceCommit": os.environ.get("GITHUB_SHA", ""),
        "audit": current_audit,
    }
    immutable_content = document_bytes(immutable_doc)
    immutable_sha = sha256_bytes(immutable_content)

    previous_proof: dict = {}
    if proof_path.exists():
        loaded = read_json(proof_path)
        if not isinstance(loaded, dict):
            raise CampaignUpdateError("existing ING proof must be an object")
        previous_proof = loaded
    existing_results = previous_proof.get("results", [])
    if not isinstance(existing_results, list):
        raise CampaignUpdateError("existing ING proof results must be an array")
    existing_result_ids = [
        str(item.get("id", "")) for item in existing_results if isinstance(item, dict)
    ]
    if (
        len(existing_result_ids) != len(existing_results)
        or len(existing_result_ids) != len(set(existing_result_ids))
        or not set(existing_result_ids).issubset(target_ids)
    ):
        raise CampaignUpdateError("existing ING proof results are invalid or duplicated")
    result_by_id = {
        str(item["id"]): dict(item) for item in existing_results
    }

    audit_ref = immutable_relative.as_posix()
    for audit_row in audit_rows:
        product_id = str(audit_row["id"])
        product = canonical_by_id[product_id]
        urls = evidence_urls(product)
        reason = str(product.get("reason", "")).strip()
        decision = str(product.get("status", ""))
        resolved = decision in {"포함", "제외"} and bool(reason) and bool(urls)
        previous = result_by_id.get(product_id, {})
        audit_refs = [str(value) for value in previous.get("auditRefs", [])]
        if audit_ref not in audit_refs:
            audit_refs.append(audit_ref)
        result_by_id[product_id] = {
            "id": product_id,
            "finalDecision": decision,
            "resolved": resolved,
            "checked": bool(audit_row.get("checked")),
            "changed": bool(audit_row.get("changed")),
            "directEvidenceUrls": urls,
            "reason": reason,
            "errors": [str(value) for value in audit_row.get("errors", [])],
            "checkedAt": str(product.get("checkedAt", "")),
            "attemptCount": len(audit_refs),
            "auditRefs": audit_refs,
            "lastAuditRef": audit_ref,
            "lastAuditSha256": immutable_sha,
        }

    ordered_results = [result_by_id[value] for value in target_ids if value in result_by_id]
    attempted_ids = [item["id"] for item in ordered_results]
    attempted_set = set(attempted_ids)
    remaining_ids = [value for value in target_ids if value not in attempted_set]
    unresolved_ids = [
        value
        for value in target_ids
        if str(canonical_by_id[value].get("status", "")) not in {"포함", "제외"}
        or not str(canonical_by_id[value].get("reason", "")).strip()
        or not evidence_urls(canonical_by_id[value])
    ]
    final_counts = Counter(
        str(canonical_by_id[value].get("status", "")) for value in target_ids
    )
    active_duplicate_records = sum(
        bool(item.get("duplicateOf")) for item in canonical if isinstance(item, dict)
    )
    coverage_complete = not remaining_ids
    resolution_complete = coverage_complete and not unresolved_ids
    missing_final_decision_ids = [
        item["id"] for item in ordered_results
        if str(item.get("finalDecision", "")) not in {"포함", "보류", "제외"}
    ]
    missing_reason_ids = [
        item["id"] for item in ordered_results
        if not str(item.get("reason", "")).strip()
    ]
    missing_direct_evidence_ids = [
        item["id"] for item in ordered_results
        if not item.get("directEvidenceUrls")
    ]
    missing_audit_ids = [
        item["id"] for item in ordered_results
        if not item.get("auditRefs")
        or not str(item.get("lastAuditRef", "")).strip()
        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("lastAuditSha256", "")))
    ]
    duplicate_processing_ids = [
        item["id"] for item in ordered_results
        if int(item.get("attemptCount", 0) or 0) > 1
    ]
    coverage_verification_complete = (
        coverage_complete
        and not missing_final_decision_ids
        and not missing_reason_ids
        and not missing_direct_evidence_ids
        and not missing_audit_ids
        and not duplicate_processing_ids
        and active_duplicate_records == 0
    )
    # ``coverageComplete`` only proves that every target was attempted at
    # least once.  The user-facing campaign must never report full
    # verification while any target is still pending.  Keep the coverage
    # proof as a separate diagnostic and reserve ``verificationComplete`` for
    # a fully resolved campaign.
    verification_complete = resolution_complete and coverage_verification_complete
    completed_batch_count = len({
        ref for item in ordered_results for ref in item.get("auditRefs", [])
    })

    campaign.update({
        "attemptedIds": attempted_ids,
        "remainingIds": remaining_ids,
        "currentBatchIds": audit_ids,
        "selectedIds": audit_ids,
        "lastBatchIds": audit_ids,
        "lastAuditRef": audit_ref,
        "lastAuditSha256": immutable_sha,
        "completedBatchCount": completed_batch_count,
        "batchIndex": completed_batch_count + 1,
        "cursorNext": int(state.get("pendingCursorNext", 0) or 0),
        "unattemptedCount": len(remaining_ids),
        "unresolvedCount": len(unresolved_ids),
        "remainingResolutionCount": len(unresolved_ids),
        "coverageComplete": coverage_complete,
        "resolutionComplete": resolution_complete,
        "coverageVerificationComplete": coverage_verification_complete,
        "verificationComplete": verification_complete,
        "status": (
            "verified-complete"
            if verification_complete
            else "coverage-complete-proof-incomplete"
            if coverage_complete
            else "in-progress"
        ),
        "updatedAt": run_at,
    })

    proof = {
        "schemaVersion": 1,
        "campaignId": campaign.get("campaignId"),
        "cycleRunId": campaign.get("cycleRunId"),
        "targetCount": len(target_ids),
        "targetIdsSha256": sha256_bytes(document_bytes(target_ids)),
        "attemptedCount": len(attempted_ids),
        "remainingCount": len(remaining_ids),
        "unattemptedCount": len(remaining_ids),
        "unresolvedCount": len(unresolved_ids),
        "remainingResolutionCount": len(unresolved_ids),
        "resolvedCount": len(target_ids) - len(unresolved_ids),
        "includedCount": final_counts["포함"],
        "pendingCount": final_counts["보류"],
        "excludedCount": final_counts["제외"],
        "missingTargetIds": remaining_ids,
        "unresolvedIds": unresolved_ids,
        "duplicateResultIds": [],
        "duplicateProcessingIds": duplicate_processing_ids,
        "duplicateProcessingCount": len(duplicate_processing_ids),
        "activeDuplicateRecords": active_duplicate_records,
        "missingFinalDecisionIds": missing_final_decision_ids,
        "missingReasonIds": missing_reason_ids,
        "missingDirectEvidenceIds": missing_direct_evidence_ids,
        "missingAuditIds": missing_audit_ids,
        "resultsWithDirectEvidence": sum(
            bool(item.get("directEvidenceUrls")) for item in ordered_results
        ),
        "resultsWithErrors": sum(bool(item.get("errors")) for item in ordered_results),
        "coverageComplete": coverage_complete,
        "resolutionComplete": resolution_complete,
        "coverageVerificationComplete": coverage_verification_complete,
        "verificationComplete": verification_complete,
        "lastRun": run_at,
        "lastAuditRef": audit_ref,
        "lastAuditSha256": immutable_sha,
        "results": ordered_results,
    }

    documents = {
        immutable_path: immutable_content,
        campaign_path: document_bytes(campaign),
        proof_path: document_bytes(proof),
    }
    atomic_replace(documents)
    return {
        "status": proof["verificationComplete"] and "completed" or "in-progress",
        "targetCount": proof["targetCount"],
        "attemptedCount": proof["attemptedCount"],
        "remainingCount": proof["remainingCount"],
        "unattemptedCount": proof["unattemptedCount"],
        "unresolvedCount": proof["unresolvedCount"],
        "remainingResolutionCount": proof["remainingResolutionCount"],
        "resolvedCount": proof["resolvedCount"],
        "includedCount": proof["includedCount"],
        "pendingCount": proof["pendingCount"],
        "excludedCount": proof["excludedCount"],
        "duplicateResultIds": proof["duplicateResultIds"],
        "duplicateProcessingCount": proof["duplicateProcessingCount"],
        "missingDirectEvidenceCount": len(proof["missingDirectEvidenceIds"]),
        "activeDuplicateRecords": proof["activeDuplicateRecords"],
        "lastAuditRef": proof["lastAuditRef"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = update(args.root.resolve())
    except CampaignUpdateError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
