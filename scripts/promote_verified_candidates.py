#!/usr/bin/env python3
"""Promote explicitly verified DISC candidates into the canonical database.

The command is dry-run by default.  A candidate is eligible only when it has
the following explicit, fail-closed contract (in addition to passing
``validate_data.included_errors`` after canonical normalization)::

    "promotionGate": {
      "schemaVersion": 1,
      "promotionReady": true,
      "evidenceComplete": true,
      "stageDecision": "included",
      "dedupeDecision": "new",
      "campaignId": "YYYYMMDD-HHMM-new50",
      "waveIndex": 1,
      "auditRef": "data/<immutable-wave-audit>.json",
      "auditSha256": "<sha256 of auditRef>"
    }

The wave audit must name the same campaign, wave, and promotion-ready
candidate IDs.  ``--apply`` validates all four prospective documents first,
then atomically replaces canonical, staging, promotion ledger, and campaign
checkpoint.  If a replacement fails, all four paths are restored to their
exact pre-run bytes (or removed again if they did not exist).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from validate_data import included_errors

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_CANONICAL = DATA / "master-products.json"
DEFAULT_STAGING = DATA / "discovered-candidate-staging.json"
DEFAULT_LEDGER = DATA / "new-product-promotion-ledger.json"
DEFAULT_CHECKPOINT = DATA / "campaign-new50.json"
DEFAULT_TOMBSTONES = DATA / "deleted-duplicate-tombstones.json"

ID_RE = re.compile(r"^KBM-(\d{8})-(\d{4})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CATEGORIES = {
    "완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀",
}
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "igshid", "ref", "source", "src", "tracking", "campaign",
}
EVIDENCE_FIELDS = (
    "category", "subtype", "brand", "name", "officialModel", "officialSku",
    "countryOfManufacture", "saleStatus", "ageRange", "ageEvidence",
    "manufacturer", "importer", "regulatoryRegime", "regulatoryNote",
    "kcApplicable", "kcNumber", "kcType", "certifications",
    "officialUrls", "saleUrls", "safetyKoreaSearchUrl", "reason", "checkedAt",
    "certificationEvidenceLevel", "certStatusSummary", "certDateSummary",
    "certTypeSummary", "certAuthoritySummary",
)


class PromotionError(RuntimeError):
    """A fail-closed promotion contract or invariant violation."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def document_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_json(value: Any) -> str:
    return sha_bytes(canonical_json(value))


def file_sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return copy.deepcopy(default)
        raise PromotionError(f"required JSON file is absent: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"cannot read JSON {path}: {exc}") from exc


def normalize_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def list_values(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def identity_keys(item: dict[str, Any]) -> set[str]:
    identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
    dedupe = item.get("dedupe") if isinstance(item.get("dedupe"), dict) else {}
    brands = [item.get("brand", ""), *list_values(item.get("brandAliases"))]
    names = [
        item.get("name", ""),
        *list_values(item.get("aliases")),
        identity.get("exactProductName", ""),
    ]
    models = [item.get("officialModel", ""), identity.get("officialModel", "")]
    keys: set[str] = set()
    for raw_brand in brands:
        brand = normalize_text(raw_brand)
        if not brand:
            continue
        for raw_name in names:
            name = normalize_text(raw_name)
            if name:
                keys.add(f"brand-name:{brand}|{name}")
        for raw_model in models:
            model = normalize_text(raw_model)
            if model:
                keys.add(f"brand-model:{brand}|{model}")
    explicit_key = normalize_text(dedupe.get("normalizedKey"))
    if explicit_key:
        keys.add(f"declared:{explicit_key}")
    return keys


def tombstone_identity_keys(record: dict[str, Any]) -> set[str]:
    names = [record.get("name", "")]
    brands = [record.get("brand", ""), *record.get("brandAliases", [])]
    keys: set[str] = set()
    for brand in brands:
        for name in names:
            normalized_brand = normalize_text(brand)
            normalized_name = normalize_text(name)
            if normalized_brand and normalized_name:
                keys.add(f"brand-name:{normalized_brand}|{normalized_name}")
    return keys


def normalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw.startswith(("https://", "http://")):
        return ""
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    host = parsed.netloc.casefold()
    if host.endswith(":80") and parsed.scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and parsed.scheme == "https":
        host = host[:-4]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, value_part in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        query.append((key, value_part))
    query.sort()
    return urlunparse((parsed.scheme.casefold(), host, path, "", urlencode(query), ""))


def product_urls(item: dict[str, Any]) -> set[str]:
    raw_urls: list[Any] = []
    for key in ("directProductUrl", "officialUrls", "saleUrls"):
        value = item.get(key, [])
        raw_urls.extend(value if isinstance(value, list) else [value])
    safety_url = item.get("safetyKoreaSearchUrl")
    if "/release/certDetail" in str(safety_url or ""):
        raw_urls.append(safety_url)
    for cert in item.get("certifications", []):
        if isinstance(cert, dict) and "/release/certDetail" in str(cert.get("url", "")):
            raw_urls.append(cert.get("url"))
    return {url for value in raw_urls if (url := normalize_url(value))}


def tombstone_urls(record: dict[str, Any]) -> set[str]:
    values = record.get("evidenceUrls", [])
    return {url for value in values if (url := normalize_url(value))}


def normalize_kc(value: Any) -> str:
    return re.sub(r"[^0-9A-Z-]+", "", str(value or "").upper())


def product_kc_numbers(item: dict[str, Any]) -> set[str]:
    values: list[Any] = [item.get("kcNumber", "")]
    for cert in item.get("certifications", []):
        if isinstance(cert, dict):
            values.extend(
                cert.get(key, "")
                for key in ("number", "kcNumber", "certNum", "certificateNumber")
            )
            values.extend(
                value
                for key, value in parse_qsl(urlparse(str(cert.get("url", ""))).query)
                if key.casefold() == "certnum"
            )
    return {number for value in values if (number := normalize_kc(value))}


def tombstone_kc_numbers(record: dict[str, Any]) -> set[str]:
    values: list[Any] = [record.get("kcNumber", "")]
    for raw_url in record.get("evidenceUrls", []):
        values.extend(
            value
            for key, value in parse_qsl(urlparse(str(raw_url)).query)
            if key.casefold() == "certnum"
        )
    return {number for value in values if (number := normalize_kc(value))}


def evidence_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in EVIDENCE_FIELDS if key in item}


def evidence_sha(item: dict[str, Any]) -> str:
    return sha_json(evidence_payload(item))


def campaign_id(checkpoint: dict[str, Any]) -> str:
    value = str(checkpoint.get("campaignId") or checkpoint.get("cycleRunId") or "")
    if not value:
        raise PromotionError("checkpoint has no campaignId/cycleRunId")
    return value


def campaign_target(checkpoint: dict[str, Any]) -> int:
    raw = checkpoint.get(
        "targetCanonicalPromotions", checkpoint.get("newCycleTarget", 50)
    )
    try:
        target = int(raw)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"invalid campaign target: {raw!r}") from exc
    if target <= 0:
        raise PromotionError("campaign target must be positive")
    return target


def checkpoint_promoted_ids(checkpoint: dict[str, Any]) -> list[str]:
    values = checkpoint.get("promotedIncludedCanonicalIds", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise PromotionError("checkpoint promotedIncludedCanonicalIds must be a string list")
    if len(values) != len(set(values)):
        raise PromotionError("checkpoint promotedIncludedCanonicalIds contains duplicates")
    return list(values)


def ledger_promotions(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    values = ledger.get("promotions", [])
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise PromotionError("promotion ledger promotions must be an object list")
    return list(values)


def deleted_count(checkpoint: dict[str, Any]) -> int:
    value = checkpoint.get("deletedExistingDuplicates", 0)
    if isinstance(value, list):
        return len(set(str(item) for item in value))
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError("deletedExistingDuplicates must be a list or integer") from exc
    if count < 0:
        raise PromotionError("deletedExistingDuplicates cannot be negative")
    return count


def resolve_repo_ref(root: Path, reference: str) -> Path:
    if not reference or Path(reference).is_absolute():
        raise PromotionError("promotion audit reference must be a nonblank repo-relative path")
    root_resolved = root.resolve()
    resolved = (root / reference).resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PromotionError("promotion audit reference escapes the repository root")
    return resolved


def audit_candidate_ids(audit: dict[str, Any]) -> list[str]:
    for key in ("promotionReadyCandidateIds", "candidateIds", "promotedCandidateIds"):
        values = audit.get(key)
        if isinstance(values, list):
            return [str(value) for value in values]
    promotion = audit.get("promotion")
    if isinstance(promotion, dict):
        for key in ("readyCandidateIds", "candidateIds"):
            values = promotion.get(key)
            if isinstance(values, list):
                return [str(value) for value in values]
    products = audit.get("products")
    if isinstance(products, list):
        ids = []
        for product in products:
            if not isinstance(product, dict):
                continue
            decision = product.get("stageDecision") or product.get("decision")
            if decision == "included":
                ids.append(str(product.get("candidateId") or product.get("id") or ""))
        if ids:
            return ids
    raise PromotionError(
        "wave audit must contain promotionReadyCandidateIds/candidateIds "
        "or included product audit rows"
    )


def promotion_contract(
    candidate: dict[str, Any], expected_campaign: str, expected_wave: int
) -> dict[str, Any]:
    contract = candidate.get("promotionGate")
    candidate_id = str(candidate.get("id", ""))
    if not isinstance(contract, dict):
        raise PromotionError(f"{candidate_id}: explicit promotionGate contract is absent")
    exact = {
        "schemaVersion": 1,
        "promotionReady": True,
        "evidenceComplete": True,
        "stageDecision": "included",
        "dedupeDecision": "new",
    }
    for key, expected in exact.items():
        if contract.get(key) != expected:
            raise PromotionError(
                f"{candidate_id}: promotionGate.{key} must be {expected!r}"
            )
    if str(contract.get("campaignId", "")) != expected_campaign:
        raise PromotionError(f"{candidate_id}: promotionGate campaign mismatch")
    try:
        wave = int(contract.get("waveIndex"))
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{candidate_id}: invalid promotionGate waveIndex") from exc
    if wave != expected_wave:
        raise PromotionError(f"{candidate_id}: promotionGate wave mismatch")
    audit_ref = str(contract.get("auditRef", ""))
    audit_sha = str(contract.get("auditSha256", "")).casefold()
    if not audit_ref or not SHA_RE.fullmatch(audit_sha):
        raise PromotionError(f"{candidate_id}: auditRef/auditSha256 is incomplete")
    if candidate.get("revalidationMissingFields") != []:
        raise PromotionError(f"{candidate_id}: revalidationMissingFields must be []")
    if candidate.get("status") not in {"보류", "포함"}:
        raise PromotionError(f"{candidate_id}: staged status must be 보류 or 포함")
    return contract


def canonicalize_candidate(
    candidate: dict[str, Any], final_id: str, campaign: str, wave: int
) -> dict[str, Any]:
    product = copy.deepcopy(candidate)
    product.pop("promotionGate", None)
    product.pop("remainingMissingFields", None)
    product.pop("stageDecision", None)
    source_id = str(candidate.get("id", ""))
    product.update({
        "id": final_id,
        "canonicalProductId": final_id,
        "duplicateOf": "",
        "status": "포함",
        "statusDisplay": "기준 충족",
        "strict419Status": "검증 완료",
        "revalidationMissingFields": [],
        "revalidationResolved": True,
        "revalidationState": "검증 완료",
        "promotionSourceCandidateId": source_id,
        "promotionCampaignId": campaign,
        "promotionWaveIndex": wave,
    })
    product["promotionEvidenceSha256"] = evidence_sha(product)
    return product


def next_final_ids(
    candidates: Iterable[dict[str, Any]], campaign: str, used_ids: set[str]
) -> dict[str, str]:
    match = re.match(r"^(\d{8})(?:-|$)", campaign)
    date_part = match.group(1) if match else datetime.now(timezone.utc).strftime("%Y%m%d")
    used_numbers = {
        int(match.group(2))
        for value in used_ids
        if (match := ID_RE.fullmatch(value)) and match.group(1) == date_part
    }
    cursor = max(used_numbers, default=0) + 1
    assigned: dict[str, str] = {}
    for candidate in sorted(candidates, key=lambda item: str(item.get("id", ""))):
        while cursor in used_numbers:
            cursor += 1
        if cursor > 9999:
            raise PromotionError(f"no available KBM sequence for {date_part}")
        candidate_id = str(candidate.get("id", ""))
        assigned[candidate_id] = f"KBM-{date_part}-{cursor:04d}"
        used_numbers.add(cursor)
        cursor += 1
    return assigned


def ensure_unique_records(records: list[dict[str, Any]], label: str) -> None:
    ids = [str(item.get("id", "")) for item in records]
    if not all(ids) or len(ids) != len(set(ids)):
        raise PromotionError(f"{label} IDs must be nonblank and unique")


def build_indexes(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, set[str]]]:
    indexes = {
        "identity": defaultdict(set),
        "url": defaultdict(set),
        "kc": defaultdict(set),
    }
    for item in records:
        item_id = str(item.get("id", ""))
        for value in identity_keys(item):
            indexes["identity"][value].add(item_id)
        for value in product_urls(item):
            indexes["url"][value].add(item_id)
        for value in product_kc_numbers(item):
            indexes["kc"][value].add(item_id)
    return indexes


def assert_no_product_duplicates(
    selected: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
    other_staging: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
) -> None:
    existing = build_indexes([*canonical, *other_staging])
    tombstone_index = {
        "identity": defaultdict(set),
        "url": defaultdict(set),
        "kc": defaultdict(set),
    }
    for item in tombstones:
        marker = str(item.get("deletedId", "tombstone"))
        for value in tombstone_identity_keys(item):
            tombstone_index["identity"][value].add(marker)
        for value in tombstone_urls(item):
            tombstone_index["url"][value].add(marker)
        for value in tombstone_kc_numbers(item):
            tombstone_index["kc"][value].add(marker)
    selected_seen = {
        "identity": defaultdict(set),
        "url": defaultdict(set),
        "kc": defaultdict(set),
    }
    for item in selected:
        candidate_id = str(item.get("id", ""))
        values_by_kind = {
            "identity": identity_keys(item),
            "url": product_urls(item),
            "kc": product_kc_numbers(item),
        }
        if not values_by_kind["identity"]:
            raise PromotionError(f"{candidate_id}: normalized product identity is incomplete")
        if not values_by_kind["url"]:
            raise PromotionError(f"{candidate_id}: direct official/sale URL is absent")
        for kind, values in values_by_kind.items():
            for value in values:
                matches = (
                    existing[kind].get(value, set())
                    | tombstone_index[kind].get(value, set())
                    | selected_seen[kind].get(value, set())
                )
                if matches:
                    raise PromotionError(
                        f"{candidate_id}: duplicate {kind} {value!r} matches "
                        f"{sorted(matches)}"
                    )
                selected_seen[kind][value].add(candidate_id)


def validate_ledger(ledger: dict[str, Any]) -> None:
    promotions = ledger_promotions(ledger)
    candidate_ids = [str(item.get("candidateId", "")) for item in promotions]
    final_ids = [str(item.get("finalCanonicalId", "")) for item in promotions]
    if not all(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise PromotionError("ledger candidate IDs must be nonblank and unique")
    if not all(final_ids) or len(final_ids) != len(set(final_ids)):
        raise PromotionError("ledger final canonical IDs must be nonblank and unique")
    for entry in promotions:
        if not ID_RE.fullmatch(str(entry.get("finalCanonicalId", ""))):
            raise PromotionError("ledger contains a malformed final canonical ID")
        for key in ("evidenceSha256", "sourceCandidateSha256", "auditSha256"):
            if not SHA_RE.fullmatch(str(entry.get(key, "")).casefold()):
                raise PromotionError(f"ledger entry has invalid {key}")


def validate_prospective_documents(
    canonical: list[dict[str, Any]],
    staging: list[dict[str, Any]],
    ledger: dict[str, Any],
    checkpoint: dict[str, Any],
    tombstones: list[dict[str, Any]],
) -> None:
    ensure_unique_records(canonical, "canonical")
    ensure_unique_records(staging, "staging")
    canonical_ids = [str(item.get("id", "")) for item in canonical]
    if [item.get("sequence") for item in canonical] != list(range(1, len(canonical) + 1)):
        raise PromotionError("prospective canonical sequence is not contiguous 1..N")
    if any(value.startswith("DISC-") for value in canonical_ids):
        raise PromotionError("DISC candidate leaked into prospective canonical IDs")
    if set(canonical_ids) & {str(item.get("id", "")) for item in staging}:
        raise PromotionError("canonical and staging active IDs overlap")
    tombstone_ids = {
        str(value)
        for item in tombstones
        for value in (item.get("deletedId", ""), item.get("retainedId", ""))
        if value
    }
    new_ids = set(checkpoint_promoted_ids(checkpoint))
    if new_ids & tombstone_ids:
        raise PromotionError("promoted canonical ID collides with tombstone identity")
    by_id = {str(item.get("id", "")): item for item in canonical}
    for final_id in new_ids:
        product = by_id.get(final_id)
        if product is None:
            raise PromotionError(f"checkpoint promotion absent from canonical: {final_id}")
        if (
            product.get("status") != "포함"
            or product.get("revalidationMissingFields") != []
            or product.get("duplicateOf") != ""
            or product.get("canonicalProductId") != final_id
        ):
            raise PromotionError(f"{final_id}: prospective included canonical contract failed")
        failures = included_errors(product)
        if failures:
            raise PromotionError(f"{final_id}: included evidence failures: {failures}")
    validate_ledger(ledger)
    campaign = campaign_id(checkpoint)
    campaign_entries = [
        item for item in ledger_promotions(ledger) if item.get("campaignId") == campaign
    ]
    ledger_ids = [str(item.get("finalCanonicalId", "")) for item in campaign_entries]
    if ledger_ids != checkpoint_promoted_ids(checkpoint):
        raise PromotionError("checkpoint promotion IDs do not equal campaign ledger order")
    staged_ids = {str(item.get("id", "")) for item in staging}
    if any(str(item.get("candidateId", "")) in staged_ids for item in campaign_entries):
        raise PromotionError("promoted candidate remains in active staging")
    target = campaign_target(checkpoint)
    if len(ledger_ids) > target:
        raise PromotionError("campaign promotion target would be exceeded")
    if checkpoint.get("remainingToTarget", checkpoint.get("newRemaining")) != target - len(ledger_ids):
        raise PromotionError("checkpoint remainingToTarget/newRemaining is inconsistent")
    baseline = checkpoint.get("promotionBaseline")
    if not isinstance(baseline, dict):
        raise PromotionError("checkpoint promotionBaseline is absent")
    baseline_ids = baseline.get("canonicalIds")
    if not isinstance(baseline_ids, list) or len(baseline_ids) != len(set(baseline_ids)):
        raise PromotionError("promotionBaseline canonicalIds is invalid")
    if baseline.get("canonicalIdsSha256") != sha_json(baseline_ids):
        raise PromotionError("promotionBaseline canonical ID SHA mismatch")
    if new_ids & set(baseline_ids):
        raise PromotionError("promoted final ID was already present in the baseline")
    baseline_deleted = int(baseline.get("deletedExistingDuplicates", 0))
    deleted_after_baseline = deleted_count(checkpoint) - baseline_deleted
    if deleted_after_baseline < 0:
        raise PromotionError("deleted duplicate count moved backwards after baseline")
    expected_count = int(baseline.get("activeCount", -1)) + len(new_ids) - deleted_after_baseline
    if len(canonical) != expected_count:
        raise PromotionError(
            f"canonical delta mismatch: {len(canonical)} != {expected_count}"
        )


def stage_temp(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    try:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)
    finally:
        handle.close()


def restore_paths(before: dict[Path, bytes | None]) -> None:
    failures = []
    for path, original in before.items():
        try:
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                replacement = stage_temp(path, original)
                os.replace(replacement, path)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    for path, original in before.items():
        actual = path.read_bytes() if path.exists() else None
        if actual != original:
            failures.append(f"{path}: restored bytes do not match pre-run bytes")
    if failures:
        raise PromotionError("rollback failed: " + "; ".join(failures))


def replace_four_atomically(documents: dict[Path, bytes]) -> None:
    before = {path: path.read_bytes() if path.exists() else None for path in documents}
    temp_paths: dict[Path, Path] = {}
    try:
        for path, content in documents.items():
            temp_paths[path] = stage_temp(path, content)
        for path, temp_path in temp_paths.items():
            os.replace(temp_path, path)
        for path, content in documents.items():
            if path.read_bytes() != content:
                raise PromotionError(f"post-replace readback mismatch: {path}")
    except Exception:
        for temp_path in temp_paths.values():
            if temp_path.exists():
                temp_path.unlink()
        restore_paths(before)
        raise


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="replace all four data files")
    parser.add_argument("--campaign-id", help="must match the campaign checkpoint")
    parser.add_argument("--wave-index", type=int, help="wave represented by the immutable audit")
    parser.add_argument("--candidate-id", action="append", default=[], help="repeatable DISC ID")
    parser.add_argument("--audit-ref", help="repo-relative audit path; must match every contract")
    parser.add_argument("--promoted-at", help="fixed ISO timestamp (primarily for reproducible audit runs)")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--tombstones", type=Path, default=DEFAULT_TOMBSTONES)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    canonical = read_json(args.canonical)
    staging = read_json(args.staging)
    ledger = read_json(args.ledger, default={"schemaVersion": 1, "promotions": []})
    checkpoint = read_json(args.checkpoint)
    tombstone_doc = read_json(args.tombstones, default={"records": []})
    tombstones = tombstone_doc.get("records", []) if isinstance(tombstone_doc, dict) else []
    if not isinstance(canonical, list) or not isinstance(staging, list):
        raise PromotionError("canonical and staging must both be JSON arrays")
    if not isinstance(ledger, dict) or not isinstance(checkpoint, dict):
        raise PromotionError("ledger and checkpoint must both be JSON objects")
    ensure_unique_records(canonical, "canonical")
    ensure_unique_records(staging, "staging")
    validate_ledger(ledger)

    campaign = campaign_id(checkpoint)
    if args.campaign_id and args.campaign_id != campaign:
        raise PromotionError("CLI campaign ID does not match checkpoint")
    wave = args.wave_index if args.wave_index is not None else int(checkpoint.get("waveIndex", 0))
    if wave <= 0:
        raise PromotionError("--wave-index must be positive for a promotion wave")
    promoted_at = args.promoted_at or iso_now()

    existing_entries = ledger_promotions(ledger)
    entry_by_candidate = {str(item.get("candidateId", "")): item for item in existing_entries}
    canonical_by_id = {str(item.get("id", "")): item for item in canonical}
    staging_by_id = {str(item.get("id", "")): item for item in staging}
    explicit_ids = list(dict.fromkeys(args.candidate_id))
    already_promoted: list[str] = []
    if explicit_ids:
        missing = []
        selected = []
        for candidate_id in explicit_ids:
            candidate = staging_by_id.get(candidate_id)
            if candidate is not None:
                selected.append(candidate)
                continue
            entry = entry_by_candidate.get(candidate_id)
            final_id = str(entry.get("finalCanonicalId", "")) if entry else ""
            if entry and final_id in canonical_by_id and entry.get("campaignId") == campaign:
                already_promoted.append(final_id)
            else:
                missing.append(candidate_id)
        if missing:
            raise PromotionError(f"requested candidate IDs are absent: {missing}")
    else:
        selected = [
            item for item in staging
            if str(item.get("id", "")).startswith("DISC-")
            and isinstance(item.get("promotionGate"), dict)
            and item["promotionGate"].get("promotionReady") is True
        ]

    if not selected:
        result = {
            "status": "no-op",
            "mode": "apply" if args.apply else "dry-run",
            "campaignId": campaign,
            "waveIndex": wave,
            "alreadyPromotedCanonicalIds": already_promoted,
            "promotions": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if len(selected) > 30:
        raise PromotionError(f"wave promotion count {len(selected)} exceeds 30")
    counts = Counter(str(item.get("category", "")) for item in selected)
    if any(category not in ALLOWED_CATEGORIES for category in counts):
        raise PromotionError(f"noncanonical promotion category: {dict(counts)}")
    over_category = {key: value for key, value in counts.items() if value > 5}
    if over_category:
        raise PromotionError(f"wave category promotion cap exceeded: {over_category}")

    contracts = [promotion_contract(item, campaign, wave) for item in selected]
    audit_refs = {str(item.get("auditRef")) for item in contracts}
    audit_shas = {str(item.get("auditSha256", "")).casefold() for item in contracts}
    if len(audit_refs) != 1 or len(audit_shas) != 1:
        raise PromotionError("all selected candidates must share one immutable wave audit")
    audit_ref = next(iter(audit_refs))
    if args.audit_ref and args.audit_ref != audit_ref:
        raise PromotionError("CLI audit reference does not match candidate contracts")
    audit_path = resolve_repo_ref(args.root, audit_ref)
    expected_audit_sha = next(iter(audit_shas))
    if not audit_path.exists() or file_sha(audit_path) != expected_audit_sha:
        raise PromotionError("wave audit is absent or its SHA does not match promotionGate")
    audit = read_json(audit_path)
    audit_campaign = str(audit.get("campaignId") or audit.get("cycleRunId") or "")
    if audit_campaign != campaign or int(audit.get("waveIndex", -1)) != wave:
        raise PromotionError("wave audit campaign/wave does not match checkpoint")
    selected_ids = [str(item.get("id", "")) for item in selected]
    if set(audit_candidate_ids(audit)) != set(selected_ids):
        raise PromotionError("wave audit candidate IDs do not exactly match this promotion")

    selected_id_set = set(selected_ids)
    if any(not candidate_id.startswith("DISC-") for candidate_id in selected_ids):
        raise PromotionError("only DISC-* staging candidates may be promoted")
    tombstone_ids = {
        str(value)
        for item in tombstones
        for value in (item.get("deletedId", ""), item.get("retainedId", ""))
        if value
    }
    if selected_id_set & (set(canonical_by_id) | tombstone_ids):
        raise PromotionError("candidate ID collides with canonical/tombstone IDs")
    other_staging = [item for item in staging if str(item.get("id", "")) not in selected_id_set]
    assert_no_product_duplicates(selected, canonical, other_staging, tombstones)

    campaign_entries_before = [item for item in existing_entries if item.get("campaignId") == campaign]
    checkpoint_ids_before = checkpoint_promoted_ids(checkpoint)
    if [str(item.get("finalCanonicalId", "")) for item in campaign_entries_before] != checkpoint_ids_before:
        raise PromotionError("pre-run checkpoint and promotion ledger are inconsistent")
    baseline = checkpoint.get("promotionBaseline")
    if baseline is None:
        if checkpoint_ids_before or campaign_entries_before:
            raise PromotionError("cannot infer baseline after campaign promotions already exist")
        canonical_ids_before = [str(item.get("id", "")) for item in canonical]
        baseline = {
            "canonicalSha256": file_sha(args.canonical),
            "canonicalIdsSha256": sha_json(canonical_ids_before),
            "canonicalIds": canonical_ids_before,
            "activeCount": len(canonical_ids_before),
            "deletedExistingDuplicates": deleted_count(checkpoint),
            "frozenAt": promoted_at,
        }
    elif not isinstance(baseline, dict):
        raise PromotionError("checkpoint promotionBaseline must be an object")

    used_ids = set(canonical_by_id) | tombstone_ids | {
        str(item.get("finalCanonicalId", "")) for item in existing_entries
    }
    mapping: dict[str, str] = {}
    unmapped = []
    for candidate in selected:
        candidate_id = str(candidate.get("id", ""))
        old_entry = entry_by_candidate.get(candidate_id)
        if old_entry:
            if old_entry.get("campaignId") != campaign:
                raise PromotionError(f"{candidate_id}: already mapped by another campaign")
            final_id = str(old_entry.get("finalCanonicalId", ""))
            if final_id in canonical_by_id:
                raise PromotionError(f"{candidate_id}: already canonical but still active in staging")
            mapping[candidate_id] = final_id
        else:
            unmapped.append(candidate)
    mapping.update(next_final_ids(unmapped, campaign, used_ids | set(mapping.values())))
    if len(set(mapping.values())) != len(mapping):
        raise PromotionError("prospective final canonical IDs are not unique")

    promoted_products = []
    new_entries = []
    for candidate in selected:
        candidate_id = str(candidate.get("id", ""))
        final_id = mapping[candidate_id]
        if final_id in canonical_by_id or final_id in tombstone_ids:
            raise PromotionError(f"{candidate_id}: final ID collision: {final_id}")
        product = canonicalize_candidate(candidate, final_id, campaign, wave)
        failures = included_errors(product)
        if failures:
            raise PromotionError(f"{candidate_id}: included evidence failures: {failures}")
        promoted_products.append(product)
        new_entries.append({
            "candidateId": candidate_id,
            "finalCanonicalId": final_id,
            "campaignId": campaign,
            "waveIndex": wave,
            "promotedAt": promoted_at,
            "evidenceSha256": evidence_sha(product),
            "sourceCandidateSha256": sha_json(candidate),
            "auditRef": audit_ref,
            "auditSha256": expected_audit_sha,
        })

    prospective_canonical = copy.deepcopy(canonical) + promoted_products
    for sequence, product in enumerate(prospective_canonical, start=1):
        product["sequence"] = sequence
    prospective_staging = copy.deepcopy(other_staging)
    prospective_ledger = copy.deepcopy(ledger)
    prospective_ledger["schemaVersion"] = 1
    prospective_ledger["promotions"] = existing_entries + new_entries
    prospective_ledger["updatedAt"] = promoted_at

    prospective_checkpoint = copy.deepcopy(checkpoint)
    prospective_checkpoint["promotionBaseline"] = baseline
    new_final_ids = [mapping[candidate_id] for candidate_id in selected_ids]
    all_campaign_ids = checkpoint_ids_before + new_final_ids
    target = campaign_target(checkpoint)
    if len(all_campaign_ids) > target:
        raise PromotionError(
            f"campaign target {target} would be exceeded by {len(all_campaign_ids)} promotions"
        )
    prospective_checkpoint["promotedIncludedCanonicalIds"] = all_campaign_ids
    prospective_checkpoint["remainingToTarget"] = target - len(all_campaign_ids)
    category_targets = prospective_checkpoint.get("categoryTargets")
    if category_targets is not None:
        if (
            not isinstance(category_targets, dict)
            or set(category_targets) != ALLOWED_CATEGORIES
        ):
            raise PromotionError("categoryTargets must contain the six canonical categories")
        normalized_targets: dict[str, int] = {}
        for category in ALLOWED_CATEGORIES:
            try:
                value = int(category_targets[category])
            except (TypeError, ValueError) as exc:
                raise PromotionError(f"invalid category target for {category}") from exc
            if value <= 0:
                raise PromotionError(f"category target must be positive for {category}")
            normalized_targets[category] = value
        if sum(normalized_targets.values()) != target:
            raise PromotionError("categoryTargets total differs from campaign target")
        prospective_by_id = {
            str(item.get("id", "")): item for item in prospective_canonical
        }
        category_counts = Counter(
            str(prospective_by_id[value].get("category", ""))
            for value in all_campaign_ids
            if value in prospective_by_id
        )
        over_target = {
            category: category_counts[category]
            for category in ALLOWED_CATEGORIES
            if category_counts[category] > normalized_targets[category]
        }
        if over_target:
            raise PromotionError(f"category campaign target exceeded: {over_target}")
        category_remaining = {
            category: normalized_targets[category] - category_counts[category]
            for category in sorted(ALLOWED_CATEGORIES)
        }
        prospective_checkpoint["categoryPromotedCounts"] = {
            category: category_counts[category]
            for category in sorted(ALLOWED_CATEGORIES)
        }
        prospective_checkpoint["categoryRemaining"] = category_remaining
        prospective_checkpoint["remainingToTarget"] = sum(category_remaining.values())
        prospective_checkpoint["status"] = (
            "verified-complete"
            if not any(category_remaining.values())
            else "in-progress"
        )
    if "newRemaining" in prospective_checkpoint or "newCycleTarget" in prospective_checkpoint:
        prospective_checkpoint["newRemaining"] = target - len(all_campaign_ids)
    prospective_checkpoint["waveIndex"] = max(int(checkpoint.get("waveIndex", 0)), wave)
    freeze = {
        "campaignId": campaign,
        "waveIndex": wave,
        "auditRef": audit_ref,
        "auditSha256": expected_audit_sha,
        "candidateIds": selected_ids,
        "finalCanonicalIds": new_final_ids,
        "canonicalSha256Before": file_sha(args.canonical),
        "stagingSha256Before": file_sha(args.staging),
        "ledgerSha256Before": file_sha(args.ledger) if args.ledger.exists() else None,
        "checkpointSha256Before": file_sha(args.checkpoint),
        "canonicalCountBefore": len(canonical),
        "stagingCountBefore": len(staging),
        "frozenAt": promoted_at,
    }
    freezes = list(prospective_checkpoint.get("promotionAuditFreezes", []))
    if any(int(item.get("waveIndex", -1)) == wave for item in freezes if isinstance(item, dict)):
        raise PromotionError(f"checkpoint already contains a promotion freeze for wave {wave}")
    freezes.append(freeze)
    prospective_checkpoint["promotionAuditFreezes"] = freezes
    prospective_checkpoint["lastPromotion"] = {
        "waveIndex": wave,
        "candidateIds": selected_ids,
        "finalCanonicalIds": new_final_ids,
        "count": len(new_final_ids),
        "promotedAt": promoted_at,
    }
    prospective_checkpoint["updatedAt"] = promoted_at

    validate_prospective_documents(
        prospective_canonical,
        prospective_staging,
        prospective_ledger,
        prospective_checkpoint,
        tombstones,
    )
    documents = {
        args.canonical: document_bytes(prospective_canonical),
        args.staging: document_bytes(prospective_staging),
        args.ledger: document_bytes(prospective_ledger),
        args.checkpoint: document_bytes(prospective_checkpoint),
    }
    preview = {
        "status": "ready",
        "mode": "apply" if args.apply else "dry-run",
        "campaignId": campaign,
        "waveIndex": wave,
        "audit": {"ref": audit_ref, "sha256": expected_audit_sha},
        "counts": {
            "canonicalBefore": len(canonical),
            "canonicalAfter": len(prospective_canonical),
            "stagingBefore": len(staging),
            "stagingAfter": len(prospective_staging),
            "promotedThisWave": len(new_final_ids),
            "promotedCampaignTotal": len(all_campaign_ids),
            "remainingToTarget": target - len(all_campaign_ids),
        },
        "promotions": [
            {"candidateId": candidate_id, "finalCanonicalId": mapping[candidate_id]}
            for candidate_id in selected_ids
        ],
        "sha256": {
            str(path): {
                "before": file_sha(path) if path.exists() else None,
                "after": sha_bytes(content),
            }
            for path, content in documents.items()
        },
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    if not args.apply:
        return
    replace_four_atomically(documents)
    for path, content in documents.items():
        if path.read_bytes() != content:
            raise PromotionError(f"final readback mismatch: {path}")
    print(json.dumps({
        "status": "applied",
        "campaignId": campaign,
        "waveIndex": wave,
        "promotedCanonicalIds": new_final_ids,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except PromotionError as exc:
        raise SystemExit(f"promotion blocked: {exc}")
