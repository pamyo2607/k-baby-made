#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver product-detail discovery."""
from __future__ import annotations

import naver_product_discovery as naver
import run_ultra_parallel as pipeline


def discover(category: str, products: list[dict]) -> list[dict]:
    try:
        return naver.discover(category, products, pipeline.NEW_PER_CATEGORY)
    except Exception as exc:
        print(f"naver discovery failed for {category}: {type(exc).__name__}: {exc}")
        return []


pipeline.safe_discover = discover
pipeline.main()
