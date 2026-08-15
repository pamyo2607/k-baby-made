#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/naver-resolution-diagnostics.json"
CATEGORIES = {
    "완구": ("완구", "장난감", "인형", "볼풀", "딸랑이", "모빌"),
    "구강·치발기": ("치발기", "티더", "잇몸"),
    "턱받이": ("턱받이", "빕", "bib"),
    "수유용품": ("수유", "젖병", "분유", "빨대컵", "유축"),
    "이유식·식기": ("이유식", "흡착식판", "스푼", "유아식기", "아기식기"),
    "위생·기저귀": ("물티슈", "기저귀", "손수건", "목욕", "세정", "면봉"),
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
    "Referer": "https://search.naver.com/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def korean(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text))


def extract_redirect(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for selector, attr in [
        ("link[rel='canonical']", "href"),
        ("meta[property='og:url']", "content"),
        ("meta[http-equiv='refresh']", "content"),
    ]:
        node = soup.select_one(selector)
        if node and node.get(attr):
            value = str(node.get(attr))
            if selector.startswith("meta[http") and "url=" in value.lower():
                value = re.split(r"url=", value, flags=re.I, maxsplit=1)[-1].strip(" '\"")
            candidates.append(value)
    patterns = [
        r"location(?:\.href|\.replace)?\s*[=(]\s*['\"](https?://[^'\"]+)",
        r"url\s*[:=]\s*['\"](https?://[^'\"]+)",
        r"(https?://(?:smartstore|brand|m\.smartstore|shopping|m\.shopping)\.naver\.com/[^'\"<>\\ ]+)",
        r"(https?://[^'\"<>\\ ]+/(?:products?|goods|item|shopdetail|detail|view)/[^'\"<>\\ ]+)",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html, flags=re.I))
    for value in candidates:
        value = value.replace("&amp;", "&")
        if value.startswith("http") and "search.naver.com" not in value:
            return value
    return base_url


def resolve(url: str) -> dict:
    try:
        response = SESSION.get(url, timeout=20, allow_redirects=True)
    except requests.RequestException as exc:
        return {"status": 0, "error": type(exc).__name__, "input": url}
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    extracted = extract_redirect(response.text, response.url)
    return {
        "input": url,
        "status": response.status_code,
        "finalUrl": response.url,
        "extractedUrl": extracted,
        "bytes": len(response.content),
        "pageTitle": soup.title.get_text(" ", strip=True)[:180] if soup.title else "",
        "history": [item.url for item in response.history],
    }


def main() -> None:
    output = {"checkedAt": datetime.now(timezone.utc).isoformat(), "categories": {}}
    for category, terms in CATEGORIES.items():
        query = f'"{category}" 아기 국내제조 KC 제품 구매'
        search_url = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query=" + quote(query)
        response = SESSION.get(search_url, timeout=25)
        response.encoding = response.apparent_encoding or response.encoding
        soup = BeautifulSoup(response.text, "html.parser")
        matches = []
        seen = set()
        for anchor in soup.select("a[href]"):
            title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            href = str(anchor.get("href", "")).strip()
            lower = title.lower()
            if not korean(title) or not any(term.lower() in lower for term in terms):
                continue
            domain = urlparse(href).netloc.lower()
            if not domain or href in seen:
                continue
            if not any(value in domain for value in (
                "ader.naver.com", "shopping.naver.com", "smartstore.naver.com",
                "brand.naver.com", "coupang.com", "11st.co.kr", "gmarket.co.kr",
                "auction.co.kr", "ssg.com", "lotteon.com",
            )):
                continue
            seen.add(href)
            matches.append({"title": title[:180], "resolution": resolve(href)})
            if len(matches) >= 8:
                break
        output["categories"][category] = {
            "searchStatus": response.status_code,
            "matches": matches,
        }
    REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        category: [
            {
                "title": item["title"],
                "status": item["resolution"].get("status"),
                "final": item["resolution"].get("finalUrl"),
                "extracted": item["resolution"].get("extractedUrl"),
            }
            for item in info["matches"]
        ]
        for category, info in output["categories"].items()
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
