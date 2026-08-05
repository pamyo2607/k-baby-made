#!/usr/bin/env python3
"""Fail closed when a public included product lacks exact official evidence."""
from __future__ import annotations
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/master-products.json"
BANNED_UI_TERMS = [
    "공식 SKU","제품 ID","세부 유형","추정 판매 순위","추정 판매 순위 미공개",
    "모델 정보","KC 비대상","인증 대상 아님"
]
GENERIC_REASONS = [
    "원본 Master DB에서 기준 충족 판정을 완료한 제품으로 기존 판정과 근거 URL을 보존해 복원함",
    "원본 압축 DB 복원 · 포함 판정 유지",
    "기존 조사 결과 유지",
    "공식 근거 확인 완료",
    "KC 인증 확인",
]
KC_PATTERN = re.compile(r"^[A-Z]{1,3}\d[A-Z0-9-]*[A-Z0-9]$", re.I)

def is_direct_product_url(url: str) -> bool:
    if not url.startswith(("http://","https://")):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    blocked = ["/search", "/category", "/categories", "/itemsearch", "/certificationsearch"]
    return not any(part in path for part in blocked)

def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids: set[str] = set()
    unique_keys: set[tuple[str,str,str]] = set()

    for product in products:
        pid = str(product.get("id","")).strip()
        if not pid:
            errors.append("missing internal id")
        elif pid in ids:
            errors.append(f"duplicate id: {pid}")
        ids.add(pid)
        key = (
            re.sub(r"\s+","",str(product.get("category","")).lower()),
            re.sub(r"\s+","",str(product.get("brand","")).lower()),
            re.sub(r"\s+","",str(product.get("name","")).lower()),
        )
        if key in unique_keys:
            errors.append(f"duplicate product key: {key}")
        unique_keys.add(key)

        if product.get("status") != "포함":
            continue
        checks = {
            "made in Korea": "대한민국" in str(product.get("origin","")),
            "exact KC": bool(KC_PATTERN.fullmatch(str(product.get("kcNumber","")).strip())),
            "fit status": product.get("kcStatus") == "적합",
            "certification date": bool(product.get("kcDate")),
            "certification type": bool(product.get("kcType")),
            "authority": bool(product.get("kcAuthority")),
            "manufacturer": bool(product.get("manufacturer")),
            "Safety Korea detail": "/release/certDetail" in str(product.get("safetyKoreaUrl","")),
            "number in reason": str(product.get("kcNumber","")) in str(product.get("reason","")),
            "specific reason": not any(generic == str(product.get("reason","")).strip() for generic in GENERIC_REASONS),
            "age": bool(re.search(r"\d+\s*개월|0~35", str(product.get("age","")))),
            "current sale evidence": any(is_direct_product_url(url) for url in product.get("evidenceUrls",[]) if "safetykorea" not in url),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            errors.append(f"{pid}: {', '.join(failed)}")

    for relative in ["public/index.html","public/app.js"]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for term in BANNED_UI_TERMS:
            if term in text:
                errors.append(f"{relative}: banned UI term {term}")

    public_data = json.loads((ROOT/"public/data/fallback-products.json").read_text(encoding="utf-8"))
    if len(public_data) != len(products):
        errors.append("public data count does not match canonical data")
    if not products:
        errors.append("empty database")

    report = {
        "status": "failed" if errors else "passed",
        "rawRows": len(products),
        "uniqueProducts": len(ids),
        "included": sum(item.get("status") == "포함" for item in products),
        "pending": sum(item.get("status") == "보류" for item in products),
        "excluded": sum(item.get("status") == "제외" for item in products),
        "violations": errors,
    }
    (ROOT/"data/strict-validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    main()
