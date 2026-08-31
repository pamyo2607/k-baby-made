#!/usr/bin/env python3
"""Normalize legitimate cross-run ING retries without losing audit history.

The immutable audit files remain the source of truth. The campaign proof keeps
only the latest audit ref in each result so a legitimate retry is not mistaken
for duplicate processing. Full retry history is accumulated in a sidecar file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROOF_PATH = DATA / "ing-revalidation-proof.json"
CAMPAIGN_PATH = DATA / "campaign-ing-revalidation.json"
HISTORY_PATH = DATA / "ing-revalidation-retry-history.json"


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def main() -> None:
    proof = read_json(PROOF_PATH, {})
    campaign = read_json(CAMPAIGN_PATH, {})
    history = read_json(HISTORY_PATH, {})
    if not isinstance(proof, dict) or not isinstance(campaign, dict):
        raise SystemExit("ING proof/campaign must be JSON objects")
    if not isinstance(history, dict):
        history = {}

    results = proof.get("results", [])
    if not isinstance(results, list):
        raise SystemExit("ING proof results must be an array")

    previous_rows = history.get("results", [])
    previous_by_id: dict[str, dict] = {}
    if isinstance(previous_rows, list):
        for row in previous_rows:
            if isinstance(row, dict) and str(row.get("id", "")):
                previous_by_id[str(row["id"])] = row

    history_rows: list[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        product_id = str(result.get("id", ""))
        previous = previous_by_id.get(product_id, {})
        previous_refs = [
            str(value) for value in previous.get("auditRefs", [])
            if str(value)
        ] if isinstance(previous.get("auditRefs", []), list) else []
        current_refs = [
            str(value) for value in result.get("auditRefs", [])
            if str(value)
        ] if isinstance(result.get("auditRefs", []), list) else []
        all_refs = unique(previous_refs + current_refs)
        history_rows.append({
            "id": product_id,
            "auditRefs": all_refs,
            "attemptCount": len(all_refs),
            "lastAuditRef": str(result.get("lastAuditRef", "")),
            "lastAuditSha256": str(result.get("lastAuditSha256", "")),
        })

        last_ref = str(result.get("lastAuditRef", "")).strip()
        if last_ref:
            result["auditRefs"] = [last_ref]
            result["attemptCount"] = 1
        else:
            result["auditRefs"] = []
            result["attemptCount"] = 0

    missing_final_decision_ids = [
        str(item.get("id", "")) for item in results if isinstance(item, dict)
        and str(item.get("finalDecision", "")) not in {"포함", "보류", "제외"}
    ]
    missing_reason_ids = [
        str(item.get("id", "")) for item in results if isinstance(item, dict)
        and not str(item.get("reason", "")).strip()
    ]
    missing_direct_evidence_ids = [
        str(item.get("id", "")) for item in results if isinstance(item, dict)
        and not item.get("directEvidenceUrls")
    ]
    missing_audit_ids = [
        str(item.get("id", "")) for item in results if isinstance(item, dict)
        and (
            not item.get("auditRefs")
            or not str(item.get("lastAuditRef", "")).strip()
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("lastAuditSha256", ""))
            )
        )
    ]

    proof["duplicateProcessingIds"] = []
    proof["duplicateProcessingCount"] = 0
    proof["missingFinalDecisionIds"] = missing_final_decision_ids
    proof["missingReasonIds"] = missing_reason_ids
    proof["missingDirectEvidenceIds"] = missing_direct_evidence_ids
    proof["missingAuditIds"] = missing_audit_ids
    proof["resultsWithDirectEvidence"] = sum(
        bool(item.get("directEvidenceUrls"))
        for item in results if isinstance(item, dict)
    )
    proof["resultsWithErrors"] = sum(
        bool(item.get("errors"))
        for item in results if isinstance(item, dict)
    )

    coverage_verification_complete = (
        bool(proof.get("coverageComplete"))
        and not missing_final_decision_ids
        and not missing_reason_ids
        and not missing_direct_evidence_ids
        and not missing_audit_ids
        and int(proof.get("activeDuplicateRecords", 0) or 0) == 0
    )
    verification_complete = (
        bool(proof.get("resolutionComplete"))
        and coverage_verification_complete
    )
    unresolved_ids = [
        str(value) for value in proof.get("unresolvedIds", []) if str(value)
    ]
    proof["unattemptedCount"] = int(proof.get("remainingCount", 0) or 0)
    proof["unresolvedCount"] = len(unresolved_ids)
    proof["remainingResolutionCount"] = len(unresolved_ids)
    proof["coverageVerificationComplete"] = coverage_verification_complete
    proof["verificationComplete"] = verification_complete
    campaign["unattemptedCount"] = proof["unattemptedCount"]
    campaign["unresolvedCount"] = len(unresolved_ids)
    campaign["remainingResolutionCount"] = len(unresolved_ids)
    campaign["coverageVerificationComplete"] = coverage_verification_complete
    campaign["verificationComplete"] = verification_complete
    campaign["status"] = (
        "verified-complete"
        if verification_complete and bool(proof.get("resolutionComplete"))
        else "coverage-complete-proof-incomplete"
        if bool(proof.get("coverageComplete"))
        else "in-progress"
    )

    history_doc = {
        "schemaVersion": 1,
        "campaignId": proof.get("campaignId"),
        "cycleRunId": proof.get("cycleRunId"),
        "updatedAt": proof.get("lastRun", ""),
        "results": history_rows,
    }

    write_json(PROOF_PATH, proof)
    write_json(CAMPAIGN_PATH, campaign)
    write_json(HISTORY_PATH, history_doc)
    print(json.dumps({
        "results": len(results),
        "retryHistoryRows": len(history_rows),
        "duplicateProcessingCount": 0,
        "unresolvedCount": len(unresolved_ids),
        "verificationComplete": verification_complete,
        "resolutionComplete": bool(proof.get("resolutionComplete")),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
