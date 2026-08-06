#!/usr/bin/env python3
"""Ultra-strict pending-product revalidation and conservative candidate discovery.

Included status is allowed only when:
- an active individual Korean sales page is reachable
- the sales page exposes the exact KC number
- an age basis applicable to 0–35 months is present
- Safety Korea confirms the same number, fit status, Korean manufacture
- official model/manufacturer/certification fields are present
- product identity tokens match
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
STATE = ROOT / "data/continuous-research-state.json"
AUDIT = ROOT / "data/research-last-run.json"
CATEGORIES = ["완구", "구강치발기", "턱받이", "수유용품", "이유식용품", "위생용품"]
KC_PATTERN = re.compile(r"\b[A-Z]{1,3}\d[A-Z0-9-]{5,}[A-Z0-9]\b", re.I)
BLOCKED_PATHS = ["/search", "/category", "/categories", "/guide", "/board", "/event", "/brand"]
DIRECT_HINTS = ["/product", "/products", "/item", "/goods", "/shopdetail", "/view", "/detail"]
PURCHASE_MARKERS = ["구매하기", "장바구니", "바로구매", "판매가", "할인가", "주문하기", "구매 혜택"]
STOP_MARKERS = ["판매종료", "현재 판매하지", "판매 중지", "단종", "품절 상품입니다"]
TEMP_STOP_MARKERS = ["일시품절", "재입고 알림", "품절"]
GENERIC_TOKENS = {
    "세트", "아기", "유아", "실리콘", "제품", "컬러", "개입", "국내", "공식",
    "대한민국", "한국", "신생아", "완구", "용품",
}
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; KBabyMadeEvidenceBot/2.0; +https://github.com/pamyo2607/k-baby-made)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
})
TIMEOUT = 22


def fetch(url: str) -> tuple[int, str, str]:
    for pause in [0, 5, 15, 45, 120]:
        if pause:
            time.sleep(pause)
        try:
            response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding or response.encoding
                return 200, response.text, response.url
            if response.status_code not in {403, 408, 429, 500, 502, 503, 504}:
                return response.status_code, "", response.url
        except requests.RequestException:
            continue
    return 0, "", url


def plain(source: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(source, "html.parser").get_text(" ", strip=True))


def direct_product_url(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(part in path for part in BLOCKED_PATHS):
        return False
    return any(part in path for part in DIRECT_HINTS) or bool(parsed.query)


def first_sales_url(product: dict) -> str:
    for url in product.get("evidenceUrls", []):
        if "safetykorea.kr" not in url and direct_product_url(url):
            return url
    return ""


def cert_detail_url(number: str) -> str:
    return "https://www.safetykorea.kr/release/certDetail?certNum=" + quote(number)


def product_tokens(product: dict) -> set[str]:
    raw = f"{product.get('brand', '')} {product.get('name', '')}"
    return {
        token.lower()
        for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", raw)
        if token not in GENERIC_TOKENS
    }


def token_overlap(product: dict, official_text: str) -> int:
    lower = official_text.lower()
    return sum(1 for token in product_tokens(product) if token in lower)


def parse_label(text: str, label: str, next_labels: list[str]) -> str:
    tail = "|".join(re.escape(item) for item in next_labels)
    match = re.search(
        re.escape(label) + r"\s*[|:]?\s*(.+?)(?=\s*(?:" + tail + r")\s*[|:]?|$)",
        text,
    )
    return match.group(1).strip() if match else ""


def active_sales_page(text: str) -> tuple[bool, str]:
    if any(marker in text for marker in STOP_MARKERS):
        return False, "판매 종료·단종"
    if any(marker in text for marker in TEMP_STOP_MARKERS):
        return False, "현재 구매 불가"
    if not any(marker in text for marker in PURCHASE_MARKERS):
        return False, "구매 기능 확인 불가"
    return True, "현재 구매 가능"


def age_basis(text: str) -> tuple[bool, str]:
    compact = re.sub(r"\s+", "", text)

    # 명확한 최소 연령이 36개월 또는 3세 이상이면 제외한다.
    if re.search(r"(?:만)?3세이상|36개월이상", compact):
        return False, "36개월 이상 전용"

    patterns = [
        r"(신생아|출생직후|출생부터|0개월부터)",
        r"(\d{1,2})개월이상",
        r"(\d{1,2})\s*[~\-–]\s*(\d{1,2})개월",
        r"사용연령[:：]?(?:만)?(\d)세미만",
        r"(?:만)?3세미만",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        nums = [int(value) for value in match.groups() if value and value.isdigit()]
        if not nums or min(nums) <= 35:
            return True, match.group(0)
    return False, "0~35개월 공식 연령 근거 없음"


def normalize_date(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return value.strip()


def normalize_age_label(value: str) -> str:
    """Keep exact evidence in ageBasis while satisfying the existing public validator."""
    compact = re.sub(r"\s+", "", value)
    if re.search(r"\d+개월", compact):
        return value
    if any(marker in compact for marker in ["신생아", "출생직후", "출생부터", "0개월부터"]):
        return "0개월 이상"
    if "3세미만" in compact:
        return "0~35개월"
    return "0~35개월"


def revalidate(product: dict, quality: str) -> dict:
    result = {
        "id": product.get("id"),
        "checked": False,
        "changed": False,
        "quality": quality,
        "errors": [],
    }
    sale_url = first_sales_url(product)
    if not sale_url:
        product["researchStatus"] = "개별 판매 상세 URL 확인 필요"
        result["errors"].append("sales_url")
        return result

    sale_status, sale_html, final_sale_url = fetch(sale_url)
    if sale_status != 200:
        product["researchStatus"] = f"판매 페이지 재확인 필요 HTTP {sale_status or 'ERR'}"
        result["errors"].append(f"sale_{sale_status}")
        return result

    sale_text = plain(sale_html)
    sale_ok, sale_basis = active_sales_page(sale_text)
    if not sale_ok:
        product["status"] = "보류" if sale_basis == "현재 구매 불가" else "제외"
        product["reason"] = (
            f"{date.today().isoformat()} 개별 판매 페이지에서 {sale_basis} 상태를 확인했다. "
            f"{final_sale_url}"
        )
        product["checkedAt"] = date.today().isoformat()
        product["researchStatus"] = sale_basis
        result.update(checked=True, changed=True)
        result["errors"].append("sale_inactive")
        return result

    age_ok, age_text = age_basis(sale_text)
    if not age_ok:
        product["status"] = "보류"
        product["age"] = "확인 중"
        product["ageBasis"] = age_text
        product["checkedAt"] = date.today().isoformat()
        product["researchStatus"] = "0~35개월 공식 연령 근거 추가 확인 필요"
        product["reason"] = (
            f"{date.today().isoformat()} 판매 페이지의 구매 가능 상태는 확인했으나 "
            f"0~35개월 사용 근거를 확보하지 못해 보류한다. {final_sale_url}"
        )
        result["errors"].append("age_basis")
        return result

    numbers: list[str] = []
    for found in KC_PATTERN.findall(sale_text):
        upper = found.upper()
        if upper not in numbers:
            numbers.append(upper)

    existing = str(product.get("kcNumber", "")).strip().upper()
    if existing and existing in numbers:
        numbers.remove(existing)
        numbers.insert(0, existing)

    # 최극상 모드에서는 판매 페이지에 직접 표시된 번호만 인정한다.
    if not numbers:
        product["status"] = "보류"
        product["checkedAt"] = date.today().isoformat()
        product["researchStatus"] = "판매 페이지 동일 제품 KC 번호 확인 필요"
        product["reason"] = (
            f"{date.today().isoformat()} 구매 가능한 개별 판매 페이지와 연령 근거는 확인했으나 "
            f"판매 페이지에서 동일 제품 KC 번호를 확인하지 못해 보류한다. {final_sale_url}"
        )
        result["errors"].append("kc_number_on_sales_page")
        return result

    for number in numbers[:6]:
        safety_url = cert_detail_url(number)
        safety_status, safety_html, final_safety_url = fetch(safety_url)
        if safety_status != 200:
            continue

        official = plain(safety_html)
        exact_number = number.lower() in official.lower()
        fit = bool(re.search(r"인증상태\s*[|:]?\s*적합", official))
        korea = bool(re.search(r"제조국\s*[|:]?\s*(한국|대한민국)", official))
        overlap = token_overlap(product, official)

        authority = parse_label(official, "인증기관", ["인증구분", "인증번호"])
        cert_type = parse_label(official, "인증구분", ["인증번호", "인증상태"])
        cert_date = normalize_date(parse_label(
            official, "인증일자", ["인증변경일자", "인증변경사유"]
        ))
        item = parse_label(official, "품목명", ["모델명", "상세정보"])
        model = parse_label(official, "모델명", ["상세정보", "제품분류코드", "파생모델"])
        manufacturer = parse_label(official, "제조사", ["제조국", "수입업체"])

        required_fields = [authority, cert_type, cert_date, item, model, manufacturer]
        identity_ok = overlap >= 1
        if not (
            exact_number
            and fit
            and korea
            and identity_ok
            and all(required_fields)
            and "/release/certDetail" in final_safety_url
        ):
            continue

        product.update({
            "status": "포함",
            "origin": "대한민국 🇰🇷",
            "saleStatus": sale_basis,
            "age": normalize_age_label(age_text),
            "ageBasis": f"공식 판매 페이지: {age_text}",
            "kcNumber": number,
            "kcStatus": "적합",
            "kcDate": cert_date,
            "kcType": cert_type,
            "kcAuthority": authority,
            "kcItem": item,
            "kcModel": model,
            "manufacturer": manufacturer,
            "safetyKoreaUrl": final_safety_url,
            "checkedAt": date.today().isoformat(),
            "researchStatus": "최극상 교차검증 완료",
            "quality": "공식 판매·연령·KC·제조국·제품 동일성 교차검증",
        })
        evidence = product.setdefault("evidenceUrls", [])
        for url in [final_safety_url, final_sale_url]:
            if url not in evidence:
                evidence.insert(0, url)

        product["reason"] = (
            f"개별 판매 페이지 {final_sale_url}에서 현재 구매 가능과 연령 근거 {age_text} 및 "
            f"동일 제품 KC 번호 {number}를 확인했다. Safety Korea 상세 {final_safety_url}에서 "
            f"인증상태 적합과 제조국 대한민국과 인증 모델명 {model}과 제조사 {manufacturer}를 "
            f"대조했으며 제품명 식별 토큰 {overlap}개가 일치했다."
        )
        history = str(product.get("history", "")).strip()
        entry = (
            f"{date.today().isoformat()} 최극상 검증: 판매 페이지·연령·KC {number}·"
            f"Safety Korea·대한민국 제조·동일 제품 대조 완료"
        )
        product["history"] = f"{history}\n{entry}".strip()
        result.update(checked=True, changed=True)
        return result

    product["status"] = "보류"
    product["checkedAt"] = date.today().isoformat()
    product["researchStatus"] = "Safety Korea 동일 제품 최극상 검증 추가 확인 필요"
    product["reason"] = (
        f"{date.today().isoformat()} 판매 페이지에서 후보 KC 번호 {', '.join(numbers[:6])}를 "
        f"확인했으나 적합·대한민국 제조·필수 인증 필드·제품 동일성 조건을 모두 충족한 "
        f"Safety Korea 상세 근거를 확보하지 못해 보류한다."
    )
    result["errors"].append("ultra_strict_match")
    return result


def ddg_results(query: str) -> list[tuple[str, str]]:
    status, body, _ = fetch("https://html.duckduckgo.com/html/?q=" + quote(query))
    if status != 200:
        return []
    soup = BeautifulSoup(body, "html.parser")
    results: list[tuple[str, str]] = []
    for anchor in soup.select("a.result__a"):
        href = anchor.get("href", "")
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc:
            href = unquote(parse_qs(parsed.query).get("uddg", [""])[0])
        title = anchor.get_text(" ", strip=True)
        if href and title:
            results.append((title, href))
    return results


def discover(category: str, existing: list[dict], limit: int) -> list[dict]:
    queries = [
        f"대한민국 제조 {category} 아기 KC 인증 공식몰",
        f"{category} 0개월 대한민국 제조 상품정보제공고시",
        f"{category} 국내 제조 아기 인기 제품 공식",
        f"{category} KC 인증번호 제조국 대한민국 구매하기",
        f"{category} 베스트 국내 제조 신생아 공식몰",
    ]
    existing_urls = {url for item in existing for url in item.get("evidenceUrls", [])}
    existing_keys = {
        re.sub(r"\s+", "", f"{item.get('brand', '')}|{item.get('name', '')}").lower()
        for item in existing
    }
    added: list[dict] = []

    for query in queries:
        for title, url in ddg_results(query):
            if len(added) >= limit:
                return added
            if url in existing_urls or not direct_product_url(url):
                continue

            domain = urlparse(url).netloc.lower()
            blocked_domains = [
                "blog.", "cafe.", "youtube.", "instagram.", "facebook.",
                "tiktok.", "namu.wiki", "pinterest.",
            ]
            if not domain or any(value in domain for value in blocked_domains):
                continue

            clean_title = html.unescape(re.sub(r"\s*[-|:].*$", "", title)).strip()
            if len(clean_title) < 4:
                continue

            brand = clean_title.split()[0][:40]
            key = re.sub(r"\s+", "", f"{brand}|{clean_title}").lower()
            if key in existing_keys:
                continue

            digest = hashlib.sha1(f"{category}|{url}".encode()).hexdigest()[:12].upper()
            added.append({
                "id": f"DISC-{digest}",
                "category": category,
                "subtype": "",
                "brand": brand,
                "name": clean_title[:160],
                "status": "보류",
                "origin": "확인 중",
                "saleStatus": "개별 상품 후보 발견 · 실제 구매 가능 상태 재확인 필요",
                "age": "확인 중",
                "ageBasis": "",
                "kcNumber": "",
                "kcStatus": "",
                "kcDate": "",
                "kcType": "",
                "kcAuthority": "",
                "kcItem": "",
                "kcModel": "",
                "manufacturer": "",
                "safetyKoreaUrl": "",
                "checkedAt": date.today().isoformat(),
                "reason": (
                    f"검색어 '{query}'에서 개별 상품 후보 URL을 발견했다. "
                    f"공식 판매와 0~35개월과 대한민국 제조와 판매 페이지 동일 KC 번호와 "
                    f"Safety Korea 상세를 모두 확인하기 전까지 보류한다."
                ),
                "evidenceUrls": [url],
                "quality": "신규 후보 · 최극상 검증 전",
                "history": f"{date.today().isoformat()} 자동 후보 조사",
                "researchStatus": "최극상 전면 재검증 대기",
                "group": category,
            })
            existing_urls.add(url)
            existing_keys.add(key)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending-limit", type=int, default=50)
    parser.add_argument("--new-per-category", type=int, default=20)
    parser.add_argument("--all-categories", action="store_true")
    parser.add_argument("--quality", choices=["strict", "ultra"], default="ultra")
    args = parser.parse_args()

    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    state = (
        json.loads(STATE.read_text(encoding="utf-8"))
        if STATE.exists()
        else {"categoryIndex": 0}
    )

    candidates = [product for product in products if product.get("status") == "보류"]
    candidates.sort(key=lambda product: (
        not bool(product.get("kcNumber")),
        product.get("checkedAt", ""),
    ))
    audits = [
        revalidate(product, args.quality)
        for product in candidates[:args.pending_limit]
    ]

    categories = (
        CATEGORIES
        if args.all_categories
        else [CATEGORIES[int(state.get("categoryIndex", 0)) % len(CATEGORIES)]]
    )
    discovered: list[dict] = []
    for category in categories:
        discovered.extend(discover(
            category,
            products + discovered,
            args.new_per_category,
        ))
    products.extend(discovered)

    next_index = (
        int(state.get("categoryIndex", 0)) + len(categories)
    ) % len(CATEGORIES)

    new_state = {
        "lastRun": date.today().isoformat(),
        "qualityMode": args.quality,
        "scheduleTarget": "5분 최단 주기",
        "currentCategories": categories,
        "nextCategory": CATEGORIES[next_index],
        "categoryIndex": next_index,
        "pendingSelected": len(audits),
        "verifiedThisRun": sum(bool(item.get("changed")) for item in audits),
        "includedThisRun": sum(
            1 for product in products if product.get("status") == "포함"
        ),
        "newCandidates": len(discovered),
        "errors": sum(bool(item.get("errors")) for item in audits),
        "totalProducts": len(products),
    }
    SOURCE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    STATE.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    AUDIT.write_text(
        json.dumps(
            {"state": new_state, "products": audits},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(new_state, ensure_ascii=False))


if __name__ == "__main__":
    main()
