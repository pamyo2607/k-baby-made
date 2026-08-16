#!/usr/bin/env python3
"""Shared dynamic data-contract helpers for generated assets and validation."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

SALE_STATUS_BLOCKED = re.compile(
    r"재검증|확인\s*(?:필요|중)|미확인|품절|종료|단종|직구|구매\s*불가"
)
SALE_STATUS_CONFIRMED = re.compile(
    r"판매.*확인|구매.*(?:링크|가능|확인)|주문.*(?:가능|확인)"
)


def is_current_sale(product: Mapping[str, object]) -> bool:
    """Return the single canonical current-sale decision used by all assets."""
    sale_status = str(product.get("saleStatus", ""))
    return (
        product.get("status") != "제외"
        and not product.get("archive")
        and SALE_STATUS_BLOCKED.search(sale_status) is None
        and SALE_STATUS_CONFIRMED.search(sale_status) is not None
    )


def current_sale_count(products: Iterable[Mapping[str, object]]) -> int:
    return sum(is_current_sale(product) for product in products)
