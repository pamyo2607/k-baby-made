#!/usr/bin/env python3
"""Strict pending-product revalidation and conservative candidate discovery.

A search result can create a pending candidate only. Included status requires a
current product detail page and an exact Safety Korea detail that confirms the
same KC number, fit status, Korean manufacture and matching product identity.
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
CATEGORIES = ["완구","구강치발기","턱받이","수유용품","이유식용품","위생용품"]
KC_PATTERN = re.compile(r"\b[A-Z]{1,3}\d[A-Z0-9-]{5,}[A-Z0-9]\b", re.I)
BLOCKED_PATHS = ["/search","/category","/categories","/guide","/board","/event","/brand"]
DIRECT_HINTS = ["/product","/products","/item","/goods","/shopdetail","/view","/detail"]
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; KBabyMadeEvidenceBot/1.0; +https://github.com/pamyo2607/k-baby-made)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
})
TIMEOUT = 18


def fetch(url: str) -> tuple[int, str]:
    for pause in [0,5,15,45]:
        if pause:
            time.sleep(pause)
        try:
            response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding or response.encoding
                return 200, response.text
            if response.status_code not in {403,429,500,502,503,504}:
                return response.status_code, ""
        except requests.RequestException:
            continue
    return 0, ""


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


def token_overlap(product: dict, official_text: str) -> int:
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", f"{product.get('brand','')} {product.get('name','')}")
    ignored = {"세트","아기","유아","실리콘","제품","컬러","개입","국내","공식"}
    return sum(1 for token in set(tokens) if token not in ignored and token.lower() in official_text.lower())


def parse_label(text: str, label: str, next_labels: list[str]) -> str:
    tail = "|".join(re.escape(item) for item in next_labels)
    match = re.search(re.escape(label) + r"\s*[|:]?\s*(.+?)(?=\s*(?:" + tail + r")\s*[|:]?|$)", text)
    return match.group(1).strip() if match else ""


def revalidate(product: dict) -> dict:
    result = {"id": product.get("id"), "checked": False, "changed": False, "errors": []}
    sale_url = first_sales_url(product)
    if not sale_url:
        product["researchStatus"] = "판매 상세 URL 확인 필요"
        result["errors"].append("sales_url")
        return result
    sale_status, sale_html = fetch(sale_url)
    if sale_status != 200:
        product["researchStatus"] = f"판매 페이지 재확인 필요 HTTP {sale_status or 'ERR'}"
        result["errors"].append(f"sale_{sale_status}")
        return result
    sale_text = plain(sale_html)
    if any(word in sale_text for word in ["판매종료","현재 판매하지","품절 상품입니다"]):
        product["status"] = "제외"
        product["reason"] = f"{date.today().isoformat()} 판매 상세 페이지에서 판매 종료 또는 품절 상태를 확인해 제외했다. {sale_url}"
        product["checkedAt"] = date.today().isoformat()
        result.update(checked=True, changed=True)
        return result

    numbers: list[str] = []
    existing = str(product.get("kcNumber", "")).strip()
    if KC_PATTERN.fullmatch(existing):
        numbers.append(existing)
    for found in KC_PATTERN.findall(sale_text):
        if found not in numbers:
            numbers.append(found)
    if not numbers:
        product["researchStatus"] = "동일 제품 KC 번호 확인 필요"
        product["checkedAt"] = date.today().isoformat()
        result["errors"].append("kc_number")
        return result

    for number in numbers[:4]:
        safety_url = cert_detail_url(number)
        safety_status, safety_html = fetch(safety_url)
        if safety_status != 200:
            continue
        official = plain(safety_html)
        exact_number = number.lower() in official.lower()
        fit = bool(re.search(r"인증상태\s*[|:]?\s*적합", official))
        korea = bool(re.search(r"제조국\s*[|:]?\s*(한국|대한민국)", official))
        model_match = token_overlap(product, official) >= 1
        if not (exact_number and fit and korea and model_match):
            continue

        authority = parse_label(official, "인증기관", ["인증구분","인증번호"])
        cert_type = parse_label(official, "인증구분", ["인증번호","인증상태"])
        cert_date = parse_label(official, "인증일자", ["인증변경일자","인증변경사유"])
        model = parse_label(official, "모델명", ["상세정보","제품분류코드","파생모델"])
        manufacturer = parse_label(official, "제조사", ["제조국","수입업체"])
        if cert_date and re.fullmatch(r"\d{8}", cert_date):
            cert_date = f"{cert_date[:4]}-{cert_date[4:6]}-{cert_date[6:]}"
        product.update({
            "status": "포함",
            "origin": "대한민국 🇰🇷",
            "kcNumber": number,
            "kcStatus": "적합",
            "kcDate": cert_date,
            "kcType": cert_type,
            "kcAuthority": authority,
            "kcModel": model,
            "manufacturer": manufacturer,
            "safetyKoreaUrl": safety_url,
            "checkedAt": date.today().isoformat(),
            "researchStatus": "검증 완료",
        })
        evidence = product.setdefault("evidenceUrls", [])
        if safety_url not in evidence:
            evidence.insert(0, safety_url)
        product["reason"] = (
            f"Safety Korea 인증번호 {number}의 인증상태 적합과 제조국 한국 및 동일 제품 식별정보를 확인했다. "
            f"국내 판매 상세 페이지 {sale_url}에서 현재 판매와 같은 인증번호를 교차 확인했다."
        )
        result.update(checked=True, changed=True)
        return result

    product["status"] = "보류"
    product["checkedAt"] = date.today().isoformat()
    product["researchStatus"] = "Safety Korea 동일 제품 연결 추가 확인 필요"
    product["reason"] = (
        f"{date.today().isoformat()} 판매 페이지는 확인했으나 후보 KC 번호 "
        f"{', '.join(numbers[:4])} 중 적합·한국 제조·동일 제품 연결을 모두 충족한 상세 근거를 확보하지 못해 보류한다."
    )
    result["errors"].append("strict_match")
    return result


def ddg_results(query: str) -> list[tuple[str, str]]:
    status, body = fetch("https://html.duckduckgo.com/html/?q=" + quote(query))
    if status != 200:
        return []
    soup = BeautifulSoup(body, "html.parser")
    results = []
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
        f"{category} 0개월 3개월 대한민국 제조 상품",
        f"{category} 국내 제조 아기 인기 제품",
    ]
    existing_urls = {url for item in existing for url in item.get("evidenceUrls", [])}
    existing_keys = {re.sub(r"\s+", "", f"{item.get('brand','')}|{item.get('name','')}").lower() for item in existing}
    added: list[dict] = []
    for query in queries:
        for title, url in ddg_results(query):
            if len(added) >= limit:
                return added
            if url in existing_urls or not direct_product_url(url):
                continue
            domain = urlparse(url).netloc.lower()
            if not domain or any(x in domain for x in ["blog.","cafe.","youtube.","instagram.","facebook."]):
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
                "id": f"DISC-{digest}", "category": category, "subtype": "", "brand": brand,
                "name": clean_title[:160], "status": "보류", "origin": "확인 중",
                "saleStatus": "개별 상품 후보 발견 · 실제 판매 상태 재확인 필요", "age": "확인 중",
                "ageBasis": "", "kcNumber": "", "kcStatus": "", "kcDate": "", "kcType": "",
                "kcAuthority": "", "kcItem": "", "kcModel": "", "manufacturer": "",
                "safetyKoreaUrl": "", "checkedAt": date.today().isoformat(),
                "reason": f"검색어 '{query}'에서 개별 상품 후보 URL을 발견했다. 공식 판매와 0~35개월과 대한민국 제조와 동일 제품 KC 상세를 모두 확인하기 전까지 보류한다.",
                "evidenceUrls": [url], "quality": "신규 후보 · 엄격 검증 전",
                "history": f"{date.today().isoformat()} 자동 후보 조사", "researchStatus": "전면 재검증 대기",
                "group": category,
            })
            existing_urls.add(url)
            existing_keys.add(key)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending-limit", type=int, default=50)
    parser.add_argument("--new-per-category", type=int, default=30)
    parser.add_argument("--all-categories", action="store_true")
    args = parser.parse_args()

    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"categoryIndex": 0}
    candidates = [product for product in products if product.get("status") == "보류"]
    candidates.sort(key=lambda product: (not bool(product.get("kcNumber")), product.get("checkedAt", "")))
    audits = [revalidate(product) for product in candidates[:args.pending_limit]]

    categories = CATEGORIES if args.all_categories else [CATEGORIES[int(state.get("categoryIndex", 0)) % len(CATEGORIES)]]
    discovered: list[dict] = []
    for category in categories:
        discovered.extend(discover(category, products + discovered, args.new_per_category))
    products.extend(discovered)

    next_index = (int(state.get("categoryIndex", 0)) + len(categories)) % len(CATEGORIES)
    new_state = {
        "lastRun": date.today().isoformat(), "currentCategories": categories,
        "nextCategory": CATEGORIES[next_index], "categoryIndex": next_index,
        "pendingSelected": len(audits), "verifiedThisRun": sum(bool(item.get("changed")) for item in audits),
        "newCandidates": len(discovered), "errors": sum(bool(item.get("errors")) for item in audits),
        "totalProducts": len(products),
    }
    SOURCE.write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    AUDIT.write_text(json.dumps({"state": new_state, "products": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(new_state, ensure_ascii=False))


if __name__ == "__main__":
    main()
