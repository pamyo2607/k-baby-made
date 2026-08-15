#!/usr/bin/env python3
"""Fail closed on canonical, generated-asset, and evidence invariants."""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SOURCE = DATA / "master-products.json"
EXPECTED_BUILD = "20260815-live459-recovery1"
MINIMUM_RECOVERED_RECORDS = 459
CANONICAL_CATEGORIES = {
    "완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀",
}
EXPECTED_DUPLICATES = {
    "TOY-20260729-115": "TOY-20260729-024",
    "TOY-20260729-124": "TOY-20260729-123",
    "TOY-20260729-125": "TOY-20260729-053",
    "TOY-20260729-155": "TOY-20260729-154",
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
    if not re.search(r"\d+\s*개월|3\s*세\s*미만|신생아|출생", age) or re.search(r"36\s*개월\s*이상|3\s*세\s*이상", age):
        failures.append("official 0-35 month age is absent")
    if not any(real(value) for value in people):
        failures.append("manufacturer/importer is absent")
    if not real(product.get("regulatoryRegime")):
        failures.append("regulatory regime is absent")
    sale_urls = [
        url for url in product.get("officialUrls", [])
        if "safetykorea.kr" not in str(url) and direct_url(url)
    ]
    if not sale_urls or re.search(r"종료|단종|직구|품절|구매\s*불가", str(product.get("saleStatus", ""))):
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
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids = [str(product.get("id", "")) for product in products]
    if not all(ids):
        errors.append("blank canonical ID")
    if len(ids) != len(set(ids)):
        errors.append("exact canonical ID duplicate")
    if len(products) < MINIMUM_RECOVERED_RECORDS:
        errors.append(
            f"raw count {len(products)} is below recovered floor {MINIMUM_RECOVERED_RECORDS}"
        )

    by_id = {product.get("id"): product for product in products}
    duplicate_map = {product["id"]: product.get("duplicateOf") for product in products if product.get("duplicateOf")}
    for product_id, canonical_id in EXPECTED_DUPLICATES.items():
        if duplicate_map.get(product_id) != canonical_id:
            errors.append(
                f"known duplicate mapping mismatch: {product_id} -> "
                f"{duplicate_map.get(product_id)!r}, expected {canonical_id!r}"
            )
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

    unique_products = [product for product in products if not product.get("duplicateOf")]
    duplicate_count = len(products) - len(unique_products)
    unique_status = Counter(product.get("status") for product in unique_products)
    raw_status = Counter(product.get("status") for product in products)
    if len(products) != len(unique_products) + duplicate_count:
        errors.append(
            f"raw/unique/duplicate invariant failed: "
            f"{len(products)}/{len(unique_products)}/{duplicate_count}"
        )
    if duplicate_count < len(EXPECTED_DUPLICATES):
        errors.append(f"duplicate count {duplicate_count} is below known floor 4")
    if sum(unique_status.values()) != len(unique_products) or sum(raw_status.values()) != len(products):
        errors.append("status totals do not reconcile")

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
        if hashlib.sha256(decoded).hexdigest() != embedded["validation"].get("sha256"):
            errors.append("embedded data SHA mismatch")
        if embedded.get("build") != EXPECTED_BUILD:
            errors.append("embedded build mismatch")

    csv_paths = [DATA / "master-db-419-final.csv", PUBLIC / "data/master-db-419-final.csv", PUBLIC / "master-db-sync.csv"]
    if len({sha(path) for path in csv_paths}) != 1:
        errors.append("CSV copies differ")
    with csv_paths[0].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
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
    expected_counts = {
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "included": unique_status["포함"],
        "pending": unique_status["보류"],
        "excluded": unique_status["제외"],
        "duplicateRecords": duplicate_count,
        "excludedAndDuplicate": unique_status["제외"] + duplicate_count,
    }
    for key, value in expected_counts.items():
        if meta.get(key) != value:
            errors.append(f"meta {key} {meta.get(key)} != {value}")
    if meta.get("build") != EXPECTED_BUILD:
        errors.append("meta build mismatch")

    health_paths = [ROOT / "health.json", PUBLIC / "health.json"]
    if sha(health_paths[0]) != sha(health_paths[1]):
        errors.append("health JSON copies differ")
    health = json.loads(health_paths[0].read_text(encoding="utf-8"))
    if health.get("build") != EXPECTED_BUILD or health.get("status") != "ok":
        errors.append("health build/status mismatch")

    missing_report = json.loads((DATA / "missing-fields-report.json").read_text(encoding="utf-8"))
    if missing_report.get("pendingProducts") != unique_status["보류"]:
        errors.append("missingFields report pending count mismatch")
    if missing_report.get("pendingWithStructuredMissingFields") != unique_status["보류"]:
        errors.append("not every pending product has structured missingFields")

    proof = json.loads((DATA / "strict-419-production-final-proof.json").read_text(encoding="utf-8"))
    if any(proof.get(key) != value for key, value in {
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": duplicate_count,
        "uniqueIncluded": unique_status["포함"],
        "uniquePending": unique_status["보류"],
        "uniqueExcluded": unique_status["제외"],
    }.items()):
        errors.append("production proof counts mismatch")

    report = {
        "status": "failed" if errors else "passed",
        "build": EXPECTED_BUILD,
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": duplicate_count,
        "uniqueStatusCounts": {key: unique_status[key] for key in ("포함", "보류", "제외")},
        "rawStatusCounts": {key: raw_status[key] for key in ("포함", "보류", "제외")},
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
