#!/usr/bin/env python3
"""Run the ultra pipeline with real Naver discovery and sale rechecks."""
from __future__ import annotations

import naver_pending_revalidation as pending
import naver_product_discovery as naver
import run_ultra_parallel as pipeline

_original_revalidate = pipeline.safe_revalidate


def revalidate(product: dict) -> dict:
    try:
        result = _original_revalidate(product)
    except Exception:
        result = {"checked": False, "changed": False, "errors": ["primary_revalidation_exception"]}
    if result.get("checked"):
        return result
    return pending.revalidate(product)


def discover(category: str, products: list[dict]) -> list[dict]:
    try:
        return naver.discover(category, products, pipeline.NEW_PER_CATEGORY)
    except Exception as exc:
        print(f"naver discovery failed for {category}: {type(exc).__name__}: {exc}")
        return []


pipeline.safe_revalidate = revalidate
pipeline.safe_discover = discover
pipeline.main()
