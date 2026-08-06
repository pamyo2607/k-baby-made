#!/usr/bin/env python3
"""Discover real Korean retail product-detail candidates through Naver search.

The module never marks a product included. It only emits pending candidates when
all of these are true:
- the search-result title is Korean and matches the requested baby-product category
- the result resolves to an individual retail or official product-detail URL
- the URL is not a search page, category page, blog, cafe, video, social page or ad bridge

Final inclusion remains controlled by the existing ultra-quality evidence gate.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import re
import threading
from datetime import date
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

CATEGORY_QUERIES = {
    "완구": [
        "아기 딸랑이", "신생아 모빌", "아기 애착인형", "아기 볼풀공",
        "아기 촉감 장난감", "아기 원목 장난감", "아기 목욕 장난감",
        "아기 에듀볼", "영아 완구", "신생아 장난감",
    ],
    "구강치발기": [
        "아기 치발기", "손목 치발기", "실리콘 치발기", "과즙망 치발기",
        "신생아 치발기", "아기 티더", "잇몸 마사지 치발기",
        "아기 구강발달 완구", "아기 과일 치발기", "아기 치발기 세트",
    ],
    "턱받이": [
        "아기 턱받이", "실리콘 턱받이", "이유식 턱받이", "방수 턱받이",
        "신생아 턱받이", "아기 빕", "긴팔 이유식 턱받이",
        "아기 거즈 턱받이", "유아 턱받이", "아기 일회용 턱받이",
    ],
    "수유용품": [
        "아기 젖병", "아기 빨대컵", "수유쿠션", "분유 케이스",
        "젖병 건조대", "모유 저장팩", "수유패드", "젖병 손잡이",
        "아기 물병", "신생아 수유용품",
    ],
    "이유식용품": [
        "아기 흡착식판", "이유식 스푼", "이유식 용기", "이유식 큐브",
        "아기 식기", "유아 식판", "이유식 도마", "이유식 조리도구",
        "아기 빨대컵", "이유식 보관용기",
    ],
    "위생용품": [
        "아기 물티슈", "아기 기저귀", "아기 면봉", "아기 목욕타월",
        "아기 손수건", "아기 욕조", "아기 세정제", "아기 샴푸캡",
        "아기 목욕장갑", "아기 위생용품",
    ],
}
CATEGORY_TERMS = {
    "완구": ("완구", "장난감", "딸랑이", "모빌", "인형", "볼풀", "에듀볼", "촉감", "원목", "놀이"),
    "구강치발기": ("치발기", "티더", "teether", "잇몸", "구강발달", "과즙망"),
    "턱받이": ("턱받이", "빕", "bib"),
    "수유용품": ("젖병", "빨대컵", "수유", "분유", "유축", "모유", "수유패드"),
    "이유식용품": ("이유식", "식판", "스푼", "숟가락", "아기식기", "유아식기", "큐브", "보관용기"),
    "위생용품": ("물티슈", "기저귀", "면봉", "목욕", "타월", "손수건", "욕조", "세정", "샴푸캡", "위생"),
}
SEARCH_SUFFIXES = (
    "국내생산 KC 구매",
    "국내제조 아기 제품",
    "국산 공식몰",
    "스마트스토어",
)
BLOCKED_DOMAINS = (
    "blog.naver.com", "m.blog.naver.com", "cafe.naver.com", "youtube.com",
    "instagram.com", "facebook.com", "tiktok.com", "pinterest.com",
    "tistory.com", "wikipedia.org", "namu.wiki", "search.naver.com",
    "dict.naver.com", "map.naver.com", "help.naver.com", "ads.naver.com",
)
BRIDGE_DOMAINS = (
    "ader.naver.com", "cr3.shopping.naver.com", "shopping-phinf.pstatic.net",
)
DIRECT_HINTS = (
    "/products/", "/product/", "/vp/products/", "/goods/", "/item/",
    "/shopdetail", "/shopview", "/detail/", "/view/", "/i/item/",
)
THREAD_LOCAL = threading.local()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
    "Referer": "https://search.naver.com/",
}


def session() -> requests.Session:
    current = getattr(THREAD_LOCAL, "session", None)
    if current is None:
        current = requests.Session()
        current.headers.update(HEADERS)
        THREAD_LOCAL.session = current
    return current


def has_korean(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value))


def clean_title(value: str) -> str:
    value = html.unescape(re.sub(r"\s+", " ", value or "")).strip()
    value = re.sub(r"^(?:광고|AD)\s*", "", value, flags=re.I)
    return value[:180]


def title_matches(category: str, title: str) -> bool:
    lower = title.lower()
    return has_korean(title) and any(term.lower() in lower for term in CATEGORY_TERMS[category])


def blocked_url(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return not domain or any(domain == value or domain.endswith("." + value) for value in BLOCKED_DOMAINS)


def direct_product_url(url: str) -> bool:
    if not url.startswith(("https://", "http://")) or blocked_url(url):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(value in path for value in ("/search", "/category", "/categories", "/board", "/event", "/guide")):
        return False
    if parsed.netloc.lower() in BRIDGE_DOMAINS:
        return False
    return any(value in path for value in DIRECT_HINTS)


def canonicalize(url: str) -> str:
    parsed = urlparse(url)
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"itemid", "vendoritemid", "nv_mid", "id", "no", "productno", "pack_content_id", "thiscategory", "inicategory"}:
            kept.append((key, value))
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", urlencode(kept), ""))


def extract_embedded_target(source: str) -> str:
    soup = BeautifulSoup(source, "html.parser")
    candidates: list[str] = []
    for selector, attribute in (
        ("link[rel='canonical']", "href"),
        ("meta[property='og:url']", "content"),
        ("meta[http-equiv='refresh']", "content"),
    ):
        node = soup.select_one(selector)
        if not node or not node.get(attribute):
            continue
        value = str(node.get(attribute))
        if "refresh" in selector and "url=" in value.lower():
            value = re.split(r"url=", value, flags=re.I, maxsplit=1)[-1].strip(" '\"")
        candidates.append(value)
    candidates.extend(re.findall(
        r"https?://[^'\"<>\\ ]+(?:/products?/|/vp/products/|/goods/|/item/|/shopdetail|/shopview|/detail/|/view/)[^'\"<>\\ ]*",
        source,
        flags=re.I,
    ))
    for value in candidates:
        value = html.unescape(value).replace("\\/", "/")
        if direct_product_url(value):
            return value
    return ""


def resolve_product_url(url: str) -> str:
    if direct_product_url(url):
        return canonicalize(url)
    parsed = urlparse(url)
    if parsed.netloc.lower() not in BRIDGE_DOMAINS:
        return ""
    try:
        response = session().get(url, timeout=12, allow_redirects=True)
    except requests.RequestException:
        return ""
    final_url = str(response.url)
    if direct_product_url(final_url):
        return canonicalize(final_url)
    response.encoding = response.apparent_encoding or response.encoding
    embedded = extract_embedded_target(response.text)
    return canonicalize(embedded) if embedded else ""


def collect_query(category: str, query: str) -> list[tuple[str, str, str]]:
    url = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query=" + quote(query)
    try:
        response = session().get(url, timeout=20)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        title = clean_title(anchor.get_text(" ", strip=True))
        href = str(anchor.get("href", "")).strip()
        if not title_matches(category, title) or not href.startswith(("http://", "https://")):
            continue
        if blocked_url(href) or href in seen:
            continue
        domain = urlparse(href).netloc.lower()
        if domain not in BRIDGE_DOMAINS and not direct_product_url(href):
            continue
        seen.add(href)
        results.append((title, href, query))
    return results


def infer_brand(title: str) -> str:
    cleaned = re.sub(r"^[\[({<][^\])}>]{1,30}[\])}>]\s*", "", title).strip()
    token = cleaned.split()[0] if cleaned.split() else "브랜드 확인 중"
    return token[:40]


def discover(category: str, existing: list[dict], limit: int = 20) -> list[dict]:
    bases = CATEGORY_QUERIES.get(category, [category])
    queries = [f"{base} {suffix}" for base in bases for suffix in SEARCH_SUFFIXES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        batches = list(executor.map(lambda query: collect_query(category, query), queries))
    raw = [item for batch in batches for item in batch]

    existing_urls = {
        canonicalize(str(url))
        for item in existing
        for url in item.get("evidenceUrls", [])
        if str(url).startswith(("http://", "https://"))
    }
    existing_keys = {
        re.sub(r"[^0-9a-z가-힣]+", "", f"{item.get('brand', '')}|{item.get('name', '')}".lower())
        for item in existing
    }

    resolved: list[tuple[str, str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(resolve_product_url, href): (title, query)
            for title, href, query in raw[:240]
        }
        for future in concurrent.futures.as_completed(futures):
            title, query = futures[future]
            try:
                final_url = future.result()
            except Exception:
                final_url = ""
            if final_url:
                resolved.append((title, final_url, query))

    added: list[dict] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    for title, final_url, query in resolved:
        if len(added) >= limit:
            break
        brand = infer_brand(title)
        key = re.sub(r"[^0-9a-z가-힣]+", "", f"{brand}|{title}".lower())
        if (
            final_url in existing_urls
            or final_url in seen_urls
            or key in existing_keys
            or key in seen_keys
        ):
            continue
        digest = hashlib.sha1(f"{category}|{final_url}".encode()).hexdigest()[:12].upper()
        added.append({
            "id": f"DISC-{digest}",
            "category": category,
            "subtype": "",
            "brand": brand,
            "name": title,
            "status": "보류",
            "origin": "확인 중",
            "saleStatus": "네이버 검색에서 국내 상품 상세 URL 확인 · 현재 판매 상태 재검증 대기",
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
                f"네이버 검색어 '{query}'에서 카테고리가 일치하는 실제 상품 상세 URL을 확인했다. "
                "국내 현재 판매와 0~35개월 대상과 대한민국 완제품 제조와 동일 제품 KC 번호와 "
                "Safety Korea 상세 근거를 모두 대조하기 전까지 보류한다."
            ),
            "evidenceUrls": [final_url],
            "quality": "실제 국내 상품 상세 후보 · 최극상 검증 전",
            "history": f"{date.today().isoformat()} 네이버 상품 상세 후보 조사",
            "researchStatus": "최극상 전면 재검증 대기",
            "group": category,
            "discoveryProvider": "Naver",
        })
        seen_urls.add(final_url)
        seen_keys.add(key)
    return added


if __name__ == "__main__":
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1] / "data/master-products.json"
    products = json.loads(source.read_text(encoding="utf-8"))
    print(json.dumps({category: len(discover(category, products, 20)) for category in CATEGORY_QUERIES}, ensure_ascii=False))
