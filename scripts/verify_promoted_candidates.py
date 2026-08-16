#!/usr/bin/env python3
"""Cross-check campaign checkpoint, promotion ledger, canonical, and staging."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from promote_verified_candidates import (
    DATA,
    DEFAULT_CANONICAL,
    DEFAULT_CHECKPOINT,
    DEFAULT_LEDGER,
    DEFAULT_STAGING,
    DEFAULT_TOMBSTONES,
    ID_RE,
    PromotionError,
    audit_candidate_ids,
    campaign_id,
    campaign_target,
    checkpoint_promoted_ids,
    deleted_count,
    evidence_sha,
    file_sha,
    included_errors,
    ledger_promotions,
    read_json,
    resolve_repo_ref,
    sha_json,
    validate_ledger,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", help="must match checkpoint")
    parser.add_argument(
        "--require-target", action="store_true",
        help="require the campaign to have reached its exact target (50 normally)",
    )
    parser.add_argument("--root", type=Path, default=DATA.parent)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--tombstones", type=Path, default=DEFAULT_TOMBSTONES)
    return parser.parse_args()


def freeze_by_wave(checkpoint: dict[str, Any]) -> dict[int, dict[str, Any]]:
    freezes = checkpoint.get("promotionAuditFreezes", [])
    if not isinstance(freezes, list) or any(not isinstance(item, dict) for item in freezes):
        raise PromotionError("checkpoint promotionAuditFreezes must be an object list")
    result: dict[int, dict[str, Any]] = {}
    for item in freezes:
        try:
            wave = int(item.get("waveIndex"))
        except (TypeError, ValueError) as exc:
            raise PromotionError("promotion audit freeze has invalid waveIndex") from exc
        if wave in result:
            raise PromotionError(f"multiple promotion audit freezes for wave {wave}")
        result[wave] = item
    return result


def main() -> None:
    args = parse_args()
    canonical = read_json(args.canonical)
    staging = read_json(args.staging)
    ledger = read_json(args.ledger)
    checkpoint = read_json(args.checkpoint)
    tombstone_doc = read_json(args.tombstones, default={"records": []})
    if not isinstance(canonical, list) or not isinstance(staging, list):
        raise PromotionError("canonical and staging must be JSON arrays")
    if not isinstance(ledger, dict) or not isinstance(checkpoint, dict):
        raise PromotionError("ledger and checkpoint must be JSON objects")
    validate_ledger(ledger)
    campaign = campaign_id(checkpoint)
    if args.campaign_id and args.campaign_id != campaign:
        raise PromotionError("CLI campaign ID does not match checkpoint")

    canonical_ids = [str(item.get("id", "")) for item in canonical]
    if not all(canonical_ids) or len(canonical_ids) != len(set(canonical_ids)):
        raise PromotionError("canonical IDs must be nonblank and unique")
    if [item.get("sequence") for item in canonical] != list(range(1, len(canonical) + 1)):
        raise PromotionError("canonical sequence is not contiguous 1..N")
    staging_ids = [str(item.get("id", "")) for item in staging]
    if not all(staging_ids) or len(staging_ids) != len(set(staging_ids)):
        raise PromotionError("staging IDs must be nonblank and unique")
    if set(canonical_ids) & set(staging_ids):
        raise PromotionError("canonical and staging IDs overlap")

    entries = [item for item in ledger_promotions(ledger) if item.get("campaignId") == campaign]
    candidate_ids = [str(item.get("candidateId", "")) for item in entries]
    final_ids = [str(item.get("finalCanonicalId", "")) for item in entries]
    if not all(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise PromotionError("campaign ledger candidate IDs are blank or duplicated")
    if not all(final_ids) or len(final_ids) != len(set(final_ids)):
        raise PromotionError("campaign ledger final IDs are blank or duplicated")
    if any(not ID_RE.fullmatch(value) for value in final_ids):
        raise PromotionError("campaign ledger contains a malformed KBM final ID")
    if final_ids != checkpoint_promoted_ids(checkpoint):
        raise PromotionError("campaign ledger order differs from checkpoint promotions")

    baseline = checkpoint.get("promotionBaseline")
    if not isinstance(baseline, dict):
        raise PromotionError("promotion baseline is absent")
    baseline_ids = baseline.get("canonicalIds")
    if not isinstance(baseline_ids, list) or len(baseline_ids) != len(set(baseline_ids)):
        raise PromotionError("promotion baseline ID set is invalid")
    if baseline.get("canonicalIdsSha256") != sha_json(baseline_ids):
        raise PromotionError("promotion baseline ID SHA mismatch")
    if set(final_ids) & set(baseline_ids):
        raise PromotionError("a promoted final ID existed in the pre-promotion baseline")

    tombstones = tombstone_doc.get("records", []) if isinstance(tombstone_doc, dict) else []
    tombstone_ids = {
        str(value)
        for item in tombstones
        if isinstance(item, dict)
        for value in (item.get("deletedId", ""), item.get("retainedId", ""))
        if value
    }
    if set(final_ids) & tombstone_ids:
        raise PromotionError("promoted final ID collides with a tombstone ID")
    if set(candidate_ids) & set(staging_ids):
        raise PromotionError("promoted candidate remains in active staging")
    if set(final_ids) & set(staging_ids):
        raise PromotionError("promoted final ID appears in active staging")

    by_id = {str(item.get("id", "")): item for item in canonical}
    for entry in entries:
        final_id = str(entry.get("finalCanonicalId", ""))
        candidate_id = str(entry.get("candidateId", ""))
        product = by_id.get(final_id)
        if product is None:
            raise PromotionError(f"ledger final ID absent from canonical: {final_id}")
        if sum(value == final_id for value in canonical_ids) != 1:
            raise PromotionError(f"ledger final ID is not unique: {final_id}")
        if (
            product.get("status") != "포함"
            or product.get("revalidationMissingFields") != []
            or product.get("duplicateOf") != ""
            or product.get("canonicalProductId") != final_id
            or product.get("promotionSourceCandidateId") != candidate_id
            or product.get("promotionCampaignId") != campaign
        ):
            raise PromotionError(f"{final_id}: canonical promotion contract mismatch")
        failures = included_errors(product)
        if failures:
            raise PromotionError(f"{final_id}: included evidence failures: {failures}")
        if entry.get("evidenceSha256") != evidence_sha(product):
            raise PromotionError(f"{final_id}: ledger evidence SHA mismatch")

    freezes = freeze_by_wave(checkpoint)
    entries_by_wave: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        try:
            wave = int(entry.get("waveIndex"))
        except (TypeError, ValueError) as exc:
            raise PromotionError("ledger promotion has invalid waveIndex") from exc
        entries_by_wave.setdefault(wave, []).append(entry)
    if set(entries_by_wave) != set(freezes):
        raise PromotionError("ledger waves and frozen wave audits differ")
    for wave, wave_entries in entries_by_wave.items():
        freeze = freezes[wave]
        wave_candidate_ids = [str(item.get("candidateId", "")) for item in wave_entries]
        wave_final_ids = [str(item.get("finalCanonicalId", "")) for item in wave_entries]
        if freeze.get("campaignId") != campaign:
            raise PromotionError(f"wave {wave}: freeze campaign mismatch")
        if freeze.get("candidateIds") != wave_candidate_ids:
            raise PromotionError(f"wave {wave}: frozen candidate IDs differ from ledger")
        if freeze.get("finalCanonicalIds") != wave_final_ids:
            raise PromotionError(f"wave {wave}: frozen final IDs differ from ledger")
        audit_ref = str(freeze.get("auditRef", ""))
        audit_path = resolve_repo_ref(args.root, audit_ref)
        audit_sha = str(freeze.get("auditSha256", ""))
        if not audit_path.exists() or file_sha(audit_path) != audit_sha:
            raise PromotionError(f"wave {wave}: immutable audit SHA mismatch")
        audit = read_json(audit_path)
        audit_campaign = str(audit.get("campaignId") or audit.get("cycleRunId") or "")
        if audit_campaign != campaign or int(audit.get("waveIndex", -1)) != wave:
            raise PromotionError(f"wave {wave}: audit campaign/wave mismatch")
        if set(audit_candidate_ids(audit)) != set(wave_candidate_ids):
            raise PromotionError(f"wave {wave}: audit candidate IDs differ from ledger")
        for entry in wave_entries:
            if (
                entry.get("auditRef") != audit_ref
                or entry.get("auditSha256") != audit_sha
            ):
                raise PromotionError(f"wave {wave}: ledger audit reference mismatch")

    target = campaign_target(checkpoint)
    remaining = target - len(final_ids)
    if checkpoint.get("remainingToTarget", checkpoint.get("newRemaining")) != remaining:
        raise PromotionError("checkpoint remaining count is inconsistent")
    if args.require_target and (len(final_ids) != target or remaining != 0):
        raise PromotionError(
            f"campaign target is incomplete: {len(final_ids)}/{target}, remaining {remaining}"
        )
    baseline_deleted = int(baseline.get("deletedExistingDuplicates", 0))
    deleted_after_baseline = deleted_count(checkpoint) - baseline_deleted
    if deleted_after_baseline < 0:
        raise PromotionError("deleted duplicate count moved backwards after baseline")
    expected_active = int(baseline.get("activeCount", -1)) + len(final_ids) - deleted_after_baseline
    if len(canonical) != expected_active:
        raise PromotionError(
            f"canonical active delta mismatch: {len(canonical)} != {expected_active}"
        )

    report = {
        "status": "passed",
        "campaignId": campaign,
        "targetCanonicalPromotions": target,
        "promotedIncludedCanonicalCount": len(final_ids),
        "promotedIncludedCanonicalIds": final_ids,
        "remainingToTarget": remaining,
        "baselineActive": int(baseline.get("activeCount", -1)),
        "currentActive": len(canonical),
        "deletedExistingDuplicatesAfterBaseline": deleted_after_baseline,
        "waveCounts": dict(sorted(Counter(int(item.get("waveIndex")) for item in entries).items())),
        "categoryCounts": dict(sorted(Counter(by_id[value].get("category") for value in final_ids).items())),
        "canonicalSha256": file_sha(args.canonical),
        "stagingSha256": file_sha(args.staging),
        "ledgerSha256": file_sha(args.ledger),
        "checkpointSha256": file_sha(args.checkpoint),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except PromotionError as exc:
        raise SystemExit(f"promotion verification failed: {exc}")
