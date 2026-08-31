#!/usr/bin/env python3
"""Revalidate staged DISC candidates for the cumulative top-30 campaign.

Each run selects at most five candidates per category.  A candidate becomes
promotion-ready only after the same strict inclusion contract used by the
canonical validator passes.  Pending evidence and rejected decisions remain
auditable in staging; this script never writes directly to canonical data.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from promote_verified_candidates import (
    PromotionError,
    assert_no_product_duplicates,
)
from validate_data import included_errors


CATEGORIES = ["완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀"]
KC_CATEGORIES = {"완구", "구강·치발기"}
PLACEHOLDERS = {"", "확인 중", "미상", "해당 없음", "브랜드 확인 중"}
LOW_VALUE_SOURCE_PARTS = (
    "itemscout.io", "nstoreprice.co.kr", "fallcent.com", "salefinder.co.kr",
    "alltimeprice.com", "nid.naver.com", "search.shopping.naver.com",
    "brand.naver.com", "smartstore.naver.com", "shopping.naver.com",
)
HIGH_VALUE_RETAIL_HOST_PARTS = (
    "store.ohou.se", "ohou.se",
)
LOW_REACHABILITY_RETAIL_HOST_PARTS = (
    "11st.co.kr", "coupang.com", "ssg.com", "lotteon.com", "gmarket.co.kr",
    "auction.co.kr",
)
DEMAND_SIGNAL_TERMS = (
    "누적 판매", "판매 1위", "베스트", "인기", "랭킹", "국민", "리뷰",
)


class StagedResearchError(RuntimeError):
    pass


def read_json(path: Path, default: object | None = None) -> object:
    if not path.exists() and default is not None:
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedResearchError(f"cannot read {path}: {exc}") from exc


def document_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
                raise StagedResearchError(f"readback mismatch: {path}")
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()


def clean_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" |:：;,").strip()
    return value[:180]


def candidate_source_priority(item: dict) -> tuple[int, int, int, int, int, str]:
    """Rank likely exact retail evidence ahead of aggregators and dead bridges.

    This ranking never grants inclusion. It only decides which candidates are
    researched first within the same attempt count. The strict evidence gate
    below remains authoritative.
    """
    urls = list(dict.fromkeys(
        str(value).strip()
        for field in ("officialUrls", "saleUrls", "revalidationEvidenceUrls", "evidenceUrls")
        for value in (item.get(field, []) if isinstance(item.get(field, []), list) else [])
        if str(value).strip().startswith(("http://", "https://"))
    ))

    ranks: list[int] = []
    for url in urls:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if (
            any(value in host for value in LOW_VALUE_SOURCE_PARTS)
            or "block_state" in path
            or (host == "m.smartstore.naver.com" and path.startswith("/main/products/"))
        ):
            ranks.append(4)
        elif any(value in host for value in HIGH_VALUE_RETAIL_HOST_PARTS):
            ranks.append(0)
        elif any(value in host for value in LOW_REACHABILITY_RETAIL_HOST_PARTS):
            ranks.append(3)
        elif any(value in path for value in (
            "/products/", "/product/", "/goods/", "/item/", "/detail/", "/shopdetail",
        )):
            ranks.append(1)
        else:
            ranks.append(2)

    best_source_rank = min(ranks, default=5)
    known_sale_and_age = int(not (
        "현재 구매 가능" in str(item.get("saleStatus", ""))
        and bool(re.search(r"(?:신생아|\d+\s*개월)", str(item.get("ageRange", ""))))
    ))
    text = f"{item.get('name', '')} {item.get('reason', '')} {item.get('rankEvidence', '')}"
    demand_signal_rank = int(not any(term in text for term in DEMAND_SIGNAL_TERMS))
    discovery_rank = int(item.get("discoveryResultRank", 10**6) or 10**6)
    return (
        best_source_rank,
        known_sale_and_age,
        demand_signal_rank,
        discovery_rank,
        -len(urls),
        str(item.get("id", "")),
    )


def candidate_selection_priority(
    item: dict, attempt_count: int, original_order: int
) -> tuple:
    """Balance reachable source quality with bounded retry rotation."""
    source = candidate_source_priority(item)
    attempt_count = max(int(attempt_count or 0), 0)
    effective_source_rank = source[0] + min(attempt_count, 2)
    return (
        effective_source_rank,
        attempt_count,
        *source[1:-1],
        original_order,
        source[-1],
    )


def labeled_value(text: str, labels: tuple[str, ...], stops: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(value) for value in labels)
    stop_pattern = "|".join(re.escape(value) for value in stops)
    pattern = re.compile(
        rf"(?:{label_pattern})\s*[:：]?\s*(.{{1,180}}?)(?=\s*(?:{stop_pattern})\s*[:：]?|$)",
        re.I,
    )
    for match in pattern.finditer(text):
        value = clean_value(match.group(1))
        if value and value not in PLACEHOLDERS:
            return value
    return ""


def country_value(text: str) -> str:
    match = re.search(
        r"(?:제조국|원산지)\s*[:：]?\s*(대한민국|한국|국산|중국|베트남|인도네시아|태국|일본|독일|미국|프랑스|이탈리아|대만)",
        text,
        re.I,
    )
    if not match:
        return ""
    value = match.group(1)
    return "대한민국" if value in {"대한민국", "한국", "국산"} else value


def manufacturer_value(text: str) -> str:
    return labeled_value(
        text,
        ("제조자명", "제조업소명", "제조자", "제조사", "생산자"),
        (
            "제조국", "원산지", "수입자", "수입업소명", "판매자", "판매원",
            "품명", "모델명", "재질", "소재", "사용연령", "권장연령",
        ),
    )


def model_value(text: str) -> str:
    return labeled_value(
        text,
        ("품명 및 모델명", "제품명 및 모델명", "모델명", "품명", "제품명"),
        (
            "제조자", "제조사", "제조국", "원산지", "재질", "소재", "크기",
            "사용연령", "권장연령", "KC", "품질보증", "취급방법",
        ),
    )


def regulatory_regime(category: str, text: str) -> tuple[str, str, str]:
    if re.search(r"식품위생법|식품용\s*기구|기구\s*및\s*용기.?포장", text, re.I):
        return (
            "식품위생법 · 식품용 기구 및 용기·포장",
            "어린이제품 KC 비대상 · 식품 접촉 제품",
            "not-applicable",
        )
    if re.search(r"위생용품관리법|위생용품", text, re.I):
        return (
            "위생용품관리법",
            "어린이제품 KC 비대상 · 위생용품",
            "not-applicable",
        )
    if re.search(r"화장품법|화장품책임판매업", text, re.I):
        return (
            "화장품법",
            "어린이제품 KC 비대상 · 화장품",
            "not-applicable",
        )
    if re.search(r"공급자적합성|안전기준준수|어린이제품안전특별법", text, re.I):
        return (
            "어린이제품안전특별법 · 공급자적합성 또는 안전기준준수",
            "어린이제품 공급자적합성 또는 안전기준준수 대상",
            "supplier-conformity",
        )
    # Category alone is not enough to assign a law.  The mapping below only
    # narrows the missing-field message and never grants inclusion.
    if category in {"수유용품", "이유식·식기"}:
        return "", "제품별 식품 접촉 법령 확인 필요", ""
    if category == "위생·기저귀":
        return "", "제품별 위생용품·화장품 법령 확인 필요", ""
    return "", "제품별 어린이제품·식품 접촉 법령 확인 필요", ""


def non_kc_revalidate(product: dict) -> dict:
    """Resolve a non-KC candidate only from exact product disclosure text."""
    import run_ultra_naver as strict
    import run_ultra_parallel as pipeline

    original = deepcopy(product)
    pages = strict.fetch_pages(product)
    pages.extend(strict.discover_identity_pages(product, pages))
    identity_pages = [page for page in pages if page.get("identity")]
    tried = [str(page.get("url", "")) for page in pages if page.get("url")]
    if not identity_pages:
        return {
            "id": product.get("id"), "checked": False, "changed": False,
            "quality": pipeline.QUALITY, "errors": ["exact_product_page"],
            "evidencePagesTried": tried,
        }

    sale_url = ""
    sale_basis = ""
    for page in identity_pages:
        active, basis = pipeline.rr.active_sales_page(str(page.get("text", "")))
        if active:
            sale_url = str(page.get("url", ""))
            sale_basis = basis
            break

    age_text = ""
    age_url = ""
    country = ""
    country_url = ""
    manufacturer = ""
    manufacturer_url = ""
    model = ""
    model_url = ""
    regime = ""
    kc_applicable = ""
    evidence_level = ""
    regime_url = ""
    foreign_country = ""
    foreign_url = ""
    age_excluded_url = ""
    for page in identity_pages:
        text = str(page.get("text", ""))
        url = str(page.get("url", ""))
        age_ok, basis = pipeline.rr.age_basis(text)
        if age_ok and not age_text:
            age_text, age_url = basis, url
        elif basis == "36개월 이상 전용" and not age_excluded_url:
            age_excluded_url = url
        parsed_country = country_value(text)
        if parsed_country:
            if parsed_country == "대한민국" and not country:
                country, country_url = parsed_country, url
            elif parsed_country != "대한민국" and not foreign_country:
                foreign_country, foreign_url = parsed_country, url
        parsed_manufacturer = manufacturer_value(text)
        if parsed_manufacturer and not manufacturer:
            manufacturer, manufacturer_url = parsed_manufacturer, url
        parsed_model = model_value(text)
        if parsed_model and not model:
            model, model_url = parsed_model, url
        parsed_regime, parsed_kc, parsed_level = regulatory_regime(
            str(product.get("category", "")), text
        )
        if parsed_regime and not regime:
            regime, kc_applicable, evidence_level, regime_url = (
                parsed_regime, parsed_kc, parsed_level, url
            )

    today = date.today().isoformat()
    if age_excluded_url:
        product.update({
            "status": "제외",
            "checkedAt": today,
            "revalidationResolved": True,
            "revalidationState": "공식 월령 기준 제외 완료",
            "revalidationMissingFields": [],
            "reason": (
                f"동일 제품 상세 {age_excluded_url}에서 36개월 이상 전용으로 확인되어 "
                "0~35개월 대상 기준을 충족하지 않아 제외했다."
            ),
        })
        product["evidenceUrls"] = list(dict.fromkeys(
            [age_excluded_url] + [str(value) for value in product.get("evidenceUrls", [])]
        ))
        return {
            "id": product.get("id"), "checked": True, "changed": product != original,
            "quality": pipeline.QUALITY, "errors": [], "resolution": "excluded_age_36_plus",
            "evidencePagesTried": tried,
        }

    if foreign_country and foreign_url:
        product.update({
            "status": "제외",
            "countryOfManufacture": foreign_country,
            "checkedAt": today,
            "revalidationResolved": True,
            "revalidationState": "동일 제품 고시 해외 제조 확인",
            "revalidationMissingFields": [],
            "reason": (
                f"동일 제품 정보고시 {foreign_url}에서 완제품 제조국이 {foreign_country}로 "
                "확인되어 대한민국 제조 기준에서 제외했다."
            ),
        })
        product["evidenceUrls"] = list(dict.fromkeys(
            [foreign_url] + [str(value) for value in product.get("evidenceUrls", [])]
        ))
        return {
            "id": product.get("id"), "checked": True, "changed": product != original,
            "quality": pipeline.QUALITY, "errors": [], "resolution": "excluded_foreign_manufacture",
            "evidencePagesTried": tried,
        }

    missing: list[str] = []
    if not sale_url:
        missing.append("currentSale")
    if not age_text:
        missing.append("officialAge")
    if country != "대한민국":
        missing.append("countryOfManufacture")
    if not manufacturer:
        missing.append("manufacturerOrImporter")
    if not model:
        missing.append("sameProductIdentity")
    if not regime:
        missing.append("regulatoryRegime")
    if missing:
        product.update({
            "status": "보류",
            "checkedAt": today,
            "revalidationResolved": False,
            "revalidationState": "비KC 동일 제품 공식 고시 추가 확인 필요",
            "revalidationMissingFields": missing,
            "reason": (
                f"동일 제품 상세 {len(identity_pages)}개를 확인했으나 "
                f"{', '.join(missing)} 근거가 남아 보류한다."
            ),
        })
        product["revalidationEvidenceUrls"] = list(dict.fromkeys(tried))
        return {
            "id": product.get("id"), "checked": True, "changed": product != original,
            "quality": pipeline.QUALITY, "errors": missing,
            "evidencePagesTried": tried,
        }

    evidence_urls = list(dict.fromkeys(
        value for value in (
            sale_url, age_url, country_url, manufacturer_url, model_url, regime_url
        ) if value
    ))
    product.update({
        "status": "포함",
        "countryOfManufacture": "대한민국 🇰🇷",
        "saleStatus": f"현재 국내 판매 확인 · {sale_basis}",
        "ageRange": pipeline.rr.normalize_age_label(age_text),
        "ageEvidence": f"동일 제품 상세 {age_url}: {age_text}",
        "officialModel": model,
        "manufacturer": manufacturer,
        "regulatoryRegime": regime,
        "regulatoryNote": f"동일 제품 정보고시 {regime_url}에서 적용 법령 표기 확인",
        "kcApplicable": kc_applicable,
        "kcNumber": "",
        "kcType": "",
        "certifications": [],
        "certStatusSummary": "KC 비대상" if evidence_level == "not-applicable" else "공급자 적합성 확인",
        "certificationEvidenceLevel": evidence_level,
        "checkedAt": today,
        "revalidationResolved": True,
        "revalidationState": "비KC 동일 제품 공식 고시 교차검증 완료",
        "revalidationMissingFields": [],
        "quality": "현재 판매·공식 월령·제조국·제조사·모델·법령 교차검증",
        "reason": (
            f"동일 제품 상세 {sale_url}에서 현재 국내 판매를 확인했다. "
            f"{age_url}에서 {age_text}를 확인하고 {country_url}에서 대한민국 제조를 확인했다. "
            f"제조사 {manufacturer}와 공식 모델 {model} 및 {regime} 적용 근거를 대조했다."
        ),
    })
    product["officialUrls"] = list(dict.fromkeys(
        evidence_urls + [str(value) for value in product.get("officialUrls", [])]
    ))
    product["saleUrls"] = list(dict.fromkeys(
        [sale_url] + [str(value) for value in product.get("saleUrls", [])]
    ))
    product["revalidationEvidenceUrls"] = evidence_urls
    return {
        "id": product.get("id"), "checked": True, "changed": product != original,
        "quality": pipeline.QUALITY, "errors": [], "resolution": "included_law_specific",
        "evidencePagesTried": tried,
    }


def strict_validator(candidate: dict) -> tuple[dict, dict]:
    import run_ultra_naver as strict

    strict.configure_pipeline()
    working = deepcopy(candidate)
    if str(working.get("category", "")) in KC_CATEGORIES:
        result = strict.revalidate(working)
    else:
        result = non_kc_revalidate(working)
    return working, result


def process(
    root: Path,
    validator: Callable[[dict], tuple[dict, dict]] = strict_validator,
    *,
    now: datetime | None = None,
    workers: int = 10,
) -> dict:
    data = root / "data"
    canonical_path = data / "master-products.json"
    staging_path = data / "discovered-candidate-staging.json"
    checkpoint_path = data / "campaign-new50.json"
    tombstones_path = data / "deleted-duplicate-tombstones.json"
    state_path = data / "new-top30-research-state.json"
    canonical = read_json(canonical_path)
    staging = read_json(staging_path)
    checkpoint = read_json(checkpoint_path)
    tombstone_doc = read_json(tombstones_path, {"records": []})
    previous_state = read_json(state_path, {})
    if not isinstance(canonical, list) or not isinstance(staging, list):
        raise StagedResearchError("canonical and staging must be arrays")
    if not isinstance(checkpoint, dict) or not isinstance(previous_state, dict):
        raise StagedResearchError("campaign checkpoint/state must be objects")
    campaign_id = str(checkpoint.get("campaignId", ""))
    targets = checkpoint.get("categoryTargets")
    remaining = checkpoint.get("categoryRemaining")
    if not campaign_id or not isinstance(targets, dict) or not isinstance(remaining, dict):
        raise StagedResearchError("30-per-category campaign is not initialized")
    if set(targets) != set(CATEGORIES) or set(remaining) != set(CATEGORIES):
        raise StagedResearchError("category target keys are invalid")
    per_category = int(checkpoint.get("perCategoryWaveLimit", 5) or 5)
    if not 1 <= per_category <= 5:
        raise StagedResearchError("per-category wave limit must be 1..5")
    if workers <= 0:
        raise StagedResearchError("workers must be positive")

    if previous_state.get("campaignId") != campaign_id:
        previous_state = {}
    attempts = {
        str(key): int(value)
        for key, value in previous_state.get("attemptsById", {}).items()
        if str(key)
    }
    order = {str(item.get("id", "")): index for index, item in enumerate(staging)}
    selected: list[dict] = []
    selected_by_category: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for category in CATEGORIES:
        if int(remaining.get(category, 0) or 0) <= 0:
            continue
        eligible = [
            item for item in staging
            if str(item.get("category", "")) == category
            and str(item.get("id", "")).startswith("DISC-")
            and str(item.get("status", "")) in {"보류", "포함"}
            and not (
                isinstance(item.get("promotionGate"), dict)
                and item["promotionGate"].get("promotionReady") is True
            )
        ]
        eligible.sort(key=lambda item: candidate_selection_priority(
            item,
            attempts.get(str(item.get("id", "")), 0),
            order.get(str(item.get("id", "")), 10**9),
        ))
        batch = eligible[:min(per_category, int(remaining.get(category, 0) or 0))]
        selected.extend(batch)
        selected_by_category[category] = [str(item.get("id", "")) for item in batch]

    results: list[tuple[dict, dict]] = []
    if selected:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(selected)), thread_name_prefix="staged-revalidate"
        ) as executor:
            results = list(executor.map(lambda item: validator(deepcopy(item)), selected))

    staging_by_id = {str(item.get("id", "")): item for item in staging}
    result_rows: list[dict] = []
    ready_candidates: list[dict] = []
    for original, (working, audit) in zip(selected, results):
        candidate_id = str(original.get("id", ""))
        if str(working.get("id", "")) != candidate_id:
            raise StagedResearchError(f"validator changed candidate ID: {candidate_id}")
        attempts[candidate_id] = attempts.get(candidate_id, 0) + 1
        staging_by_id[candidate_id] = working
        failures = included_errors(working) if working.get("status") == "포함" else []
        ready = working.get("status") == "포함" and not failures
        result_rows.append({
            "candidateId": candidate_id,
            "category": working.get("category"),
            "decision": working.get("status"),
            "checked": bool(audit.get("checked")),
            "changed": bool(audit.get("changed")),
            "errors": [str(value) for value in audit.get("errors", [])],
            "includedContractErrors": failures,
            "attemptCount": attempts[candidate_id],
            "evidencePagesTried": audit.get("evidencePagesTried", []),
            "promotionReadyBeforeDedupe": ready,
        })
        if ready:
            ready_candidates.append(working)

    refreshed_staging = [staging_by_id[str(item.get("id", ""))] for item in staging]
    tombstones = (
        tombstone_doc.get("records", []) if isinstance(tombstone_doc, dict) else []
    )
    promotion_ready: list[dict] = []
    for candidate in ready_candidates:
        candidate_id = str(candidate.get("id", ""))
        other_staging = [
            item for item in refreshed_staging
            if str(item.get("id", "")) != candidate_id
        ]
        try:
            assert_no_product_duplicates([candidate], canonical, other_staging, tombstones)
        except PromotionError as exc:
            for row in result_rows:
                if row["candidateId"] == candidate_id:
                    row["dedupeDecision"] = "blocked"
                    row["dedupeReason"] = str(exc)
                    break
            candidate.pop("promotionGate", None)
            candidate["status"] = "보류"
            candidate["revalidationResolved"] = False
            candidate["revalidationState"] = "중복 가능성 검토 필요"
            continue
        promotion_ready.append(candidate)
        for row in result_rows:
            if row["candidateId"] == candidate_id:
                row["dedupeDecision"] = "new"
                break

    run_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    run_iso = run_at.isoformat()
    wave_index = max(
        int(checkpoint.get("waveIndex", 0) or 0),
        int(previous_state.get("waveIndex", 0) or 0),
    ) + 1
    slug = run_at.strftime("%Y%m%dT%H%M%SZ")
    audit_relative = Path("data/new-product-audits") / f"{slug}-wave-{wave_index:03d}.json"
    audit_path = root / audit_relative
    ready_ids = [str(item.get("id", "")) for item in promotion_ready]
    audit_document = {
        "schemaVersion": 1,
        "campaignId": campaign_id,
        "waveIndex": wave_index,
        "runAt": run_iso,
        "sourceCommit": os.environ.get("GITHUB_SHA", ""),
        "workflowRunId": os.environ.get("GITHUB_RUN_ID", ""),
        "selectedCandidateIdsByCategory": selected_by_category,
        "promotionReadyCandidateIds": ready_ids,
        "products": result_rows,
    }
    audit_content = document_bytes(audit_document)
    audit_sha = sha256(audit_content)
    for candidate in promotion_ready:
        candidate["promotionGate"] = {
            "schemaVersion": 1,
            "promotionReady": True,
            "evidenceComplete": True,
            "stageDecision": "included",
            "dedupeDecision": "new",
            "campaignId": campaign_id,
            "waveIndex": wave_index,
            "auditRef": audit_relative.as_posix(),
            "auditSha256": audit_sha,
        }

    state = {
        "schemaVersion": 1,
        "campaignId": campaign_id,
        "lastRun": run_iso,
        "waveIndex": wave_index,
        "auditRef": audit_relative.as_posix(),
        "auditSha256": audit_sha,
        "selectedCandidateIdsByCategory": selected_by_category,
        "selectedCount": len(selected),
        "promotionReadyCandidateIds": ready_ids,
        "promotionReadyCount": len(ready_ids),
        "attemptsById": dict(sorted(attempts.items())),
        "results": result_rows,
        "categoryTargets": targets,
        "categoryRemainingBeforeRun": remaining,
        "stagingCount": len(refreshed_staging),
    }
    atomic_replace({
        staging_path: document_bytes(refreshed_staging),
        audit_path: audit_content,
        state_path: document_bytes(state),
    })
    return {
        "campaignId": campaign_id,
        "waveIndex": wave_index,
        "selectedCount": len(selected),
        "selectedCandidateIdsByCategory": selected_by_category,
        "promotionReadyCount": len(ready_ids),
        "promotionReadyCandidateIds": ready_ids,
        "auditRef": audit_relative.as_posix(),
        "auditSha256": audit_sha,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    try:
        result = process(args.root.resolve(), workers=args.workers)
    except StagedResearchError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
