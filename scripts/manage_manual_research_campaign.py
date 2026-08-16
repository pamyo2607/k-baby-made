#!/usr/bin/env python3
"""Acquire or release the shared lock and initialize durable campaign ledgers.

Acquisition is fail-closed and writes the lock, ING checkpoint, new-50
checkpoint, and promotion ledger as one rollback-safe group.  Release only
removes the lock whose campaign ID and optional expected HEAD match exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from check_manual_research_lock import load_and_validate
from promote_verified_candidates import sha_json


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOCK = DATA / "manual-research-lock.json"
ING = DATA / "campaign-ing-revalidation.json"
NEW = DATA / "campaign-new50.json"
LEDGER = DATA / "new-product-promotion-ledger.json"
CANONICAL = DATA / "master-products.json"
QUEUE = DATA / "revalidation-queue.json"
STAGING = DATA / "discovered-candidate-staging.json"
QUARANTINE = DATA / "discovered-candidate-quarantine.json"
TOMBSTONES = DATA / "deleted-duplicate-tombstones.json"


LANE_A = [
    "MASTER-0016", "MASTER-0011", "MASTER-0012", "MASTER-0013",
    "MASTER-0014", "MASTER-0017", "MASTER-0018", "MASTER-0019",
    "MASTER-0020", "MASTER-0021", "MASTER-0032", "MASTER-0004",
    "MASTER-0005", "MASTER-0006", "MASTER-0026", "MASTER-0028",
    "MASTER-0002",
]
LANE_B = [
    "MASTER-0071", "MASTER-0046", "MASTER-0048", "MASTER-0049",
    "MASTER-0058", "MASTER-0064", "MASTER-0065", "MASTER-0066",
    "MASTER-0001", "MASTER-0008", "MASTER-0052", "MASTER-0082",
    "MASTER-0038", "MASTER-0051", "MASTER-0053", "MASTER-0061",
    "MASTER-0063",
]
LANE_C = [
    "MASTER-0003", "MASTER-0023", "MASTER-0031", "MASTER-0035",
    "MASTER-0037", "MASTER-0040", "MASTER-0042", "MASTER-0044",
    "MASTER-0054", "MASTER-0056", "MASTER-0060", "MASTER-0069",
    "MASTER-0077", "MASTER-0072", "MASTER-0073",
    "TEETHER-20260801-013",
]


class CampaignError(RuntimeError):
    pass


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read {path}: {exc}") from exc


def document_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def stage(path: Path, content: bytes) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp",
        dir=path.parent, delete=False,
    )
    try:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)
    finally:
        handle.close()


def replace_group(documents: dict[Path, bytes]) -> None:
    before = {path: path.read_bytes() if path.exists() else None for path in documents}
    staged: dict[Path, Path] = {}
    try:
        for path, content in documents.items():
            staged[path] = stage(path, content)
        for path, temporary in staged.items():
            os.replace(temporary, path)
        for path, content in documents.items():
            if path.read_bytes() != content:
                raise CampaignError(f"readback mismatch: {path}")
    except Exception:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        failures = []
        for path, original in before.items():
            try:
                if original is None:
                    if path.exists():
                        path.unlink()
                else:
                    os.replace(stage(path, original), path)
            except OSError as exc:
                failures.append(f"{path}: {exc}")
        if failures:
            raise CampaignError("rollback failed: " + "; ".join(failures))
        raise


def acquire(args: argparse.Namespace) -> None:
    paths = (LOCK, ING, NEW, LEDGER)
    existing = [str(path.relative_to(ROOT)) for path in paths if path.exists()]
    if existing:
        raise CampaignError(f"campaign state already exists: {existing}")
    head = git_head()
    if args.base_sha and args.base_sha != head:
        raise CampaignError(f"HEAD {head} differs from requested base {args.base_sha}")

    canonical = read_json(CANONICAL)
    queue = read_json(QUEUE)
    staging = read_json(STAGING)
    quarantine_doc = read_json(QUARANTINE) if QUARANTINE.exists() else []
    quarantine = (
        quarantine_doc.get("products", [])
        if isinstance(quarantine_doc, dict)
        else quarantine_doc
    )
    tombstone_doc = read_json(TOMBSTONES)
    if not all(isinstance(value, list) for value in (canonical, queue, staging, quarantine)):
        raise CampaignError("canonical, queue, staging, and quarantine must be arrays")
    canonical_ids = [str(item.get("id", "")) for item in canonical]
    if not all(canonical_ids) or len(canonical_ids) != len(set(canonical_ids)):
        raise CampaignError("canonical IDs are blank or duplicated")
    pending_ids = [
        str(item.get("id", "")) for item in canonical
        if item.get("status") == "보류" and not item.get("duplicateOf")
    ]
    queue_ids = [str(item.get("id", "")) for item in queue]
    if len(queue_ids) != len(set(queue_ids)) or set(queue_ids) != set(pending_ids):
        raise CampaignError("queue IDs do not equal canonical pending IDs")
    selected = queue_ids[:50]
    lanes = {"A": LANE_A, "B": LANE_B, "C": LANE_C}
    lane_ids = [value for values in lanes.values() for value in values]
    if len(lane_ids) != 50 or len(set(lane_ids)) != 50 or set(lane_ids) != set(selected):
        raise CampaignError("fixed lane allocation does not equal queue head 50")

    created = now_utc()
    expires = created + timedelta(hours=args.ttl_hours)
    counts = Counter(str(item.get("status", "")) for item in canonical)
    categories = Counter(str(item.get("category", "")) for item in canonical)
    base = {
        "baseCommit": head,
        "canonicalSha256": file_sha(CANONICAL),
        "canonicalIdsSha256": sha_json(canonical_ids),
        "activeCount": len(canonical),
        "included": counts["포함"],
        "pending": counts["보류"],
        "excluded": counts["제외"],
        "duplicateRecords": sum(bool(item.get("duplicateOf")) for item in canonical),
        "categories": dict(sorted(categories.items())),
        "queueSha256": file_sha(QUEUE),
        "stagingSha256": file_sha(STAGING),
        "quarantineSha256": file_sha(QUARANTINE) if QUARANTINE.exists() else None,
        "tombstonesSha256": file_sha(TOMBSTONES),
        "capturedAt": iso(created),
    }
    lock = {
        "campaignId": args.cycle_id,
        "owner": args.owner,
        "baseSha": head,
        "createdAt": iso(created),
        "expiresAt": iso(expires),
    }
    ing = {
        "schemaVersion": 1,
        "campaignId": args.ing_campaign_id,
        "cycleRunId": args.cycle_id,
        "baseCommit": head,
        "baseline": base,
        "targetIdsSnapshot": queue_ids,
        "attemptedIds": [],
        "remainingIds": queue_ids,
        "currentBatchIds": selected,
        "selectedIds": selected,
        "lanes": lanes,
        "batchSize": 50,
        "batchIndex": 1,
        "cursorStart": 0,
        "cursorNext": 50,
        "coverageComplete": False,
        "resolutionComplete": False,
        "status": "initialized",
        "createdAt": iso(created),
        "updatedAt": iso(created),
    }
    tombstone_records = tombstone_doc.get("records", []) if isinstance(tombstone_doc, dict) else []
    new = {
        "schemaVersion": 1,
        "campaignId": args.new_campaign_id,
        "cycleRunId": args.cycle_id,
        "baseCommit": head,
        "canonicalShaBefore": file_sha(CANONICAL),
        "targetCanonicalPromotions": 50,
        "promotedIncludedCanonicalIds": [],
        "remainingToTarget": 50,
        "waveIndex": 0,
        "waveLimit": 30,
        "perCategoryWaveLimit": 5,
        "completedQueries": [],
        "rejectedNormalizedKeys": [],
        "networkFailures": [],
        "deletedExistingDuplicates": 0,
        "promotionBaseline": {
            "canonicalSha256": file_sha(CANONICAL),
            "canonicalIdsSha256": sha_json(canonical_ids),
            "canonicalIds": canonical_ids,
            "activeCount": len(canonical),
            "included": counts["포함"],
            "pending": counts["보류"],
            "excluded": counts["제외"],
            "deletedExistingDuplicates": 0,
            "frozenAt": iso(created),
        },
        "discoveryBaseline": {
            "stagingIds": [str(item.get("id", "")) for item in staging],
            "quarantineIds": [str(item.get("id", "")) for item in quarantine],
            "tombstoneDeletedIds": [
                str(item.get("deletedId", "")) for item in tombstone_records
                if isinstance(item, dict)
            ],
        },
        "status": "initialized",
        "createdAt": iso(created),
        "updatedAt": iso(created),
    }
    ledger = {
        "schemaVersion": 1,
        "campaignId": args.new_campaign_id,
        "promotions": [],
        "createdAt": iso(created),
        "updatedAt": iso(created),
    }
    documents = {
        LOCK: document_bytes(lock),
        ING: document_bytes(ing),
        NEW: document_bytes(new),
        LEDGER: document_bytes(ledger),
    }
    replace_group(documents)
    load_and_validate(LOCK)
    print(json.dumps({
        "status": "acquired",
        "cycleRunId": args.cycle_id,
        "baseCommit": head,
        "pendingSnapshot": len(queue_ids),
        "currentBatch": len(selected),
        "newPromotionTarget": 50,
        "expiresAt": iso(expires),
    }, ensure_ascii=False, indent=2))


def release(args: argparse.Namespace) -> None:
    if not LOCK.exists():
        raise CampaignError("manual research lock is absent")
    payload, _created, _expires = load_and_validate(LOCK)
    if payload.get("campaignId") != args.cycle_id:
        raise CampaignError("lock campaignId does not match requested release")
    if args.expected_head and git_head() != args.expected_head:
        raise CampaignError("HEAD does not match --expected-head")
    LOCK.unlink()
    if LOCK.exists():
        raise CampaignError("lock still exists after release")
    print(json.dumps({"status": "released", "cycleRunId": args.cycle_id}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire_parser = subparsers.add_parser("acquire")
    acquire_parser.add_argument("--cycle-id", required=True)
    acquire_parser.add_argument("--ing-campaign-id", required=True)
    acquire_parser.add_argument("--new-campaign-id", required=True)
    acquire_parser.add_argument("--owner", required=True)
    acquire_parser.add_argument("--base-sha")
    acquire_parser.add_argument("--ttl-hours", type=int, default=24)
    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--cycle-id", required=True)
    release_parser.add_argument("--expected-head")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "acquire":
        if args.ttl_hours <= 0 or args.ttl_hours > 168:
            raise CampaignError("--ttl-hours must be between 1 and 168")
        acquire(args)
    else:
        release(args)


if __name__ == "__main__":
    try:
        main()
    except CampaignError as exc:
        raise SystemExit(f"campaign management blocked: {exc}")
