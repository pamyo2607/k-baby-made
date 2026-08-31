"""Runtime-only network bounds for the ultra research entrypoint.

Loaded automatically by Python because the entrypoint lives in scripts/.
Only run_ultra_naver.py is patched. Other repository scripts keep their
original behavior. Evidence gates remain fail-closed: a transient miss stays
pending and is retried on a later campaign rotation.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys

if os.path.basename(sys.argv[0]) == "run_ultra_naver.py":
    try:
        import html as html_lib
        from urllib.parse import parse_qs, quote, unquote, urlparse

        from bs4 import BeautifulSoup
        import requests
        import research_runner as rr

        _original_fetch = rr.fetch

        def _single_fetch(url: str, timeout: int = 8) -> tuple[int, str, str]:
            try:
                response = rr.SESSION.get(url, timeout=timeout, allow_redirects=True)
                if response.status_code == 200:
                    response.encoding = response.apparent_encoding or response.encoding
                    return 200, response.text, str(response.url)
                return response.status_code, "", str(response.url)
            except requests.RequestException:
                return 0, "", url

        def bounded_official_fetch(url: str) -> tuple[int, str, str]:
            host = urlparse(url).netloc.lower()
            if "safetykorea.kr" in host or "duckduckgo.com" in host:
                return _single_fetch(url, timeout=7)
            return _original_fetch(url)

        def _extract_search_results(source: str, engine: str) -> list[tuple[str, str]]:
            soup = BeautifulSoup(source, "html.parser")
            results: list[tuple[str, str]] = []
            seen: set[str] = set()
            if engine == "ddg":
                anchors = soup.select("a.result__a")
            elif engine == "bing":
                anchors = soup.select("li.b_algo h2 a")
            else:
                anchors = soup.select("a[href]")
            for anchor in anchors:
                href = html_lib.unescape(str(anchor.get("href", "")).strip())
                title = anchor.get_text(" ", strip=True)
                if engine == "ddg" and href:
                    parsed = urlparse(href)
                    if "duckduckgo.com" in parsed.netloc:
                        href = unquote(parse_qs(parsed.query).get("uddg", [""])[0])
                if not href.startswith(("http://", "https://")) or not title:
                    continue
                host = urlparse(href).netloc.lower()
                if engine == "naver" and (
                    host.endswith("naver.com")
                    and not any(value in host for value in ("smartstore", "brand"))
                ):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                results.append((title, href))
                if len(results) >= 30:
                    break
            return results

        def _search_endpoint(engine: str, url: str) -> list[tuple[str, str]]:
            status, body, _ = _single_fetch(url, timeout=7)
            if status != 200 or not body:
                return []
            return _extract_search_results(body, engine)

        def fast_search_results(query: str) -> list[tuple[str, str]]:
            endpoints = (
                ("naver", "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query=" + quote(query)),
                ("ddg", "https://html.duckduckgo.com/html/?q=" + quote(query)),
                ("bing", "https://www.bing.com/search?q=" + quote(query)),
            )
            merged: list[tuple[str, str]] = []
            seen: set[str] = set()
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(_search_endpoint, engine, url) for engine, url in endpoints]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        batch = future.result()
                    except Exception:
                        batch = []
                    for title, href in batch:
                        if href in seen:
                            continue
                        seen.add(href)
                        merged.append((title, href))
                        if len(merged) >= 40:
                            return merged
            return merged

        rr.fetch = bounded_official_fetch
        rr.ddg_results = fast_search_results

        import run_ultra_parallel as parallel

        _original_parallel_bounded_fetch = parallel.bounded_fetch

        def fast_parallel_bounded_fetch(url: str) -> tuple[int, str, str]:
            host = urlparse(url).netloc.lower()
            if "safetykorea.kr" in host:
                hit = parallel.cached(url)
                if hit is not None:
                    return hit
                result = _single_fetch(url, timeout=7)
                return parallel.store(url, result)
            return _original_parallel_bounded_fetch(url)

        parallel.bounded_fetch = fast_parallel_bounded_fetch
        parallel.PROBE_TIMEOUT = 6
        parallel.MAX_REVALIDATE_WORKERS = 25
    except Exception as exc:  # fail closed; runtime audit will expose unresolved evidence
        print(f"sitecustomize research patch unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
