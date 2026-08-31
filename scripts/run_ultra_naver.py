#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver discovery and strict cross-source rechecks."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date
from urllib.parse import parse_qs, urlparse

import naver_pending_revalidation as pending_sale
import naver_product_discovery as naver
import naver_product_discovery_paced as paced
import run_ultra_parallel as pipeline

_original_revalidate = pipeline.safe_revalidate
_original_resolve = naver.resolve_product_url
_original_title_matches = naver.title_matches
_original_select_pending_batch = pipeline.select_pending_batch
naver.DIRECT_HINTS = tuple(dict.fromkeys(naver.DIRECT_HINTS + ("/catalog/",)))
GENERIC_BRAND_TOKENS = {
    "국내", "국내생산", "국산", "생산", "아기", "유아", "신생아", "공식",
    "공식몰", "인기", "추천", "특가", "할인", "무료배송", "kc", "인증",
}
ERROR_PRIORITY = {
    "ultra_strict_match": 0,
    "safety_korea": 1,
    "kc_number_on_sales_page": 2,
    "law_specific_evidence": 3,
    "age_basis": 4,
    "sale_inactive": 5,
}
NON_OFFICIAL_HOST_PARTS = (
    "shopping.naver.com", "smartstore.naver.com", "brand.naver.com",
    "coupang.com", "11st.co.kr", "gmarket.co.kr", "auction.co.kr",
    "ssg.com", "lotteon.com", "kurly.com", "danawa.com", "enuri.com",
    "fallcent.com", "wrapuppro.com", "blog.naver.com", "post.naver.com",
    "youtube.com", "instagram.com", "facebook.com", "tiktok.com",
    "shinailbo.co.kr", "slist.kr",
)


def resolve_product_url(url: str) -> str:
    parsed = urlparse(url)
    nv_mid = parse_qs(parsed.query).get("nv_mid", [""])[0]
    if nv_mid.isdigit():
        return f"https://search.shopping.naver.com/catalog/{nv_mid}"
    return _original_resolve(url)


def strict_title_matches(category: str, title: str) -> bool:
    if not _original_title_matches(category, title):
        return False
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", title.lower())
    return len(normalized) >= 8


def infer_brand(title: str) -> str:
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", title or ""):
        lowered = token.lower()
        if lowered not in GENERIC_BRAND_TOKENS and not lowered.isdigit():
            return token[:40]
    return "브랜드 확인 중"


def prioritize_url(product: dict, url: str, final_url: str = "") -> None:
    ordered = [value for value in (final_url, url) if value]
    current_sale = [str(value) for value in product.get("saleUrls", [])]
    current_official = [str(value) for value in product.get("officialUrls", [])]
    product["saleUrls"] = list(dict.fromkeys(
        ordered + [value for value in current_sale if value not in ordered]
    ))
    product["officialUrls"] = list(dict.fromkeys(
        ordered + [value for value in current_official if value not in ordered]
    ))


def evidence_score(working: dict, result: dict) -> tuple[int, int, int, int]:
    status = str(working.get("status", ""))
    resolved_rank = 0 if status in {"포함", "제외"} else 1
    missing = [
        str(value) for value in working.get("revalidationMissingFields", [])
        if str(value)
    ]
    errors = [str(value) for value in result.get("errors", []) if str(value)]
    error_rank = min(
        (ERROR_PRIORITY.get(value, len(ERROR_PRIORITY)) for value in errors),
        default=-1,
    )
    checked_rank = 0 if result.get("checked") else 1
    return resolved_rank, len(missing), checked_rank, error_rank


def identity_threshold(product: dict) -> int:
    tokens = pipeline.rr.product_tokens(product)
    return 2 if len(tokens) >= 2 else 1


def identity_matches(product: dict, text: str) -> bool:
    return pipeline.rr.token_overlap(product, text) >= identity_threshold(product)


def trusted_official_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host or any(value in host for value in NON_OFFICIAL_HOST_PARTS):
        return False
    if any(value in path for value in ("/news/", "/article", "/blog/", "/review/")):
        return False
    return True


def candidate_evidence_urls(product: dict) -> list[str]:
    values: list[str] = []
    for field in (
        "officialUrls", "saleUrls", "revalidationEvidenceUrls", "evidenceUrls",
    ):
        raw = product.get(field, [])
        if isinstance(raw, list):
            values.extend(str(value).strip() for value in raw if str(value).strip())
    return list(dict.fromkeys(
        value for value in values
        if value.startswith(("http://", "https://"))
        and "safetykorea.kr" not in value
        and pipeline.rr.direct_product_url(value)
    ))


def canonical_kc_numbers(product: dict) -> list[str]:
    values: list[str] = []
    if product.get("kcNumber"):
        values.append(str(product.get("kcNumber")))
    for cert in product.get("certifications", []):
        if not isinstance(cert, dict):
            continue
        for key in ("certNumber", "number", "kcNumber", "certNum", "certificateNumber"):
            if cert.get(key):
                values.append(str(cert.get(key)))
        raw_url = str(cert.get("url", ""))
        values.extend(parse_qs(urlparse(raw_url).query).get("certNum", []))
    safety_url = str(product.get("safetyKoreaSearchUrl", ""))
    values.extend(parse_qs(urlparse(safety_url).query).get("certNum", []))
    return list(dict.fromkeys(
        match.upper()
        for value in values
        for match in pipeline.rr.KC_PATTERN.findall(value)
    ))


def page_record(product: dict, url: str) -> dict | None:
    status, body, final_url = pipeline.probe(url)
    if status != 200 or not body:
        return None
    resolved = final_url or url
    if "safetykorea.kr" in resolved:
        return None
    text = pipeline.rr.plain(body)
    return {
        "url": resolved,
        "sourceUrl": url,
        "body": body,
        "text": text,
        "identity": identity_matches(product, text),
        "official": trusted_official_url(resolved),
        "searchDiscovered": False,
    }


def fetch_pages(product: dict) -> list[dict]:
    pages: list[dict] = []
    seen: set[str] = set()
    for url in candidate_evidence_urls(product)[:14]:
        page = page_record(product, url)
        if page is None or page["url"] in seen:
            continue
        seen.add(page["url"])
        pages.append(page)
    return pages


def discover_identity_pages(product: dict, existing: list[dict]) -> list[dict]:
    """Search for exact product pages that expose mandatory KC product labels.

    This is an identity bridge only. A retailer page can supply the KC number
    printed for that exact product but can never establish final manufacturer
    or country. Those facts still must come from Safety Korea.
    """
    seen = {str(page.get("url", "")) for page in existing}
    brand = str(product.get("brand", "")).strip()
    name = str(product.get("name", "")).strip()
    if not name:
        return []
    ignored = GENERIC_BRAND_TOKENS | {
        "네이버페이", "n배송", "선물샵", "예약", "가격비교", "최저가", "무료배송",
        "스토어", "공식스토어", "새", "창", "열림",
    }
    core_tokens: list[str] = []
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", name):
        lowered = token.lower()
        if lowered in ignored or lowered in core_tokens:
            continue
        core_tokens.append(lowered)
    core = " ".join(core_tokens[:8]) or name
    search_brand = brand if brand.lower() not in ignored else infer_brand(core)
    queries = [
        f"{search_brand} {core} KC 인증번호 제조국",
        f"{search_brand} {core} 제조사 사용연령 제품상세",
    ]
    found: list[dict] = []
    for query in queries:
        try:
            results = pipeline.rr.ddg_results(query)
        except Exception:
            continue
        for title, url in results[:8]:
            url = str(url).strip()
            if (
                not url.startswith(("http://", "https://"))
                or url in seen
                or "safetykorea.kr" in url
                or not pipeline.rr.direct_product_url(url)
            ):
                continue
            seen.add(url)
            page = page_record(product, url)
            if page is None or not page["identity"]:
                continue
            page["searchDiscovered"] = True
            page["searchTitle"] = str(title)
            found.append(page)
            if pipeline.rr.KC_PATTERN.search(page["text"]):
                return found
            if len(found) >= 6:
                return found
    return found


def kc_links_from_pages(pages: list[dict]) -> tuple[list[str], set[str]]:
    numbers: list[str] = []
    identity_linked: set[str] = set()
    for page in pages:
        if not page.get("identity"):
            continue
        raw = f"{page.get('searchTitle', '')} {page.get('text', '')}"
        for number in pipeline.rr.KC_PATTERN.findall(raw):
            normalized = number.upper()
            if normalized not in numbers:
                numbers.append(normalized)
            identity_linked.add(normalized)
    return numbers, identity_linked


def safety_record(product: dict, number: str, identity_linked: bool) -> dict | None:
    url = pipeline.rr.cert_detail_url(number)
    status, html, final_url = pipeline.rr.fetch(url)
    if status != 200 or not html:
        return None
    official = pipeline.rr.plain(html)
    if number.lower() not in official.lower():
        return None
    if not re.search(r"인증상태\s*[|:]?\s*적합", official):
        return None
    overlap = pipeline.rr.token_overlap(product, official)
    if not identity_linked and overlap < identity_threshold(product):
        return None

    authority = pipeline.rr.parse_label(
        official, "인증기관", ["인증구분", "인증번호"]
    )
    cert_type = pipeline.rr.parse_label(
        official, "인증구분", ["인증번호", "인증상태"]
    )
    cert_date = pipeline.rr.normalize_date(pipeline.rr.parse_label(
        official, "인증일자", ["인증변경일자", "인증변경사유"]
    ))
    item = pipeline.rr.parse_label(
        official, "품목명", ["모델명", "상세정보"]
    )
    model = pipeline.rr.parse_label(
        official, "모델명", ["상세정보", "제품분류코드", "파생모델"]
    )
    manufacturer = pipeline.rr.parse_label(
        official, "제조사", ["제조국", "수입업체"]
    )
    country = pipeline.rr.parse_label(
        official, "제조국", ["수입업체", "제품분류코드", "파생모델"]
    )
    importer = pipeline.rr.parse_label(
        official, "수입업체", ["제품분류코드", "파생모델"]
    )
    if not country:
        match = re.search(r"제조국\s*[|:]?\s*([가-힣A-Za-z ]{2,30})", official)
        country = match.group(1).strip() if match else ""
    if not all((authority, cert_type, cert_date, item, model, manufacturer)):
        return None
    if "/release/certDetail" not in final_url:
        return None
    return {
        "number": number,
        "url": final_url,
        "authority": authority,
        "certType": cert_type,
        "certDate": cert_date,
        "itemName": item,
        "model": model,
        "manufacturer": manufacturer,
        "country": country,
        "importer": importer,
        "overlap": overlap,
        "identityLinkedByProductPage": identity_linked,
    }


def cross_source_revalidate(product: dict) -> tuple[dict | None, dict]:
    if not pipeline.rr.kc_applies(product):
        return None, {"kcApplicable": False}

    original = deepcopy(product)
    pages = fetch_pages(original)
    known_numbers, linked_numbers = kc_links_from_pages(pages)
    canonical_numbers = canonical_kc_numbers(original)
    for number in canonical_numbers:
        if number not in known_numbers:
            known_numbers.append(number)

    if not linked_numbers:
        discovered = discover_identity_pages(original, pages)
        pages.extend(discovered)
        searched_numbers, searched_links = kc_links_from_pages(discovered)
        for number in searched_numbers:
            if number not in known_numbers:
                known_numbers.append(number)
        linked_numbers.update(searched_links)

    tried = [page["url"] for page in pages]
    sale_url = ""
    sale_basis = ""
    for page in pages:
        if not page["identity"]:
            continue
        active, basis = pipeline.rr.active_sales_page(page["text"])
        if active:
            sale_url = page["url"]
            sale_basis = basis
            break
    if not sale_url:
        title, fresh_url = pending_sale.exact_listing(original)
        if fresh_url:
            status, body, final_url = pipeline.probe(fresh_url)
            if status == 200 and body:
                sale_url = final_url or fresh_url
                sale_basis = f"네이버 정확 제품명 현재 판매 확인: {title}"
                tried.append(sale_url)

    missing_fields = {
        str(value) for value in original.get("revalidationMissingFields", [])
        if str(value)
    }
    age_text = ""
    age_url = ""
    if "officialAge" not in missing_fields:
        existing_age = str(original.get("ageRange", "")).strip()
        age_ok, existing_basis = pipeline.rr.age_basis(existing_age)
        if age_ok:
            age_text = existing_basis or existing_age
            age_url = next(
                (str(value) for value in original.get("officialUrls", [])
                 if trusted_official_url(str(value))),
                "",
            )
    if not age_text:
        for page in pages:
            if not page["official"] or not page["identity"]:
                continue
            age_ok, basis = pipeline.rr.age_basis(page["text"])
            if age_ok:
                age_text = basis
                age_url = page["url"]
                break
            if basis == "36개월 이상 전용":
                product.update({
                    "status": "제외",
                    "checkedAt": date.today().isoformat(),
                    "revalidationResolved": True,
                    "revalidationState": "공식 월령 기준 제외 완료",
                    "revalidationMissingFields": [],
                    "reason": (
                        f"공식 제품 페이지 {page['url']}에서 36개월 이상 전용으로 확인되어 "
                        "0~35개월 대상 기준을 충족하지 않아 제외했다."
                    ),
                })
                evidence = [page["url"]] + [
                    str(value) for value in product.get("evidenceUrls", [])
                ]
                product["evidenceUrls"] = list(dict.fromkeys(evidence))
                return ({
                    "id": product.get("id"),
                    "checked": True,
                    "changed": product != original,
                    "quality": pipeline.QUALITY,
                    "errors": [],
                    "evidencePagesTried": list(dict.fromkeys(tried)),
                    "crossSourceResolved": True,
                    "resolution": "excluded_age_36_plus",
                }, {
                    "saleFound": bool(sale_url),
                    "ageFound": True,
                    "kcNumbers": known_numbers,
                    "linkedKcNumbers": sorted(linked_numbers),
                })

    records: list[dict] = []
    for number in known_numbers[:8]:
        record = safety_record(original, number, number in linked_numbers)
        if record:
            records.append(record)

    diagnostics = {
        "saleFound": bool(sale_url),
        "saleUrl": sale_url,
        "ageFound": bool(age_text),
        "ageUrl": age_url,
        "kcNumbers": known_numbers,
        "linkedKcNumbers": sorted(linked_numbers),
        "safetyMatches": [record["number"] for record in records],
        "evidencePagesTried": list(dict.fromkeys(tried)),
    }

    for record in records:
        country = str(record.get("country", "")).strip()
        korea = bool(re.search(r"대한민국|한국", country))
        if country and not korea:
            product.update({
                "status": "제외",
                "checkedAt": date.today().isoformat(),
                "countryOfManufacture": country,
                "manufacturer": record["manufacturer"],
                "importer": record["importer"],
                "officialModel": record["model"],
                "kcNumber": record["number"],
                "kcApplicable": record["certType"],
                "kcType": record["certType"],
                "regulatoryRegime": record["certType"],
                "safetyKoreaSearchUrl": record["url"],
                "revalidationResolved": True,
                "revalidationState": "Safety Korea 동일 인증 해외 제조 확인",
                "revalidationMissingFields": [],
                "reason": (
                    f"정확 제품 페이지에서 연결된 KC {record['number']}를 Safety Korea에서 "
                    f"재조회했고 적합 인증의 제조국이 {country}로 확인되어 대한민국 제조 "
                    "기준에서 제외했다."
                ),
            })
            product["certifications"] = [{
                "certNumber": record["number"], "found": True, "status": "적합",
                "certDate": record["certDate"], "certType": record["certType"],
                "authority": record["authority"], "itemName": record["itemName"],
                "modelName": record["model"], "manufacturer": record["manufacturer"],
                "country": country, "importer": record["importer"], "url": record["url"],
            }]
            product["evidenceUrls"] = list(dict.fromkeys(
                [record["url"]] + tried + [str(value) for value in product.get("evidenceUrls", [])]
            ))
            return ({
                "id": product.get("id"), "checked": True,
                "changed": product != original, "quality": pipeline.QUALITY,
                "errors": [], "evidencePagesTried": diagnostics["evidencePagesTried"] + [record["url"]],
                "crossSourceResolved": True, "resolution": "excluded_foreign_manufacture",
            }, diagnostics)

        if not (korea and sale_url and age_text):
            continue

        official_urls = [sale_url]
        if age_url:
            official_urls.append(age_url)
        official_urls.extend(str(value) for value in original.get("officialUrls", []))
        sale_urls = [sale_url] + [str(value) for value in original.get("saleUrls", [])]
        evidence_urls = [sale_url, age_url, record["url"]] + [
            str(value) for value in original.get("evidenceUrls", [])
        ]
        regulatory = str(record["certType"]).strip()
        normalized_age = pipeline.rr.normalize_age_label(age_text)
        product.update({
            "status": "포함",
            "countryOfManufacture": "대한민국 🇰🇷",
            "saleStatus": f"현재 국내 판매 확인 · {sale_basis}",
            "ageRange": normalized_age,
            "ageEvidence": (
                f"공식 제품 페이지 {age_url}: {age_text}"
                if age_url else str(original.get("ageEvidence", "")) or age_text
            ),
            "kcNumber": record["number"],
            "kcApplicable": regulatory,
            "kcType": regulatory,
            "testInstitute": record["authority"],
            "manufacturer": record["manufacturer"],
            "importer": record["importer"],
            "officialModel": record["model"],
            "regulatoryRegime": regulatory,
            "safetyKoreaSearchUrl": record["url"],
            "certStatusSummary": "적합",
            "certDateSummary": record["certDate"],
            "certTypeSummary": regulatory,
            "certAuthoritySummary": record["authority"],
            "activeCertificateCount": 1,
            "checkedAt": date.today().isoformat(),
            "revalidationResolved": True,
            "revalidationState": "판매 제품표시·공식 월령·Safety Korea 교차검증 완료",
            "revalidationMissingFields": [],
            "quality": "정확 제품 KC표시·공식 월령·Safety Korea 적합·대한민국 제조 교차검증",
            "reason": (
                f"현재 국내 판매 제품 {sale_url}에서 구매 가능성을 확인했다. "
                f"공식 월령 근거 {age_text}를 확인했다. 정확 제품 페이지에 연결된 KC "
                f"{record['number']}를 Safety Korea {record['url']}에서 재조회해 적합 상태와 "
                f"제조사 {record['manufacturer']}와 제조국 대한민국을 교차 확인해 포함했다."
            ),
        })
        product["officialUrls"] = list(dict.fromkeys(value for value in official_urls if value))
        product["saleUrls"] = list(dict.fromkeys(value for value in sale_urls if value))
        product["evidenceUrls"] = list(dict.fromkeys(value for value in evidence_urls if value))
        product["revalidationEvidenceUrls"] = list(dict.fromkeys(
            value for value in (sale_url, age_url, record["url"]) if value
        ))
        product["certifications"] = [{
            "certNumber": record["number"], "found": True, "status": "적합",
            "certDate": record["certDate"], "certType": regulatory,
            "authority": record["authority"], "itemName": record["itemName"],
            "modelName": record["model"], "manufacturer": record["manufacturer"],
            "country": "대한민국 🇰🇷", "importer": record["importer"], "url": record["url"],
        }]
        return ({
            "id": product.get("id"), "checked": True,
            "changed": product != original, "quality": pipeline.QUALITY,
            "errors": [], "evidencePagesTried": diagnostics["evidencePagesTried"] + [record["url"]],
            "crossSourceResolved": True, "resolution": "included_cross_source",
        }, diagnostics)

    return None, diagnostics


def revalidate(product: dict) -> dict:
    original = deepcopy(product)
    cross, cross_diagnostics = cross_source_revalidate(product)
    if cross is not None:
        cross["crossSourceDiagnostic"] = cross_diagnostics
        return cross

    product.clear()
    product.update(original)
    urls = pipeline.candidate_sales_urls(original)
    best: tuple[tuple[int, int, int, int], dict, dict] | None = None
    tried: list[str] = []
    for url in urls[:10]:
        status, body, final_url = pipeline.probe(url)
        if status != 200 or not body:
            continue
        tried.append(final_url or url)
        working = deepcopy(original)
        prioritize_url(working, url, final_url)
        result = pipeline.rr.revalidate(working, pipeline.QUALITY)
        if not result.get("checked"):
            continue
        score = evidence_score(working, result)
        if best is None or score < best[0]:
            best = (score, working, result)
        if working.get("status") in {"포함", "제외"}:
            break
    if best is None:
        _, fresh_url = pending_sale.exact_listing(original)
        if fresh_url:
            status, body, final_url = pipeline.probe(fresh_url)
            if status == 200 and body:
                tried.append(final_url or fresh_url)
                working = deepcopy(original)
                prioritize_url(working, fresh_url, final_url)
                result = pipeline.rr.revalidate(working, pipeline.QUALITY)
                if result.get("checked"):
                    best = (evidence_score(working, result), working, result)
    if best is None:
        result = _original_revalidate(product)
        result["crossSourceDiagnostic"] = cross_diagnostics
        return result
    _, working, result = best
    product.clear()
    product.update(working)
    result["changed"] = product != original
    result["evidencePagesTried"] = list(dict.fromkeys(tried))
    result["crossSourceDiagnostic"] = cross_diagnostics
    return result


def discover(category: str, products: list[dict]) -> list[dict]:
    return paced.discover(category, products, pipeline.NEW_PER_CATEGORY)


def select_pending_batch(
    candidates: list[dict], previous_state: dict, campaign: dict | None
) -> tuple[list[dict], int, int, int, str]:
    if (
        isinstance(campaign, dict)
        and campaign.get("coverageComplete") is True
        and campaign.get("resolutionComplete") is not True
    ):
        target_ids = [str(value) for value in campaign.get("targetIdsSnapshot", [])]
        candidate_by_id = {
            str(item.get("id", "")): item
            for item in candidates if str(item.get("id", ""))
        }
        if target_ids and len(target_ids) == len(set(target_ids)):
            unresolved_ids = [value for value in target_ids if value in candidate_by_id]
            target_count = len(target_ids)
            cursor_start = int(previous_state.get("pendingCursorNext", 0) or 0) % target_count
            selected_ids: list[str] = []
            last_offset = -1
            for offset in range(target_count):
                product_id = target_ids[(cursor_start + offset) % target_count]
                if product_id in candidate_by_id:
                    selected_ids.append(product_id)
                    last_offset = offset
                    if len(selected_ids) >= pipeline.PENDING_LIMIT:
                        break
            selected = [candidate_by_id[value] for value in selected_ids]
            cursor_next = (
                (cursor_start + last_offset + 1) % target_count
                if last_offset >= 0 else cursor_start
            )
            return selected, len(unresolved_ids), cursor_start, cursor_next, "campaign-unresolved-retry"
    return _original_select_pending_batch(candidates, previous_state, campaign)


def normalize_schedule_metadata() -> None:
    if pipeline.rr.STATE.exists():
        state = json.loads(pipeline.rr.STATE.read_text(encoding="utf-8"))
        state["scheduleTarget"] = "5분 주기"
        pipeline.rr.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if pipeline.rr.AUDIT.exists():
        audit = json.loads(pipeline.rr.AUDIT.read_text(encoding="utf-8"))
        if isinstance(audit.get("state"), dict):
            audit["state"]["scheduleTarget"] = "5분 주기"
        pipeline.rr.AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def configure_pipeline() -> None:
    """Install the strict adapters without starting a canonical research run.

    Keeping configuration separate lets the staging-candidate worker reuse the
    exact same evidence gate.  Importing this module must never mutate data.
    """
    naver.resolve_product_url = resolve_product_url
    naver.title_matches = strict_title_matches
    naver.infer_brand = infer_brand
    pipeline.rr.ddg_results = pipeline.multi_search
    pipeline.safe_revalidate = revalidate
    pipeline.safe_discover = discover
    pipeline.select_pending_batch = select_pending_batch


def main() -> None:
    configure_pipeline()
    pipeline.main()
    normalize_schedule_metadata()


if __name__ == "__main__":
    main()
