#!/usr/bin/env python3
"""Recover the verified 459-row production payload without losing partial-main work.

The deployed gzip payload is the only complete surviving canonical dataset.  This
script verifies its exact baseline hash, preserves the previous partial recovery,
quarantines non-product DISC candidates, applies reviewed evidence overrides, and
regenerates structured missing-field keys.  It is intentionally not part of the
normal build because recovery must never depend on mutable network state.
"""
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
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCE = DATA / "master-products.json"
OVERRIDES = DATA / "codex-revalidation-overrides.json"
LIVE_URL = "https://k-baby-made.pamyo-2607.workers.dev/kbaby-data.js?recovery=20260815"
LIVE_CSV_URL = "https://k-baby-made.pamyo-2607.workers.dev/data/master-db-419-final.csv?recovery=20260815"
EXPECTED_BUILD = "20260804-strict419-fix1"
EXPECTED_RAW = 459
EXPECTED_SOURCE_UNIQUE = 455
EXPECTED_UNIQUE = 449
EXPECTED_DATA_SHA = "077d487e57f585c2954de2639922ec4fb9aefb7e28e8de71fb351a3699b528b7"
EXPECTED_SCRIPT_SHA = "3acd800d737b9077e5fdcb5a2fd148c6f4ba03ad1ce9c564654ddd8ac3404088"
EXPECTED_CSV_SHA = "9edfbc3526cfb38e297b3ad9893fcdf6573100cd1f1a8a0a9688ca0cb69bde43"
KC_PATTERN = re.compile(r"^[A-Z]{1,3}\d[A-Z0-9-]*[A-Z0-9]$", re.I)
PLACEHOLDER = re.compile(r"미확인|확인\s*중|확인\s*필요|후보|부족|해당\s*없음|^-$")
GENERIC_PATHS = ("/search", "/category", "/categories", "/certificationsearch", "/itemsearch")
DUPLICATES = {
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
NEWLY_STRUCTURED_DUPLICATES = {
    "TEETHER-20260801-015", "RUN18-011", "TEETHER-20260801-005",
    "RUN18-013", "RUN18-012", "TEETHER-20260801-026",
}


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_script(path: str | None) -> bytes:
    if path:
        return Path(path).read_bytes()
    request = Request(LIVE_URL, headers={"User-Agent": "KBabyMadeRecovery/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def decode_script(script: bytes) -> tuple[dict, list[dict], bytes]:
    script_sha = hashlib.sha256(script).hexdigest()
    if script_sha != EXPECTED_SCRIPT_SHA:
        raise SystemExit(f"unexpected live script SHA: {script_sha}")
    text = script.decode("utf-8")
    prefix = "window.KBABY_DATA="
    if not text.startswith(prefix) or ";\nwindow.KBABY_DATA_READY" not in text:
        raise SystemExit("unrecognized production payload wrapper")
    payload = json.loads(text[len(prefix):].split(";\nwindow.KBABY_DATA_READY", 1)[0])
    if payload.get("build") != EXPECTED_BUILD:
        raise SystemExit(f"unexpected production build: {payload.get('build')}")
    compressed = base64.b64decode(payload["fallback"]["data"])
    raw = gzip.decompress(compressed)
    data_sha = hashlib.sha256(raw).hexdigest()
    if data_sha != EXPECTED_DATA_SHA:
        raise SystemExit(f"unexpected decoded data SHA: {data_sha}")
    products = json.loads(raw)
    if len(products) != EXPECTED_RAW:
        raise SystemExit(f"unexpected production rows: {len(products)}")
    return payload, products, raw


def read_verified_csv(path: str | None) -> list[dict[str, str]]:
    if path:
        content = Path(path).read_bytes()
    else:
        request = Request(LIVE_CSV_URL, headers={"User-Agent": "KBabyMadeRecovery/1.0"})
        with urlopen(request, timeout=30) as response:
            content = response.read()
    csv_sha = hashlib.sha256(content).hexdigest()
    if csv_sha != EXPECTED_CSV_SHA:
        raise SystemExit(f"unexpected production CSV SHA: {csv_sha}")
    text = content.decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    if len(rows) != EXPECTED_RAW or any(not row.get("ID") for row in rows):
        raise SystemExit(f"unexpected production CSV rows: {len(rows)}")
    return rows


def split_values(value: object) -> list[str]:
    return [item.strip() for item in re.split(r"\r?\n|\s*[;,|]\s*", str(value or "")) if item.strip()]


def merge_verified_csv(products: list[dict], rows: list[dict[str, str]]) -> None:
    by_id = {row["ID"]: row for row in rows}
    for product in products:
        row = by_id.get(product["id"])
        if not row:
            raise SystemExit(f"production CSV missing product: {product['id']}")
        regime = row.get("적용 안전제도", "").strip()
        note = row.get("규제 기준 메모", "").strip()
        cert_number = row.get("KC 인증번호", "").strip()
        cert_status = row.get("KC 인증상태", "").strip()
        cert_date = row.get("KC 인증일자", "").strip()
        changed_date = row.get("KC 인증변경일자", "").strip()
        changed_reason = row.get("KC 인증변경사유", "").strip()
        cert_type = row.get("KC 인증구분", "").strip()
        authority = row.get("KC 인증기관", "").strip()
        detail_url = row.get("Safety Korea 상세 URL", "").strip()
        item_name = row.get("KC 품목명", "").strip()
        model_name = row.get("KC 모델명", "").strip()
        manufacturer = row.get("KC 제조사", "").strip()
        importer = row.get("KC 수입업체", "").strip()
        if regime:
            product["regulatoryRegime"] = regime
        elif kc_applies(product) and not real(product.get("regulatoryRegime")):
            product["regulatoryRegime"] = "어린이제품안전특별법 · 제품별 안전확인 인증 조회"
        if note:
            product["regulatoryNote"] = note
        if cert_number and not PLACEHOLDER.search(cert_number):
            product["kcNumber"] = cert_number
        if cert_type:
            product["kcType"] = cert_type
        if authority:
            product["testInstitute"] = authority
        if model_name:
            product["officialModel"] = model_name
        if manufacturer:
            product["manufacturer"] = manufacturer
        if importer:
            product["importer"] = importer
        if detail_url:
            product["safetyKoreaSearchUrl"] = detail_url
        product["certStatusSummary"] = cert_status or product.get("certStatusSummary", "")
        product["certDateSummary"] = cert_date or product.get("certDateSummary", "")
        product["certTypeSummary"] = cert_type or product.get("certTypeSummary", "")
        product["certAuthoritySummary"] = authority or product.get("certAuthoritySummary", "")
        product["certChangedDateSummary"] = changed_date or product.get("certChangedDateSummary", "")
        product["certChangedReasonSummary"] = changed_reason or product.get("certChangedReasonSummary", "")
        has_detail = bool(cert_number and any((cert_status, cert_date, cert_type, authority, model_name, manufacturer, importer)))
        if has_detail:
            related = [
                {"certNumber": number, "status": "확인 필요"}
                for number in split_values(row.get("연관 인증번호", ""))
            ]
            product["certifications"] = [{
                "found": True,
                "certNumber": cert_number,
                "status": cert_status or "확인 필요",
                "certDate": cert_date,
                "changedDate": changed_date,
                "certType": cert_type,
                "authority": authority,
                "changedReason": changed_reason,
                "recallStatus": "",
                "itemName": item_name,
                "modelName": model_name,
                "manufacturer": manufacturer,
                "country": product.get("countryOfManufacture", ""),
                "importer": importer,
                "classification": "",
                "relatedCertificates": related,
                "url": detail_url,
            }]
            product["activeCertificateCount"] = int(cert_status == "적합")
            product["expiredCertificateCount"] = int(cert_status == "기간만료")


def unique_urls(*groups: object) -> list[str]:
    output: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for value in group:
            url = str(value or "").strip()
            if url.startswith(("https://", "http://")) and url not in output:
                output.append(url)
    return output


def direct_url(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc) and not any(part in parsed.path.lower() for part in GENERIC_PATHS)


def real(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and not PLACEHOLDER.search(text)


def cert_people(product: dict) -> list[str]:
    people = [str(product.get("manufacturer", "")), str(product.get("importer", ""))]
    for cert in product.get("certifications", []):
        if isinstance(cert, dict):
            people.extend([str(cert.get("manufacturer", "")), str(cert.get("importer", ""))])
    return [value for value in people if real(value)]


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


def normalize_non_kc_certification(product: dict) -> None:
    """Remove inherited Safety Korea placeholders from explicit non-KC rows."""
    if not declared_non_kc(product):
        return
    product.update({
        "kcNumber": "",
        "kcType": "",
        "testInstitute": "",
        "safetyKoreaSearchUrl": "",
        "certStatusSummary": "KC 비대상",
        "certDateSummary": "",
        "certTypeSummary": "",
        "certAuthoritySummary": "",
        "certChangedDateSummary": "",
        "certChangedReasonSummary": "",
        "activeCertificateCount": 0,
        "expiredCertificateCount": 0,
        "certifications": [],
        "certificationEvidenceLevel": "not-applicable",
    })


def missing_fields(product: dict) -> list[str]:
    if product.get("status") == "제외" or product.get("duplicateOf"):
        return []
    missing: list[str] = []
    sale_status = str(product.get("saleStatus", ""))
    non_safety_urls = [
        url for url in unique_urls(product.get("officialUrls"), product.get("saleUrls"))
        if "safetykorea.kr" not in url and direct_url(url)
    ]
    if not non_safety_urls or not real(sale_status) or re.search(r"종료|단종|직구|구매\s*불가|품절", sale_status):
        missing.append("currentSale")
    age = str(product.get("ageRange", ""))
    if not real(age) or not re.search(r"\d+\s*개월|3\s*세\s*미만|신생아|출생", age):
        missing.append("officialAge")
    if not real(product.get("countryOfManufacture")):
        missing.append("countryOfManufacture")
    if not cert_people(product):
        missing.append("manufacturerOrImporter")
    if not real(product.get("regulatoryRegime")):
        missing.append("regulatoryRegime")
    if not any(direct_url(url) for url in unique_urls(product.get("officialUrls"))):
        missing.append("officialEvidence")
    if kc_applies(product):
        number = str(product.get("kcNumber", "")).strip()
        if not KC_PATTERN.fullmatch(number):
            missing.append("exactKcNumber")
        active = [
            cert for cert in product.get("certifications", [])
            if isinstance(cert, dict) and cert.get("found") and cert.get("status") == "적합"
            and "/release/certDetail" in str(cert.get("url", ""))
        ]
        if not active:
            missing.append("safetyKoreaSameModel")
    if product.get("status") == "보류" and not missing:
        missing.append("sameProductIdentity")
    return missing


def apply_override(product: dict, change: dict) -> None:
    product.update(change.get("fields", {}))
    product["officialUrls"] = unique_urls(change.get("officialUrls"), product.get("officialUrls"))
    product["saleUrls"] = unique_urls(change.get("saleUrls"), product.get("saleUrls"), change.get("officialUrls"))
    removed_urls = {
        str(value).strip() for value in change.get("removeUrls", []) if str(value).strip()
    }
    if removed_urls:
        product["officialUrls"] = [
            url for url in product["officialUrls"] if url not in removed_urls
        ]
        product["saleUrls"] = [
            url for url in product["saleUrls"] if url not in removed_urls
        ]
    certification = change.get("certification")
    if isinstance(certification, dict):
        product["certifications"] = [certification]
    history_entry = str(change.get("historyEntry", "")).strip()
    if history_entry and history_entry not in str(product.get("historySummary", "")):
        history = str(product.get("historySummary", "")).strip()
        product["historySummary"] = " | ".join(value for value in (history, history_entry) if value)
    product["revalidationVersion"] = "20260815-codex-live-recovery1"
    product["revalidationAttempted"] = True
    product["revalidationResolved"] = product.get("status") in {"포함", "제외"}
    product["revalidationState"] = "검증 완료" if product["revalidationResolved"] else "공식 근거 추가 확인 중"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="previously downloaded production kbaby-data.js")
    parser.add_argument("--csv", help="previously downloaded verified production CSV")
    args = parser.parse_args()

    script = read_script(args.source)
    payload, products, raw = decode_script(script)
    merge_verified_csv(products, read_verified_csv(args.csv))
    previous = json.loads(SOURCE.read_text(encoding="utf-8")) if SOURCE.exists() else []
    previous_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest() if SOURCE.exists() else ""

    snapshot = DATA / "recovery" / "partial-main-01900b1-master-products.json.gz"
    if previous and not snapshot.exists():
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(gzip.compress(
            (json.dumps(previous, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
            compresslevel=9,
            mtime=0,
        ))

    live_ids = {item["id"] for item in products}
    quarantine = [item for item in previous if item.get("id") not in live_ids]
    quarantine_path = DATA / "discovered-candidate-quarantine.json"
    if quarantine:
        dump(quarantine_path, {
            "status": "quarantined",
            "reason": "검색 결과 후보가 exact product identity 검증 없이 canonical DB에 편입돼 집계에서 분리함",
            "sourceMainSha": "01900b1050ee866cc646b283c5d63a9fd254a5cb",
            "records": len(quarantine),
            "products": quarantine,
        })
    elif quarantine_path.exists():
        quarantine = json.loads(quarantine_path.read_text(encoding="utf-8")).get("products", [])

    previous_by_id = {item.get("id"): item for item in previous}
    for product in products:
        local = previous_by_id.get(product.get("id"), {})
        product["officialUrls"] = unique_urls(product.get("officialUrls"), local.get("evidenceUrls"))
        product["saleUrls"] = unique_urls(product.get("saleUrls"), local.get("evidenceUrls"))
        product["duplicateOf"] = DUPLICATES.get(product["id"], str(product.get("duplicateOf", "")))
        product["canonicalProductId"] = product["duplicateOf"] or product["id"]

    override_doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in products}
    transitions: Counter[str] = Counter()
    changed_fields: Counter[str] = Counter()
    reviewed_metrics: Counter[str] = Counter()
    for change in override_doc.get("changes", []):
        pid = change["id"]
        if pid not in by_id:
            raise SystemExit(f"override references unknown product: {pid}")
        product = by_id[pid]
        before = {
            key: product.get(key)
            for key in ("status", "kcNumber", "countryOfManufacture", "ageRange", "saleStatus", "manufacturer", "importer")
        }
        apply_override(by_id[pid], change)
        after = {key: product.get(key) for key in before}
        transitions[f"{before['status']}→{after['status']}"] += 1
        for key in before:
            if before[key] != after[key]:
                changed_fields[key] += 1
        fields = change.get("fields", {})
        if "countryOfManufacture" in fields:
            reviewed_metrics["countryOfManufactureReviewed"] += 1
        if "ageRange" in fields or "ageEvidence" in fields:
            reviewed_metrics["officialAgeReviewed"] += 1
        if "saleStatus" in fields and not re.search(r"종료|단종|품절|구매\s*불가", str(fields.get("saleStatus", ""))):
            reviewed_metrics["currentSaleReviewed"] += 1
        if any(key in fields for key in ("manufacturer", "importer")) or isinstance(change.get("certification"), dict):
            reviewed_metrics["manufacturerOrImporterReviewed"] += 1
        new_number = str(after.get("kcNumber") or "").strip()
        if KC_PATTERN.fullmatch(new_number) and before.get("kcNumber") != new_number:
            reviewed_metrics["kcNumberNewOrCorrected"] += 1
        safety_urls = [url for url in change.get("officialUrls", []) if "/release/certDetail" in str(url)]
        uncertainty = re.search(r"포괄|최종 확인|연결이 확인되지", str(fields.get("reason", "")))
        if safety_urls and not uncertainty:
            reviewed_metrics["safetyKoreaSameModelReviewed"] += 1

    normalized_non_kc = 0
    for product in products:
        if declared_non_kc(product):
            normalize_non_kc_certification(product)
            normalized_non_kc += 1
        product["revalidationMissingFields"] = missing_fields(product)
        product["strict419Applied"] = product.get("status") == "보류"
        product["strict419Status"] = (
            "검증 완료" if product.get("status") == "포함"
            else "기준 제외·중복" if product.get("status") == "제외"
            else "추가 확인 중"
        )
        product["statusDisplay"] = (
            "기준 충족" if product.get("status") == "포함"
            else "기준 제외·중복" if product.get("status") == "제외"
            else "추가 확인 중"
        )

    unique_count = sum(not item.get("duplicateOf") for item in products)
    if len(products) != EXPECTED_RAW or unique_count != EXPECTED_UNIQUE:
        raise SystemExit(f"recovery invariant failed: {len(products)}/{unique_count}")
    dump(SOURCE, products)
    dump(DATA / "live-recovery-report.json", {
        "status": "passed",
        "recoveredAt": "2026-08-15T00:00:00+09:00",
        "source": LIVE_URL,
        "sourceBuild": payload["build"],
        "sourceScriptSha256": EXPECTED_SCRIPT_SHA,
        "sourceDecodedSha256": EXPECTED_DATA_SHA,
        "sourceRawRecords": EXPECTED_RAW,
        "sourceUniqueProducts": EXPECTED_SOURCE_UNIQUE,
        "repositoryBaseline": {
            "mainSha": "01900b1050ee866cc646b283c5d63a9fd254a5cb",
            "rawRecords": 350,
            "uniqueProductsAfterKnownDuplicateMapping": 346,
            "duplicateRecords": 4,
            "included": 9,
            "pending": 305,
            "uniqueExcluded": 32,
        },
        "recoveryRunInputSha256": previous_sha,
        "recoveryRunInputRecords": len(previous),
        "quarantinedNonProducts": len(quarantine),
        "reviewedOverrides": len(override_doc.get("changes", [])),
        "normalizedNonKcRecords": normalized_non_kc,
        "newlyStructuredDuplicateRecords": len(NEWLY_STRUCTURED_DUPLICATES),
        "statusTransitions": dict(sorted(transitions.items())),
        "changedFieldCounts": dict(sorted(changed_fields.items())),
        "reviewedEvidenceMetrics": dict(sorted(reviewed_metrics.items())),
        "finalRawRecords": len(products),
        "finalUniqueProducts": unique_count,
        "duplicateRecords": sum(bool(item.get("duplicateOf")) for item in products),
    })
    print(json.dumps({
        "raw": len(products),
        "unique": unique_count,
        "quarantined": len(quarantine),
        "overrides": len(override_doc.get("changes", [])),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
