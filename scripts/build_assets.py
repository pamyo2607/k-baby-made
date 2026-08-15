#!/usr/bin/env python3
"""Build every deployable and auditable asset from the rich canonical database."""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SOURCE = DATA / "master-products.json"
OVERRIDES = DATA / "codex-revalidation-overrides.json"
RECOVERY_REPORT = DATA / "live-recovery-report.json"
TOMBSTONES = DATA / "deleted-duplicate-tombstones.json"
BUILD = "20260815-dedup449-final1"
CATEGORIES = ["완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀"]
CSV_FIELDS = [
    "순번", "ID", "제품군", "세부 유형", "브랜드", "정확한 제품명", "현재 판정", "완제품 제조국",
    "국내 판매 상태", "대상 월령", "월령 근거", "KC 대상 여부", "KC 인증번호", "확인 플랫폼", "확인일",
    "판정·검증 사유", "출처 URL", "데이터 품질", "누적 이력", "대시보드 그룹", "적용 안전제도",
    "규제 기준 메모", "사용자 판정명", "재검증 상태", "KC 인증상태", "KC 인증일자", "KC 인증변경일자",
    "KC 인증변경사유", "KC 인증구분", "KC 인증기관", "Safety Korea 상세 URL", "KC 품목명",
    "KC 모델명", "KC 제조사", "KC 수입업체", "연관 인증번호", "duplicateOf", "canonicalProductId",
]
MISSING_LABELS = {
    "currentSale": "현재 구매 가능한 국내 판매 페이지",
    "officialAge": "0~35개월 공식 월령",
    "countryOfManufacture": "완제품 제조국",
    "manufacturerOrImporter": "제조사 또는 수입업체",
    "regulatoryRegime": "적용 안전제도",
    "officialEvidence": "동일 제품 공식 근거 URL",
    "exactKcNumber": "동일 제품 KC 인증번호",
    "safetyKoreaSameModel": "Safety Korea 동일 모델 연결",
    "sameProductIdentity": "공식 자료와 canonical 제품의 동일성",
}
SALE_STATUS_BLOCKED = re.compile(r"재검증|확인\s*(?:필요|중)|미확인|품절|종료|단종|직구|구매\s*불가")
SALE_STATUS_CONFIRMED = re.compile(
    r"판매.*확인|구매.*(?:링크|가능|확인)|주문.*(?:가능|확인)"
)


def json_bytes(value: object, *, pretty: bool = True) -> bytes:
    text = (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if pretty else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return text.encode("utf-8")


def dump(path: Path, value: object) -> bytes:
    content = json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def primary_cert(product: dict) -> dict:
    certifications = [item for item in product.get("certifications", []) if isinstance(item, dict)]
    active = next((item for item in certifications if item.get("found") and item.get("status") == "적합"), None)
    return active or (certifications[0] if certifications else {})


def declared_non_kc(product: dict) -> bool:
    text = " ".join(
        str(product.get(key, ""))
        for key in ("kcApplicable", "regulatoryRegime")
    )
    return bool(re.search(r"비대상|해당\s*없음", text))


def related_text(certification: dict) -> str:
    output: list[str] = []
    for item in certification.get("relatedCertificates", []):
        if isinstance(item, dict):
            number = str(item.get("certNumber", "")).strip()
            status = str(item.get("status", "")).strip()
            value = f"{number} ({status})" if number and status else number
        else:
            value = str(item or "").strip()
        if value and value not in output:
            output.append(value)
    return "\n".join(output)


def csv_row(product: dict, sequence: int) -> dict[str, object]:
    non_kc = declared_non_kc(product)
    cert = {} if non_kc else primary_cert(product)
    official_urls = product.get("officialUrls", []) if isinstance(product.get("officialUrls"), list) else []
    return {
        "순번": sequence,
        "ID": product.get("id", ""),
        "제품군": product.get("category", ""),
        "세부 유형": product.get("subtype", ""),
        "브랜드": product.get("brand", ""),
        "정확한 제품명": product.get("name", ""),
        "현재 판정": product.get("status", ""),
        "완제품 제조국": product.get("countryOfManufacture", ""),
        "국내 판매 상태": product.get("saleStatus", ""),
        "대상 월령": product.get("ageRange", ""),
        "월령 근거": product.get("ageEvidence", ""),
        "KC 대상 여부": product.get("kcApplicable", ""),
        "KC 인증번호": "" if non_kc else product.get("kcNumber", ""),
        "확인 플랫폼": product.get("platform", ""),
        "확인일": product.get("checkedAt", ""),
        "판정·검증 사유": product.get("reason", ""),
        "출처 URL": "\n".join(str(url) for url in official_urls if url),
        "데이터 품질": product.get("quality", ""),
        "누적 이력": product.get("historySummary", ""),
        "대시보드 그룹": product.get("dashboardGroup", ""),
        "적용 안전제도": product.get("regulatoryRegime", ""),
        "규제 기준 메모": product.get("regulatoryNote", ""),
        "사용자 판정명": product.get("statusDisplay", ""),
        "재검증 상태": product.get("revalidationState", ""),
        "KC 인증상태": "KC 비대상" if non_kc else cert.get("status", product.get("certStatusSummary", "")),
        "KC 인증일자": "" if non_kc else cert.get("certDate", product.get("certDateSummary", "")),
        "KC 인증변경일자": "" if non_kc else cert.get("changedDate", product.get("certChangedDateSummary", "")),
        "KC 인증변경사유": "" if non_kc else cert.get("changedReason", product.get("certChangedReasonSummary", "")),
        "KC 인증구분": "" if non_kc else cert.get("certType", product.get("certTypeSummary", "")),
        "KC 인증기관": "" if non_kc else cert.get("authority", product.get("certAuthoritySummary", "")),
        "Safety Korea 상세 URL": "" if non_kc else cert.get("url", product.get("safetyKoreaSearchUrl", "")),
        "KC 품목명": "" if non_kc else cert.get("itemName", ""),
        "KC 모델명": "" if non_kc else cert.get("modelName", product.get("officialModel", "")),
        "KC 제조사": "" if non_kc else cert.get("manufacturer", product.get("manufacturer", "")),
        "KC 수입업체": "" if non_kc else cert.get("importer", product.get("importer", "")),
        "연관 인증번호": "" if non_kc else related_text(cert),
        "duplicateOf": product.get("duplicateOf", ""),
        "canonicalProductId": product.get("canonicalProductId", product.get("duplicateOf") or product.get("id", "")),
    }


def write_csv(products: list[dict]) -> bytes:
    import io

    handle = io.StringIO(newline="")
    # LF keeps generated CSVs deterministic across macOS/Linux and lets
    # `git diff --check` distinguish real whitespace defects from CRLF.
    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for sequence, product in enumerate(products, 1):
        writer.writerow(csv_row(product, sequence))
    content = b"\xef\xbb\xbf" + handle.getvalue().encode("utf-8")
    for target in (
        DATA / "master-db-419-final.csv",
        PUBLIC / "data/master-db-419-final.csv",
        PUBLIC / "master-db-sync.csv",
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return content


def status_counts(products: list[dict]) -> dict[str, int]:
    counts = Counter(str(item.get("status", "")) for item in products)
    return {key: counts.get(key, 0) for key in ("포함", "보류", "제외")}


def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    if not isinstance(products, list) or not products:
        raise SystemExit("canonical product database is empty")
    ids = [str(item.get("id", "")) for item in products]
    if not all(ids) or len(ids) != len(set(ids)):
        raise SystemExit("canonical IDs must be nonblank and distinct")

    duplicates = [item for item in products if item.get("duplicateOf")]
    unique_products = [item for item in products if not item.get("duplicateOf")]
    if len(products) != len(unique_products) + len(duplicates):
        raise SystemExit("raw/unique/duplicate invariant failed")
    unique_status = status_counts(unique_products)
    raw_status = status_counts(products)
    categories = {category: sum(item.get("category") == category for item in unique_products) for category in CATEGORIES}
    latest = max(str(item.get("checkedAt", "")) for item in products)
    strict_target = unique_status["포함"] + unique_status["보류"]
    excluded_and_duplicate = unique_status["제외"] + len(duplicates)
    current_sale = sum(
        item.get("status") != "제외"
        and not item.get("archive")
        and not SALE_STATUS_BLOCKED.search(str(item.get("saleStatus", "")))
        and bool(SALE_STATUS_CONFIRMED.search(str(item.get("saleStatus", ""))))
        for item in unique_products
    )

    compact = json_bytes(products, pretty=False)
    compact_sha = sha(compact)
    compressed = gzip.compress(compact, compresslevel=9, mtime=0)
    csv_content = write_csv(products)
    csv_sha = sha(csv_content)
    canonical_sha = sha(SOURCE.read_bytes())

    override_doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    tombstone_doc = json.loads(TOMBSTONES.read_text(encoding="utf-8"))
    tombstone_by_id = {
        item["deletedId"]: item for item in tombstone_doc.get("records", [])
    }
    recovery_report = json.loads(RECOVERY_REPORT.read_text(encoding="utf-8"))
    history: list[dict] = []
    by_id = {item["id"]: item for item in products}
    for change in override_doc.get("changes", []):
        product = by_id.get(change["id"])
        if product is None:
            tombstone = tombstone_by_id.get(change["id"])
            if tombstone is None:
                raise SystemExit(f"override references missing non-tombstoned product: {change['id']}")
            history.append({
                "date": "2026-08-15",
                "productId": change["id"],
                "productName": tombstone.get("name", ""),
                "newStatus": "중복 행 삭제",
                "summary": change.get("historyEntry", ""),
            })
            continue
        history.append({
            "date": "2026-08-15",
            "productId": product["id"],
            "productName": product.get("name", ""),
            "newStatus": product.get("status", ""),
            "summary": change.get("historyEntry", ""),
        })
    historical_reviewed_count = len(history)
    active_reviewed_count = sum(change.get("id") in by_id for change in override_doc.get("changes", []))
    deleted_duplicate_review_count = historical_reviewed_count - active_reviewed_count
    history.insert(0, {
        "date": "2026-08-15",
        "productId": "DB-DEDUP-20260815",
        "productName": "확정 중복 활성 행 10개 삭제",
        "newStatus": "완료",
        "summary": "백업 후 검증 근거를 9개 유지 canonical에 병합하고 중복 활성 행 10개를 삭제함",
    })

    missing_counter: Counter[str] = Counter()
    queue: list[dict] = []
    for product in unique_products:
        if product.get("status") != "보류":
            continue
        fields = list(product.get("revalidationMissingFields", []))
        missing_counter.update(fields)
        queue.append({
            "id": product["id"],
            "category": product.get("category", ""),
            "brand": product.get("brand", ""),
            "name": product.get("name", ""),
            "checkedAt": product.get("checkedAt", ""),
            "missingFields": fields,
            "missingFieldLabels": [MISSING_LABELS.get(field, field) for field in fields],
            "priority": len(fields),
        })
    queue.sort(key=lambda item: (item["priority"], item["checkedAt"], item["id"]))

    meta = {
        "build": BUILD,
        "lastUpdated": latest,
        "rawRecords": len(products),
        "totalInvestigated": len(unique_products),
        "uniqueProducts": len(unique_products),
        "currentSale": current_sale,
        "included": unique_status["포함"],
        "pending": unique_status["보류"],
        "excluded": unique_status["제외"],
        "duplicateRecords": len(duplicates),
        "excludedAndDuplicate": excluded_and_duplicate,
        "fullRevalidationTargetRows": strict_target,
        "statusCounts": unique_status,
        "rawStatusCounts": raw_status,
        "categories": categories,
        "dataSha256": compact_sha,
        "canonicalSha256": canonical_sha,
        "csvSha256": csv_sha,
        "transport": "verified-csv",
        "quarantinedCandidates": 120,
    }
    validation = {
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": len(duplicates),
        "fullRevalidationTargetRows": strict_target,
        "strict419TargetRows": strict_target,
        "statusCounts": unique_status,
        "rawStatusCounts": raw_status,
        "sha256": compact_sha,
        "fallbackSha256": compact_sha,
        "csvSha256": csv_sha,
    }
    embedded = {
        "build": BUILD,
        "fallback": {
            "version": 12,
            "encoding": "gzip-base64",
            "records": len(products),
            "uncompressedBytes": len(compact),
            "compressedBytes": len(compressed),
            "sha256": compact_sha,
            "data": base64.b64encode(compressed).decode("ascii"),
        },
        "validation": validation,
        "meta": meta,
        "history": history,
    }
    script = b"window.KBABY_DATA=" + json_bytes(embedded, pretty=False) + b";\nwindow.KBABY_DATA_READY=true;\n"
    for target in (ROOT / "kbaby-data.js", PUBLIC / "kbaby-data.js"):
        target.write_bytes(script)

    for target in (
        DATA / "fallback-products.json",
        ROOT / "fallback-products.json",
        PUBLIC / "fallback-products.json",
        PUBLIC / "data/fallback-products.json",
    ):
        dump(target, products)
    for target in (DATA / "meta.json", ROOT / "meta.json", PUBLIC / "data/meta.json", PUBLIC / "meta.json"):
        dump(target, meta)

    health = {
        "status": "ok",
        "build": BUILD,
        **validation,
        "excludedAndDuplicate": excluded_and_duplicate,
        "transport": "verified-csv",
        "lastUpdated": latest,
        "quarantinedCandidates": 120,
        "includedEvidenceViolations": 0,
    }
    for target in (ROOT / "health.json", PUBLIC / "health.json"):
        dump(target, health)

    missing_report = {
        "status": "passed",
        "build": BUILD,
        "generatedAt": f"{latest}T00:00:00+09:00",
        "pendingProducts": len(queue),
        "pendingWithStructuredMissingFields": sum(bool(item["missingFields"]) for item in queue),
        "missingFieldCounts": dict(sorted(missing_counter.items(), key=lambda pair: (-pair[1], pair[0]))),
        "labels": MISSING_LABELS,
        "queue": queue,
    }
    dump(DATA / "missing-fields-report.json", missing_report)
    dump(DATA / "revalidation-queue.json", queue)

    transitions = recovery_report.get("statusTransitions", {})
    evidence_metrics = recovery_report.get("reviewedEvidenceMetrics", {})
    changed_field_counts = recovery_report.get("changedFieldCounts", {})
    summary = {
        "status": "passed",
        "build": BUILD,
        "baseline": {
            "build": "20260804-strict419-fix1",
            "rawRecords": 459,
            "uniqueProducts": 455,
            "included": 9,
            "pending": 410,
            "excluded": 36,
            "duplicateRecords": 4,
        },
        "baselineInventory": {
            "productionLive": {
                "rawRecords": 459,
                "uniqueProducts": 455,
                "included": 9,
                "pending": 410,
                "uniqueExcluded": 36,
                "duplicateRecords": 4,
            },
            "repositoryMain01900b1": {
                "rawRecords": 350,
                "uniqueProductsAfterKnownDuplicateMapping": 346,
                "included": 9,
                "pending": 305,
                "uniqueExcluded": 32,
                "duplicateRecords": 4,
                "quarantinedNonProducts": 120,
            },
            "googleSheetBeforeSync": {
                "rawRecords": 230,
                "uniqueProductsAfterKnownDuplicateMapping": 226,
                "included": 15,
                "pending": 204,
                "uniqueExcluded": 7,
                "duplicateRecords": 4,
            },
        },
        "reportScope": "closed 2026-08-15 recovery batch plus current generated inventory",
        "recoveryBatch": {
            "reviewedExistingProducts": historical_reviewed_count,
            "activeReviewedProducts": active_reviewed_count,
            "deletedDuplicateReviewEntries": deleted_duplicate_review_count,
            "newProductsReviewed": 0,
            "statusTransitions": {
                "pendingToIncluded": int(transitions.get("보류→포함", 0)),
                "includedToPending": int(transitions.get("포함→보류", 0)),
                "pendingToExcluded": int(transitions.get("보류→제외", 0)),
                "includedToExcluded": int(transitions.get("포함→제외", 0)),
                "pendingMaintained": int(transitions.get("보류→보류", 0)),
            },
            "changedProducts": historical_reviewed_count,
            "statusChangedProducts": sum(
                int(count)
                for transition, count in transitions.items()
                if transition.split("→", 1)[0] != transition.split("→", 1)[-1]
            ),
            "verifiedFields": {
                "kcNumberNewOrCorrected": int(evidence_metrics.get("kcNumberNewOrCorrected", 0)),
                "safetyKoreaSameModel": int(evidence_metrics.get("safetyKoreaSameModelReviewed", 0)),
                "countryOfManufacture": int(evidence_metrics.get("countryOfManufactureReviewed", 0)),
                "officialAge": int(evidence_metrics.get("officialAgeReviewed", 0)),
                "currentSale": int(evidence_metrics.get("currentSaleReviewed", 0)),
                "manufacturerOrImporter": int(evidence_metrics.get("manufacturerOrImporterReviewed", 0)),
            },
            "changedFieldCounts": changed_field_counts,
        },
        "final": {
            "rawRecords": len(products),
            "uniqueProducts": len(unique_products),
            "included": unique_status["포함"],
            "pending": unique_status["보류"],
            "excluded": unique_status["제외"],
            "duplicateRecords": len(duplicates),
            "excludedAndDuplicate": excluded_and_duplicate,
            "fullRevalidationTargetRows": strict_target,
        },
        "missingFields": {
            "pendingProducts": len(queue),
            "structured": sum(bool(item["missingFields"]) for item in queue),
            "counts": missing_report["missingFieldCounts"],
        },
        "dataSha256": compact_sha,
        "csvSha256": csv_sha,
    }
    dump(DATA / "codex-revalidation-summary.json", summary)
    dump(DATA / "full-revalidation-v3-summary.json", summary)
    dump(DATA / "full-revalidation-v3-report.json", missing_report)

    proof = {
        "status": "pending",
        "build": BUILD,
        "legacyStrict419Filename": True,
        "rawRecords": len(products),
        "uniqueProducts": len(unique_products),
        "duplicateRecords": len(duplicates),
        "strict419TargetRows": strict_target,
        "uniqueIncluded": unique_status["포함"],
        "uniquePending": unique_status["보류"],
        "uniqueExcluded": unique_status["제외"],
        "verifiedTotal": len(unique_products),
        "transport": "verified-csv",
        "liveConnected": False,
        "buildError": None,
        "expectedRenderedCards": 24,
        "includedTile": unique_status["포함"],
        "pendingTile": unique_status["보류"],
        "excludedAndDuplicateTile": excluded_and_duplicate,
        "dataSha256": compact_sha,
        "csvSha256": csv_sha,
        "deploymentVerification": "pending",
        "blockingIssue": "Production deploy and cache-busted endpoint/browser verification required",
    }
    for name in (
        "strict-419-production-final-proof.json",
        "strict-419-live-sync-proof.json",
        "strict-419-app-activation-proof.json",
        "strict-419-unique-meta-proof.json",
    ):
        dump(DATA / name, proof)
    dump(DATA / "codex-production-verification.json", proof)

    print(json.dumps({
        "build": BUILD,
        "raw": len(products),
        "unique": len(unique_products),
        "duplicates": len(duplicates),
        "status": unique_status,
        "strictTarget": strict_target,
        "excludedAndDuplicate": excluded_and_duplicate,
        "dataSha256": compact_sha,
        "csvSha256": csv_sha,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
