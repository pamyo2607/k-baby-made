#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/search-provider-diagnostics.json"
CATEGORIES = ["완구", "구강치발기", "턱받이", "수유용품", "이유식용품", "위생용품"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean_link(href: str) -> str:
    href = str(href or "").strip()
    if href.startswith("/url?"):
        params = parse_qs(urlparse(href).query)
        href = params.get("q", params.get("url", [""]))[0]
    if href.startswith("https://www.google.com/url?"):
        params = parse_qs(urlparse(href).query)
        href = params.get("q", params.get("url", [""]))[0]
    return unquote(href)


def external(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    domain = urlparse(url).netloc.lower()
    blocked = (
        "google.com", "naver.com/search", "search.naver.com", "brave.com",
        "youtube.com", "instagram.com", "facebook.com", "wikipedia.org",
    )
    return not any(value in domain or value in url for value in blocked)


def fetch(provider: str, query: str) -> dict:
    if provider == "google":
        url = "https://www.google.com/search?hl=ko&gl=kr&num=30&filter=0&q=" + quote(query)
    elif provider == "naver":
        url = "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query=" + quote(query)
    elif provider == "brave":
        url = "https://search.brave.com/search?source=web&q=" + quote(query)
    else:
        raise ValueError(provider)

    try:
        response = SESSION.get(url, timeout=25, allow_redirects=True)
    except requests.RequestException as exc:
        return {"status": 0, "error": type(exc).__name__, "results": []}

    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = clean_link(anchor.get("href", ""))
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if not external(href) or not title or href in seen:
            continue
        seen.add(href)
        results.append({"title": title[:180], "url": href[:500]})
        if len(results) >= 15:
            break
    return {
        "status": response.status_code,
        "finalUrl": response.url,
        "bytes": len(response.content),
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "results": results,
    }


def main() -> None:
    output = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "providers": {},
    }
    for category in CATEGORIES:
        query = f'"{category}" 아기 국내제조 KC 제품 구매'
        output["providers"][category] = {
            provider: fetch(provider, query)
            for provider in ("google", "naver", "brave")
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        category: {
            provider: {
                "status": info.get("status"),
                "count": len(info.get("results", [])),
                "first": (info.get("results") or [{}])[0],
            }
            for provider, info in providers.items()
        }
        for category, providers in output["providers"].items()
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
