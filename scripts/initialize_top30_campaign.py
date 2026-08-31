#!/usr/bin/env python3
"""Create or reconcile the cumulative 30-per-category promotion campaign.

The existing staging inventory is preserved.  This migration is allowed only
before any promotion has been written under a different campaign ID.  Repeated
execution is idempotent and recomputes category progress from the immutable
promotion ledger and canonical rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CATEGORIES = ["완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀"]
DEFAULT_CAMPAIGN_ID = "20260831-top30-per-category"


class CampaignError(RuntimeError):
    pass


def read_json(path: Path, default: object | None = None) -> object:
    if not path.exists() and default is not None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read {path}: {exc}") from exc


def document_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha_json(values: list[str]) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_replace(documents: dict[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    try:
        for path, content in documents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                staged[path] = Path(handle.name)
        for path, temporary in staged.items():
            os.replace(temporary, path)
        for path, content in documents.items():
            if path.read_bytes() != content:
                raise CampaignError(f"readback mismatch: {path}")
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()


def reconcile(root: Path, campaign_id: str, per_category_target: int) -> dict:
    if per_category_target <= 0:
        raise CampaignError("per-category target must be positive")
    data = root / "data"
    canonical_path = data / "master-products.json"
    staging_path = data / "discovered-candidate-staging.json"
    checkpoint_path = data / "campaign-new50.json"
    ledger_path = data / "new-product-promotion-ledger.json"
    canonical = read_json(canonical_path)
    staging = read_json(staging_path)
    checkpoint = read_json(checkpoint_path, {})
    ledger = read_json(ledger_path, {"schemaVersion": 1, "promotions": []})
    if not isinstance(canonical, list) or not isinstance(staging, list):
        raise CampaignError("canonical and staging must be arrays")
    if not isinstance(checkpoint, dict) or not isinstance(ledger, dict):
        raise CampaignError("checkpoint and ledger must be objects")

    promotions = ledger.get("promotions", [])
    if not isinstance(promotions, list) or any(not isinstance(item, dict) for item in promotions):
        raise CampaignError("promotion ledger is invalid")
    existing_campaign = str(checkpoint.get("campaignId", ""))
    ledger_campaign = str(ledger.get("campaignId", ""))
    new_campaign = existing_campaign != campaign_id
    if promotions and (existing_campaign != campaign_id or ledger_campaign != campaign_id):
        raise CampaignError("cannot replace a campaign that already has promotions")

    canonical_ids = [str(item.get("id", "")) for item in canonical]
    if not all(canonical_ids) or len(canonical_ids) != len(set(canonical_ids)):
        raise CampaignError("canonical IDs must be nonblank and unique")
    canonical_by_id = {str(item["id"]): item for item in canonical}
    campaign_entries = [item for item in promotions if str(item.get("campaignId", "")) == campaign_id]
    final_ids = [str(item.get("finalCanonicalId", "")) for item in campaign_entries]
    if any(value not in canonical_by_id for value in final_ids):
        raise CampaignError("promotion ledger references a missing canonical product")

    targets = {category: per_category_target for category in CATEGORIES}
    counts = Counter(str(canonical_by_id[value].get("category", "")) for value in final_ids)
    if any(counts[category] > targets[category] for category in CATEGORIES):
        raise CampaignError("category promotion target was exceeded")
    remaining = {category: targets[category] - counts[category] for category in CATEGORIES}
    total_target = sum(targets.values())
    promoted_ids = list(final_ids)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if not promotions or existing_campaign == campaign_id:
        baseline = checkpoint.get("promotionBaseline")
        if not isinstance(baseline, dict) or new_campaign:
            status_counts = Counter(str(item.get("status", "")) for item in canonical)
            baseline = {
                "canonicalSha256": file_sha(canonical_path),
                "canonicalIdsSha256": sha_json(canonical_ids),
                "canonicalIds": canonical_ids,
                "activeCount": len(canonical),
                "included": status_counts["포함"],
                "pending": status_counts["보류"],
                "excluded": status_counts["제외"],
                "deletedExistingDuplicates": int(checkpoint.get("deletedExistingDuplicates", 0) or 0),
                "frozenAt": now,
            }
        checkpoint.update({
            "schemaVersion": 2,
            "campaignId": campaign_id,
            "targetCanonicalPromotions": total_target,
            "categoryTargets": targets,
            "categoryPromotedCounts": {category: counts[category] for category in CATEGORIES},
            "categoryRemaining": remaining,
            "promotedIncludedCanonicalIds": promoted_ids,
            "remainingToTarget": sum(remaining.values()),
            "waveLimit": 30,
            "perCategoryWaveLimit": 5,
            "promotionBaseline": baseline,
            "status": "verified-complete" if not any(remaining.values()) else "in-progress",
            "updatedAt": now,
        })
        if new_campaign:
            checkpoint.update({
                "cycleRunId": f"{campaign_id}-cycle",
                "baseCommit": os.environ.get("GITHUB_SHA", "") or checkpoint.get("baseCommit", ""),
                "canonicalShaBefore": file_sha(canonical_path),
                "completedQueries": [],
                "rejectedNormalizedKeys": [],
                "networkFailures": [],
                "promotionAuditFreezes": [],
                "createdAt": now,
                "discoveryBaseline": {
                    "stagingIds": [str(item.get("id", "")) for item in staging],
                },
            })
        else:
            checkpoint.setdefault("createdAt", now)
        checkpoint.setdefault("waveIndex", 0)
        checkpoint.setdefault("deletedExistingDuplicates", 0)
        checkpoint.setdefault("rankingBasis", {
            "provider": "Naver",
            "method": "국내 상품 검색 상위 노출 후보를 순차 검증",
            "limitation": "공개 판매량 수치가 없는 상품은 검색 노출 순위를 상위권 대리 지표로 사용",
        })
        ledger.update({
            "schemaVersion": 1,
            "campaignId": campaign_id,
            "promotions": promotions,
            "updatedAt": now,
        })
        if new_campaign:
            ledger["createdAt"] = now
        else:
            ledger.setdefault("createdAt", now)

    atomic_replace({
        checkpoint_path: document_bytes(checkpoint),
        ledger_path: document_bytes(ledger),
    })
    return {
        "campaignId": campaign_id,
        "targetCanonicalPromotions": total_target,
        "promoted": len(promoted_ids),
        "remaining": sum(remaining.values()),
        "categoryTargets": targets,
        "categoryPromotedCounts": {category: counts[category] for category in CATEGORIES},
        "categoryRemaining": remaining,
        "stagingCount": len(staging),
        "status": checkpoint["status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)
    parser.add_argument("--per-category-target", type=int, default=30)
    args = parser.parse_args()
    try:
        result = reconcile(args.root.resolve(), args.campaign_id, args.per_category_target)
    except CampaignError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
