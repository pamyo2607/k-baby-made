#!/usr/bin/env python3
"""Fail closed on canonical, generated-asset, and evidence invariants."""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from data_contract import current_sale_count, is_current_sale

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SOURCE = DATA / "master-products.json"
ORDER = DATA / "sheet-sync-order.json"
TOMBSTONES = DATA / "deleted-duplicate-tombstones.json"
BOOTSTRAP_REPORT = DATA / "bootstrap-report.json"
SHEET_RECOVERY_QUARANTINE = DATA / "sheet-recovery-quarantine.json"
ING_CAMPAIGN = DATA / "campaign-ing-revalidation.json"
ING_PROOF = DATA / "ing-revalidation-proof.json"
CANONICAL_CATEGORY_ORDER = (
    "완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀",
)
CANONICAL_CATEGORIES = set(CANONICAL_CATEGORY_ORDER)
REMOVED_DUPLICATES = {
    "TOY-20260729-115": "TOY-20260729-024",
    "TOY-20260729-124": "TOY-20260729-123",
    "TOY-20260729-125": "TOY-20260729-053",
    "TOY-20260729-155": "TOY-20260729-154",
    "TEETHER-20260801-015": "RUN18-008",
    "RUN18-011": "MASTER-0195",
    "TEETHER-20260801-005": "MASTER-0195",
    "RUN18-013": "MASTER-0196",
    "RUN18-012": "MASTER-0197",
    "TEETHER-20260801-026": "MASTER-0135",
}
TOMBSTONE_SOURCE_COMMIT = "3159688dda039ba3362c8392bac2fb16a6640371"
TOMBSTONE_SOURCE_CANONICAL_SHA = "0b2338245935229bf67dc03aa8fc5589384a22f236923b54dce9e027a7afa677"
TOMBSTONE_SOURCE_ORDER_SHA = "f5c86a15d90c8eaaa4200f4aa518c355c22380efc417a55da11133455e9228ee"
EXPECTED_TOMBSTONE_IDENTITY = {
    "TOY-20260729-115": (116, "쎄씨", ("Sassy",)),
    "TOY-20260729-124": (125, "해피플레이", ("키저스",)),
    "TOY-20260729-125": (126, "피노키오", ("핑크퐁",)),
    "TOY-20260729-155": (156, "하늘썬별", ("윈펀",)),
    "RUN18-011": (455, "꼬꼬노리", ()),
    "RUN18-012": (456, "꼬꼬노리", ()),
    "RUN18-013": (457, "꼬꼬노리", ()),
    "TEETHER-20260801-005": (206, "꼬꼬노리", ()),
    "TEETHER-20260801-015": (216, "앙쥬", ()),
    "TEETHER-20260801-026": (227, "앙쥬", ()),
}
KC_PATTERN = re.compile(r"^[A-Z]{1,3}\d[A-Z0-9-]*[A-Z0-9]$", re.I)
PLACEHOLDER = re.compile(r"미확인|확인\s*중|확인\s*필요|후보|부족|^-$")
STOP_PATHS = ("/search", "/category", "/categories", "/certificationsearch", "/itemsearch")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def real(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and not PLACEHOLDER.search(text)


def direct_url(value: object) -> bool:
    url = str(value or "")
    if not url.startswith(("https://", "http://")):
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc) and not any(part in parsed.path.lower() for part in STOP_PATHS)


def kc_applies(product: dict) -> bool:
    text = " ".join(str(product.get(key, "")) for key in ("kcApplicable", "regulatoryRegime", "kcType"))
    if re.search(r"비대상|해당\s*없음", text):
        return False
    return bool(re.search(r"어린이제품|완구|안전확인|안전인증|공급자적합|전기용품", text))


def declared_non_kc(product: dict) -> bool:
    text = " ".join(
        str(product.get(key, ""))
        for key in ("kcApplicable", "regulatoryRegime")
    )
    return bool(re.search(r"비대상|해당\s*없음", text))


def age_covers_zero_to_35_months(value: object) -> bool:
    """Require an explicit official age whose allowed range intersects 0-35 months."""
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"신생아|출생", text):
        return True
    if re.search(r"3\s*세\s*미만", text):
        return True
    month_values = [int(value) for value in re.findall(r"(\d+)\s*개월", text)]
    return bool(month_values) and min(month_values) <= 35


def included_errors(product: dict) -> list[str]:
    failures: list[str] = []
    origin = str(product.get("countryOfManufacture", ""))
    age = str(product.get("ageRange", ""))
    people = [product.get("manufacturer"), product.get("importer")]
    for certification in product.get("certifications", []):
        if isinstance(certification, dict):
            people.extend([certification.get("manufacturer"), certification.get("importer")])
    if "대한민국" not in origin and "한국" not in origin:
        failures.append("finished product is not confirmed Korean manufacture")
    if not age_covers_zero_to_35_months(age):
        failures.append("official 0-35 month age is absent")
    if not real(product.get("brand")) or not real(product.get("name")):
        failures.append("exact brand/product identity is absent")
    if not real(product.get("officialModel")):
        failures.append("official model identity is absent")
    if not any(real(value) for value in people):
        failures.append("manufacturer/importer is absent")
    if not real(product.get("regulatoryRegime")):
        failures.append("regulatory regime is absent")
    sale_urls = [
        url for url in product.get("officialUrls", [])
        if "safetykorea.kr" not in str(url) and direct_url(url)
    ]
    if not sale_urls or not is_current_sale(product):
        failures.append("current Korean sale evidence is absent")
    if not str(product.get("reason", "")).strip():
        failures.append("decision reason is absent")
    if product.get("revalidationMissingFields"):
        failures.append("included product still has missingFields")
    if kc_applies(product):
        number = str(product.get("kcNumber", "")).strip()
        if not KC_PATTERN.fullmatch(number):
            failures.append("exact KC number is absent")
        active = [
            cert for cert in product.get("certifications", [])
            if isinstance(cert, dict) and cert.get("found") and cert.get("status") == "적합"
            and "/release/certDetail" in str(cert.get("url", ""))
        ]
        if not active:
            failures.append("active Safety Korea same-model detail is absent")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-external-proof",
        action="store_true",
        help="also require current Google Sheet and production deployment proofs",
    )
    args = parser.parse_args()
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids = [str(product.get("id", "")) for product in products]
    if not all(ids):
        errors.append("blank canonical ID")
    if len(ids) != len(set(ids)):
        errors.append("exact canonical ID duplicate")
    if [product.get("sequence") for product in products] != list(range(1, len(products) + 1)):
        errors.append(f"canonical sequence is not contiguous 1..{len(products)}")

    if SHEET_RECOVERY_QUARANTINE.exists():
        quarantine_doc = json.loads(
            SHEET_RECOVERY_QUARANTINE.read_text(encoding="utf-8")
        )
        quarantine_products = quarantine_doc.get("products", [])
        if not isinstance(quarantine_products, list):
            errors.append("Sheet recovery quarantine products must be an array")
            quarantine_products = []
        quarantine_ids = [
            str(item.get("id", ""))
            for item in quarantine_products
            if isinstance(item, dict)
        ]
        if (
            quarantine_doc.get("status") != "quarantined"
            or quarantine_doc.get("records") != len(quarantine_products)
            or len(quarantine_ids) != len(quarantine_products)
            or not all(quarantine_ids)
            or len(quarantine_ids) != len(set(quarantine_ids))
        ):
            errors.append("Sheet recovery quarantine structure mismatch")
        overlap = sorted(set(ids).intersection(quarantine_ids))
        if overlap:
            errors.append(f"Sheet-only quarantine overlaps canonical IDs: {overlap}")
        bootstrap_report = json.loads(
            BOOTSTRAP_REPORT.read_text(encoding="utf-8")
        )
        if (
            bootstrap_report.get("addedFromSheet") != 0
            or bootstrap_report.get("quarantinedFromSheet") != len(quarantine_products)
            or bootstrap_report.get("productsAfter") != len(products)
        ):
            errors.append("Sheet recovery quarantine report mismatch")

    by_id = {product.get("id"): product for product in products}
    if ING_PROOF.exists():
        campaign = json.loads(ING_CAMPAIGN.read_text(encoding="utf-8"))
        ing_proof = json.loads(ING_PROOF.read_text(encoding="utf-8"))
        target_ids = [str(value) for value in campaign.get("targetIdsSnapshot", [])]
        attempted_ids = [str(value) for value in campaign.get("attemptedIds", [])]
        remaining_ids = [str(value) for value in campaign.get("remainingIds", [])]
        result_rows = ing_proof.get("results", [])
        if not isinstance(result_rows, list):
            errors.append("ING proof results must be an array")
            result_rows = []
        result_ids = [
            str(item.get("id", "")) for item in result_rows if isinstance(item, dict)
        ]
        expected_attempted = [value for value in target_ids if value in set(result_ids)]
        expected_remaining = [value for value in target_ids if value not in set(result_ids)]
        if (
            not target_ids
            or not all(target_ids)
            or len(target_ids) != len(set(target_ids))
            or any(value not in by_id for value in target_ids)
        ):
            errors.append("ING campaign target IDs are invalid or missing from canonical")
        if (
            len(result_ids) != len(result_rows)
            or not all(result_ids)
            or len(result_ids) != len(set(result_ids))
            or not set(result_ids).issubset(target_ids)
            or attempted_ids != expected_attempted
            or remaining_ids != expected_remaining
        ):
            errors.append("ING proof result coverage/order mismatch")

        status_counts = Counter(str(by_id[value].get("status", "")) for value in target_ids)
        unresolved_ids: list[str] = []
        for product_id in target_ids:
            product = by_id[product_id]
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
            if product.get("safetyKoreaSearchUrl"):
                values.append(product["safetyKoreaSearchUrl"])
            for certification in product.get("certifications", []):
                if isinstance(certification, dict) and certification.get("url"):
                    values.append(certification["url"])
            has_evidence = any(direct_url(value) for value in values)
            if (
                product.get("status") not in {"포함", "제외"}
                or not str(product.get("reason", "")).strip()
                or not has_evidence
            ):
                unresolved_ids.append(product_id)

        for result in result_rows:
            if not isinstance(result, dict):
                continue
            product_id = str(result.get("id", ""))
            product = by_id.get(product_id, {})
            result_urls = result.get("directEvidenceUrls", [])
            if (
                not isinstance(result_urls, list)
                or any(not direct_url(value) for value in result_urls)
                or result.get("finalDecision") != product.get("status")
                or result.get("resolved")
                != (
                    product_id not in unresolved_ids
                    and product.get("status") in {"포함", "제외"}
                )
            ):
                errors.append(f"{product_id}: ING result decision/evidence mismatch")
            audit_refs = result.get("auditRefs", [])
            if not isinstance(audit_refs, list) or not audit_refs:
                errors.append(f"{product_id}: ING result audit refs are absent")
                continue
            for audit_ref in audit_refs:
                relative = Path(str(audit_ref))
                audit_path = ROOT / relative
                if (
                    relative.is_absolute()
                    or relative.parts[:2] != ("data", "revalidation-audits")
                    or not audit_path.exists()
                ):
                    errors.append(f"{product_id}: ING immutable audit is missing")
            last_audit_ref = str(result.get("lastAuditRef", ""))
            last_audit_path = ROOT / last_audit_ref
            if (
                not last_audit_ref
                or not last_audit_path.exists()
                or result.get("lastAuditSha256") != sha(last_audit_path)
            ):
                errors.append(f"{product_id}: ING last audit hash mismatch")

        target_sha = hashlib.sha256(
            (json.dumps(target_ids, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).hexdigest()
        expected_coverage_complete = not remaining_ids
        expected_resolution_complete = expected_coverage_complete and not unresolved_ids
        if any(ing_proof.get(key) != value for key, value in {
            "campaignId": campaign.get("campaignId"),
            "cycleRunId": campaign.get("cycleRunId"),
            "targetCount": len(target_ids),
            "targetIdsSha256": target_sha,
            "attemptedCount": len(attempted_ids),
            "remainingCount": len(remaining_ids),
            "resolvedCount": len(target_ids) - len(unresolved_ids),
            "includedCount": status_counts["포함"],
            "pendingCount": status_counts["보류"],
            "excludedCount": status_counts["제외"],
            "missingTargetIds": remaining_ids,
            "unresolvedIds": unresolved_ids,
            "duplicateResultIds": [],
            "activeDuplicateRecords": sum(bool(item.get("duplicateOf")) for item in products),
            "resultsWithDirectEvidence": sum(bool(item.get("directEvidenceUrls")) for item in result_rows),
            "resultsWithErrors": sum(bool(item.get("errors")) for item in result_rows),
            "coverageComplete": expected_coverage_complete,
            "resolutionComplete": expected_resolution_complete,
        }.items()):
            errors.append("ING campaign proof summary mismatch")
        if (
            campaign.get("coverageComplete") != expected_coverage_complete
            or campaign.get("resolutionComplete") != expected_resolution_complete
            or campaign.get("lastAuditRef") != ing_proof.get("lastAuditRef")
            or campaign.get("lastAuditSha256") != ing_proof.get("lastAuditSha256")
        ):
            errors.append("ING campaign checkpoint/proof mismatch")

    tombstone_doc = json.loads(TOMBSTONES.read_text(encoding="utf-8"))
    if (
        tombstone_doc.get("sourceCommit") != TOMBSTONE_SOURCE_COMMIT
        or tombstone_doc.get("sourceCanonicalSha256") != TOMBSTONE_SOURCE_CANONICAL_SHA
        or tombstone_doc.get("sourceSheetOrderSha256") != TOMBSTONE_SOURCE_ORDER_SHA
    ):
        errors.append("duplicate tombstone source snapshot metadata mismatch")
    tombstone_records = {
        item.get("deletedId"): item for item in tombstone_doc.get("records", [])
    }
    tombstone_map = {
        item.get("deletedId"): item.get("retainedId")
        for item in tombstone_doc.get("records", [])
    }
    if tombstone_map != REMOVED_DUPLICATES:
        errors.append("duplicate tombstone map mismatch")
    for deleted_id, (sheet_row, brand, brand_aliases) in EXPECTED_TOMBSTONE_IDENTITY.items():
        record = tombstone_records.get(deleted_id, {})
        if (
            record.get("formerSheetRow") != sheet_row
            or record.get("brand") != brand
            or tuple(record.get("brandAliases", [])) != brand_aliases
        ):
            errors.append(f"{deleted_id}: tombstone source identity mismatch")
    order_doc = json.loads(ORDER.read_text(encoding="utf-8"))
    order_ids = list(order_doc.get("ids", []))
    if (
        order_doc.get("totalRows") != len(products)
        or len(order_ids) != len(products)
        or len(set(order_ids)) != len(products)
        or set(order_ids) != set(ids)
    ):
        errors.append("Sheet sync order does not match active canonical IDs")
    duplicate_map = {product["id"]: product.get("duplicateOf") for product in products if product.get("duplicateOf")}
    if duplicate_map:
        errors.append(f"active duplicate links remain: {duplicate_map}")
    for product_id, canonical_id in REMOVED_DUPLICATES.items():
        if product_id in by_id:
            errors.append(f"deleted duplicate remains active: {product_id}")
        if canonical_id not in by_id:
            errors.append(f"retained canonical missing: {canonical_id}")
    for product in products:
        expected_canonical = product.get("duplicateOf") or product.get("id")
        if product.get("canonicalProductId") != expected_canonical:
            errors.append(f"{product.get('id')}: canonicalProductId mismatch")
        if product.get("duplicateOf") and product.get("duplicateOf") not in by_id:
            errors.append(f"{product.get('id')}: missing duplicate target")
        if product.get("status") not in {"포함", "보류", "제외"}:
            errors.append(f"{product.get('id')}: invalid status")
        if product.get("category") not in CANONICAL_CATEGORIES:
            errors.append(f"{product.get('id')}: noncanonical category {product.get('category')!r}")
        if product.get("status") == "보류" and not product.get("revalidationMissingFields"):
            errors.append(f"{product.get('id')}: pending product has no structured missingFields")
        if product.get("status") == "포함":
            for failure in included_errors(product):
                errors.append(f"{product.get('id')}: {failure}")
        if product.get("status") == "제외" and not str(product.get("reason", "")).strip():
            errors.append(f"{product.get('id')}: excluded reason is absent")
        if product.get("status") == "제외" and (
            product.get("statusDisplay") != "기준 제외"
            or product.get("strict419Status") != "기준 제외"
        ):
            errors.append(f"{product.get('id')}: excluded display still mentions duplicate")
        if declared_non_kc(product):
            if str(product.get("kcNumber", "")).strip() or product.get("certifications"):
                errors.append(f"{product.get('id')}: non-KC product retains certification data")
            if str(product.get("safetyKoreaSearchUrl", "")).strip():
                errors.append(f"{product.get('id')}: non-KC product retains Safety Korea URL")
            if product.get("certStatusSummary") != "KC 비대상":
                errors.append(f"{product.get('id')}: non-KC status summary mismatch")
            if product.get("certificationEvidenceLevel") != "not-applicable":
                errors.append(f"{product.get('id')}: non-KC evidence level mismatch")

    nested_text = lambda value: json.dumps(value, ensure_ascii=False)
    if "뽀득뽀득 싱크대" in nested_text(by_id.get("TOY-20260729-123", {})):
        errors.append("TOY-20260729-123: unrelated certification model remains")
    toy_154 = by_id.get("TOY-20260729-154", {})
    if any(
        str(cert.get("manufacturer", "")).strip() == "확인중"
        for cert in toy_154.get("certifications", []) if isinstance(cert, dict)
    ) or any(
        str(evidence.get("fields", {}).get("manufacturer", "")).strip() == "확인중"
        for evidence in toy_154.get("ocrEvidence", []) if isinstance(evidence, dict)
    ):
        errors.append("TOY-20260729-154: placeholder manufacturer evidence remains")
    master_195 = by_id.get("MASTER-0195", {})
    master_195_text = nested_text(master_195)
    if any(value in master_195_text for value in (
        "낮은가격", "사용후기", "live.ecomm-data.com/report/labang/ff175af2a4554c5c01755fb2c9037237",
    )):
        errors.append("MASTER-0195: unrelated merged evidence remains")
    master_195_people = [master_195.get("manufacturer"), master_195.get("importer")]
    for certification in master_195.get("certifications", []):
        if isinstance(certification, dict):
            master_195_people.extend([certification.get("manufacturer"), certification.get("importer")])
    if (
        master_195.get("status") == "보류"
        and not any(real(value) for value in master_195_people)
        and "manufacturerOrImporter" not in master_195.get("revalidationMissingFields", [])
    ):
        errors.append("MASTER-0195: manufacturer/importer blocker is not structured")

    unique_products = [product for product in products if not product.get("duplicateOf")]
    duplicate_count = len(products) - len(unique_products)
    unique_status = Counter(product.get("status") for product in unique_products)
    raw_status = Counter(product.get("status") for product in products)
    expected_status = {key: unique_status[key] for key in ("포함", "보류", "제외")}
    expected_raw_status = {key: raw_status[key] for key in ("포함", "보류", "제외")}
    category_counts = Counter(product.get("category") for product in unique_products)
    expected_categories = {
        category: category_counts[category] for category in CANONICAL_CATEGORY_ORDER
    }
    if len(products) != len(unique_products) + duplicate_count:
        errors.append(
            f"raw/unique/duplicate invariant failed: "
            f"{len(products)}/{len(unique_products)}/{duplicate_count}"
        )
    if duplicate_count != 0:
        errors.append(f"active duplicate count must be zero, got {duplicate_count}")
    if sum(unique_status.values()) != len(unique_products) or sum(raw_status.values()) != len(products):
        errors.append("status totals do not reconcile")
    expected_current_sale = current_sale_count(unique_products)
    expected_strict_target = unique_status["포함"] + unique_status["보류"]
    compact_products = json.dumps(
        products, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    expected_data_sha = hashlib.sha256(compact_products).hexdigest()

    fallback_paths = [
        DATA / "fallback-products.json",
        ROOT / "fallback-products.json",
        PUBLIC / "fallback-products.json",
        PUBLIC / "data/fallback-products.json",
    ]
    fallback_hashes = {sha(path) for path in fallback_paths}
    if len(fallback_hashes) != 1:
        errors.append("fallback JSON copies differ")
    for path in fallback_paths:
        if json.loads(path.read_text(encoding="utf-8")) != products:
            errors.append(f"{path.relative_to(ROOT)} does not equal canonical products")

    script_paths = [ROOT / "kbaby-data.js", PUBLIC / "kbaby-data.js"]
    if sha(script_paths[0]) != sha(script_paths[1]):
        errors.append("kbaby-data.js copies differ")
    script = script_paths[0].read_text(encoding="utf-8")
    prefix = "window.KBABY_DATA="
    if not script.startswith(prefix) or ";\nwindow.KBABY_DATA_READY" not in script:
        errors.append("embedded data wrapper is invalid")
        embedded = {}
    else:
        embedded = json.loads(script[len(prefix):].split(";\nwindow.KBABY_DATA_READY", 1)[0])
        decoded = gzip.decompress(base64.b64decode(embedded["fallback"]["data"]))
        if json.loads(decoded) != products:
            errors.append("embedded fallback does not equal canonical products")
        if hashlib.sha256(decoded).hexdigest() != expected_data_sha:
            errors.append("embedded data SHA mismatch")

    csv_paths = [DATA / "master-db-419-final.csv", PUBLIC / "data/master-db-419-final.csv", PUBLIC / "master-db-sync.csv"]
    if len({sha(path) for path in csv_paths}) != 1:
        errors.append("CSV copies differ")
    with csv_paths[0].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected_csv_sha = sha(csv_paths[0])
    if len(rows) != len(products) or [row.get("ID") for row in rows] != ids:
        errors.append("CSV row IDs do not match canonical order")
    if not {"duplicateOf", "canonicalProductId", "KC 제조사", "KC 수입업체", "KC 인증변경사유", "Safety Korea 상세 URL"}.issubset(rows[0]):
        errors.append("CSV required bridge/duplicate headers are absent")
    for row, product in zip(rows, products):
        if row.get("현재 판정") != product.get("status") or row.get("duplicateOf", "") != product.get("duplicateOf", ""):
            errors.append(f"{product.get('id')}: CSV decision/duplicate mismatch")

    meta_paths = [DATA / "meta.json", ROOT / "meta.json", PUBLIC / "data/meta.json", PUBLIC / "meta.json"]
    if len({sha(path) for path in meta_paths}) != 1:
        errors.append("meta JSON copies differ")
    meta = json.loads(meta_paths[0].read_text(encoding="utf-8"))
    expected_build = str(meta.get("build", "")).strip()
    if not expected_build:
        errors.append("meta build is blank")
    expected_counts = {
        "rawRecords": len(products),
        "totalInvestigated": len(unique_products),
        "uniqueProducts": len(unique_products),
        "currentSale": expected_current_sale,
        "included": unique_status["포함"],
        "pending": unique_status["보류"],
        "excluded": unique_status["제외"],
        "duplicateRecords": duplicate_count,
        "excludedAndDuplicate": unique_status["제외"] + duplicate_count,
        "fullRevalidationTargetRows": expected_strict_target,
        "statusCounts": expected_status,
        "rawStatusCounts": expected_raw_status,
        "categories": expected_categories,
        "dataSha256": expected_data_sha,
        "canonicalSha256": sha(SOURCE),
        "csvSha256": expected_csv_sha,
    }
    for key, value in expected_counts.items():
        if meta.get(key) != value:
            errors.append(f"meta {key} {meta.get(key)} != {value}")
    expected_validation = {
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": duplicate_count,
        "currentSale": expected_current_sale,
        "fullRevalidationTargetRows": expected_strict_target,
        "strict419TargetRows": expected_strict_target,
        "statusCounts": expected_status,
        "rawStatusCounts": expected_raw_status,
        "sha256": expected_data_sha,
        "fallbackSha256": expected_data_sha,
        "csvSha256": expected_csv_sha,
    }
    if embedded:
        if embedded.get("build") != expected_build:
            errors.append("embedded build mismatch")
        if embedded.get("meta") != meta:
            errors.append("embedded meta does not equal generated meta")
        embedded_validation = embedded.get("validation", {})
        for key, value in expected_validation.items():
            if embedded_validation.get(key) != value:
                errors.append(f"embedded validation {key} mismatch")

    health_paths = [ROOT / "health.json", PUBLIC / "health.json"]
    if sha(health_paths[0]) != sha(health_paths[1]):
        errors.append("health JSON copies differ")
    health = json.loads(health_paths[0].read_text(encoding="utf-8"))
    if health.get("build") != expected_build or health.get("status") != "ok":
        errors.append("health build/status mismatch")
    for key, value in expected_validation.items():
        if health.get(key) != value:
            errors.append(f"health {key} mismatch")

    missing_report = json.loads((DATA / "missing-fields-report.json").read_text(encoding="utf-8"))
    queue = missing_report.get("queue", [])
    queue_copy = json.loads((DATA / "revalidation-queue.json").read_text(encoding="utf-8"))
    pending_by_id = {
        product["id"]: product for product in unique_products if product.get("status") == "보류"
    }
    if missing_report.get("pendingProducts") != unique_status["보류"]:
        errors.append("missingFields report pending count mismatch")
    if missing_report.get("pendingWithStructuredMissingFields") != unique_status["보류"]:
        errors.append("not every pending product has structured missingFields")
    if missing_report.get("build") != expected_build:
        errors.append("missingFields report build mismatch")
    if queue_copy != queue:
        errors.append("revalidation queue copies differ")
    queue_ids = [item.get("id") for item in queue if isinstance(item, dict)]
    if (
        len(queue) != unique_status["보류"]
        or len(queue_ids) != len(queue)
        or len(set(queue_ids)) != len(queue_ids)
        or set(queue_ids) != set(pending_by_id)
    ):
        errors.append("revalidation queue does not match current pending products")
    for item in queue:
        if not isinstance(item, dict):
            continue
        product = pending_by_id.get(item.get("id"))
        if product is None:
            continue
        fields = list(product.get("revalidationMissingFields", []))
        if item.get("missingFields") != fields or item.get("priority") != len(fields):
            errors.append(f"{product.get('id')}: revalidation queue fields mismatch")

    proof = json.loads((DATA / "strict-419-production-final-proof.json").read_text(encoding="utf-8"))
    if any(proof.get(key) != value for key, value in {
        "build": expected_build,
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": duplicate_count,
        "currentSale": expected_current_sale,
        "strict419TargetRows": expected_strict_target,
        "uniqueIncluded": unique_status["포함"],
        "uniquePending": unique_status["보류"],
        "uniqueExcluded": unique_status["제외"],
        "verifiedTotal": len(unique_products),
        "includedTile": unique_status["포함"],
        "pendingTile": unique_status["보류"],
        "excludedAndDuplicateTile": unique_status["제외"] + duplicate_count,
        "dataSha256": expected_data_sha,
        "csvSha256": expected_csv_sha,
    }.items()):
        errors.append("generated production proof mismatch")

    if args.require_external_proof:
        sheet_proof = json.loads((DATA / "google-sheet-sync-proof.json").read_text(encoding="utf-8"))
        expected_sheet_after = {
            "rawRecords": len(products),
            "distinctIds": len(set(ids)),
            "uniqueProducts": len(unique_products),
            "currentSale": expected_current_sale,
            "included": unique_status["포함"],
            "pending": unique_status["보류"],
            "excluded": unique_status["제외"],
            "duplicateRecords": duplicate_count,
            "excludedAndDuplicate": unique_status["제외"] + duplicate_count,
            "fullRevalidationTargetRows": expected_strict_target,
            "revalidationQueueRows": unique_status["보류"],
            "revalidationQueueDistinctIds": unique_status["보류"],
        }
        sheet_after = sheet_proof.get("after", {})
        if (
            sheet_proof.get("status") != "passed"
            or sheet_proof.get("build") != expected_build
            or sheet_proof.get("duplicateMap") != {}
            or sheet_proof.get("deletedDuplicateMap") != REMOVED_DUPLICATES
            or any(sheet_after.get(key) != value for key, value in expected_sheet_after.items())
            or sheet_proof.get("readback", {}).get("masterDb", {}).get("cellDiffs") != 0
            or sheet_proof.get("readback", {}).get("strictSync", {}).get("cellDiffs") != 0
            or sheet_proof.get("readback", {}).get("revalidationQueue", {}).get("cellDiffs") != 0
            or sheet_proof.get("dataSha256") != expected_data_sha
            or sheet_proof.get("canonicalSha256") != sha(SOURCE)
            or sheet_proof.get("csvSha256") != expected_csv_sha
        ):
            errors.append("Google Sheet current sync proof mismatch")

    deletion_proof = json.loads((DATA / "duplicate-deletion-proof.json").read_text(encoding="utf-8"))
    deletion_before = deletion_proof.get("before", {})
    deletion_after = deletion_proof.get("after", {})
    historical_after_records = (
        deletion_before.get("rawRecords", 0) - deletion_proof.get("deletedRecords", 0)
    )
    if (
        deletion_proof.get("sourceCommit") != TOMBSTONE_SOURCE_COMMIT
        or deletion_proof.get("deletedRecords") != len(REMOVED_DUPLICATES)
        or deletion_proof.get("activeDuplicateRecords") != 0
        or deletion_before.get("duplicateRecords") != len(REMOVED_DUPLICATES)
        or deletion_after.get("rawRecords") != historical_after_records
        or deletion_after.get("uniqueProducts") != historical_after_records
        or deletion_after.get("duplicateRecords") != 0
        or sum(deletion_after.get(key, 0) for key in ("included", "pending", "excluded"))
        != historical_after_records
        or deletion_proof.get("sheetReadback", {}).get("deletedIdsFound") != 0
    ):
        errors.append("duplicate deletion proof mismatch")
    if args.require_external_proof and (
        deletion_proof.get("deploymentVerification") != "passed"
        or deletion_proof.get("sheetReadback", {}).get("masterCellDiffs") != 0
        or deletion_proof.get("sheetReadback", {}).get("strictSyncCellDiffs") != 0
        or deletion_proof.get("sheetReadback", {}).get("queueCellDiffs") != 0
        or deletion_proof.get("production", {}).get("liveRows") != historical_after_records
        or deletion_proof.get("production", {}).get("liveDuplicateRows") != 0
        or deletion_proof.get("production", {}).get("liveDeletedIdsFound") != 0
    ):
        errors.append("external duplicate deletion proof mismatch")

    if args.require_external_proof:
        production_proof = json.loads(
            (DATA / "codex-production-verification.json").read_text(encoding="utf-8")
        )
        production_expected = {
            "build": expected_build,
            "rawRecords": len(products),
            "uniqueProducts": len(unique_products),
            "duplicateRecords": duplicate_count,
            "currentSale": expected_current_sale,
            "strict419TargetRows": expected_strict_target,
            "uniqueIncluded": unique_status["포함"],
            "uniquePending": unique_status["보류"],
            "uniqueExcluded": unique_status["제외"],
            "verifiedTotal": len(unique_products),
            "includedTile": unique_status["포함"],
            "pendingTile": unique_status["보류"],
            "excludedAndDuplicateTile": unique_status["제외"] + duplicate_count,
            "dataSha256": expected_data_sha,
            "csvSha256": expected_csv_sha,
        }
        deployment = production_proof.get("verifiedDeployment", {})
        browser = deployment.get("browser", {})
        expected_browser = {
            "total": len(unique_products),
            "currentSale": expected_current_sale,
            "included": unique_status["포함"],
            "pending": unique_status["보류"],
            "excludedAndDuplicate": unique_status["제외"] + duplicate_count,
        }
        deployed_sha = deployment.get("sha256", {})
        expected_deployed_sha = {
            "kbaby-data.js": sha(ROOT / "kbaby-data.js"),
            "csv": expected_csv_sha,
            "fallback-products.json": sha(DATA / "fallback-products.json"),
            "health.json": sha(ROOT / "health.json"),
            "meta.json": sha(DATA / "meta.json"),
        }
        if (
            production_proof.get("status") != "passed"
            or production_proof.get("deploymentVerification") != "passed"
            or production_proof.get("liveConnected") is not True
            or any(production_proof.get(key) != value for key, value in production_expected.items())
            or deployment.get("artifactMatchesCurrentBuild") is not True
            or any(browser.get(key) != value for key, value in expected_browser.items())
            or any(deployed_sha.get(key) != value for key, value in expected_deployed_sha.items())
        ):
            errors.append("production deployment proof mismatch")

    report = {
        "status": "failed" if errors else "passed",
        "build": expected_build,
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": duplicate_count,
        "uniqueStatusCounts": {key: unique_status[key] for key in ("포함", "보류", "제외")},
        "rawStatusCounts": {key: raw_status[key] for key in ("포함", "보류", "제외")},
        "externalProofRequired": args.require_external_proof,
        "violations": errors,
    }
    (DATA / "strict-validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
