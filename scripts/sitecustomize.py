"""Runtime-only network bounds for the ultra research entrypoint.

Loaded automatically by Python because the entrypoint lives in scripts/.
Only run_ultra_naver.py is patched. Other repository scripts keep their
original behavior. Evidence gates remain fail-closed: a transient miss stays
pending and is retried on a later campaign rotation.
"""
from __future__ import annotations

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

        def _single_fetch(url: str, timeout: int = 12) -> tuple[int, str, str]:
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
                return _single_fetch(url, timeout=12)
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

        def fast_search_results(query: str) -> list[tuple[str, str]]:
            endpoints = (
                ("naver", "https://search.naver.com/search.naver?where=nexearch&sm=top_hty&query=" + quote(query)),
                ("ddg", "https://html.duckduckgo.com/html/?q=" + quote(query)),
                ("bing", "https://www.bing.com/search?q=" + quote(query)),
            )
            for engine, url in endpoints:
                status, body, _ = _single_fetch(url, timeout=10)
                if status != 200 or not body:
                    continue
                results = _extract_search_results(body, engine)
                if results:
                    return results
            return []

        rr.fetch = bounded_official_fetch
        rr.ddg_results = fast_search_results

        # run_ultra_parallel.main normally replaces rr.fetch with its own
        # multi-retry bounded_fetch. Patch that function itself so exact
        # Safety Korea certificate details fail fast and are retried next cycle
        # instead of blocking one batch for minutes. Non-official pages keep
        # the original bounded retry policy.
        import run_ultra_parallel as parallel

        _original_parallel_bounded_fetch = parallel.bounded_fetch

        def fast_parallel_bounded_fetch(url: str) -> tuple[int, str, str]:
            host = urlparse(url).netloc.lower()
            if "safetykorea.kr" in host:
                hit = parallel.cached(url)
                if hit is not None:
                    return hit
                result = _single_fetch(url, timeout=12)
                return parallel.store(url, result)
            return _original_parallel_bounded_fetch(url)

        parallel.bounded_fetch = fast_parallel_bounded_fetch
    except Exception as exc:  # fail closed; runtime audit will expose unresolved evidence
        print(f"sitecustomize research patch unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
