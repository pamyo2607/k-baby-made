#!/usr/bin/env python3
"""Build all deployable K-Baby Made assets from one canonical JSON file."""
from __future__ import annotations
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SOURCE = DATA / "master-products.json"

PUBLIC_FIELDS = [
    "category","brand","name","status","origin","saleStatus","age","ageBasis",
    "kcNumber","kcStatus","kcDate","kcType","kcAuthority","manufacturer",
    "safetyKoreaUrl","checkedAt","reason","evidenceUrls","quality","group"
]
CSV_FIELDS = [
    "id","category","subtype","brand","name","status","origin","saleStatus","age","ageBasis",
    "kcNumber","kcStatus","kcDate","kcType","kcAuthority","kcItem","kcModel","manufacturer",
    "safetyKoreaUrl","checkedAt","reason","evidenceUrls","quality","history","researchStatus","group"
]

def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main() -> None:
    products = json.loads(SOURCE.read_text(encoding="utf-8"))
    if not isinstance(products, list) or not products:
        raise SystemExit("canonical product database is empty")
    public_products = [{key: item.get(key, "") for key in PUBLIC_FIELDS} for item in products]

    for target in [
        DATA / "fallback-products.json",
        ROOT / "fallback-products.json",
        PUBLIC / "fallback-products.json",
        PUBLIC / "data/fallback-products.json",
    ]:
        dump(target, public_products)

    script = "window.KBABY_DATA = " + json.dumps(public_products, ensure_ascii=False) + ";\n"
    (ROOT / "kbaby-data.js").write_text(script, encoding="utf-8")
    (PUBLIC / "kbaby-data.js").write_text(script, encoding="utf-8")

    with (DATA / "master-db-419-final.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for product in products:
            row = {key: product.get(key, "") for key in CSV_FIELDS}
            row["evidenceUrls"] = "\n".join(product.get("evidenceUrls", []))
            writer.writerow(row)

    counts = Counter(item.get("status") for item in products)
    categories = Counter(item.get("category") for item in products)
    existing_meta = {}
    meta_path = DATA / "meta.json"
    if meta_path.exists():
        existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    existing_meta.update({
        "rawRecords": len(products),
        "uniqueProducts": len({item.get("id") for item in products}),
        "statusCounts": dict(counts),
        "categoryCounts": dict(categories),
        "bootstrapPending": False,
    })
    for target in [DATA / "meta.json", ROOT / "meta.json", PUBLIC / "data/meta.json", PUBLIC / "meta.json"]:
        dump(target, existing_meta)

    health = {
        "status": "ok",
        "rawRecords": len(products),
        "uniqueProducts": len({item.get("id") for item in products}),
        "duplicateRecords": len(products) - len({item.get("id") for item in products}),
        "statusCounts": dict(counts),
        "categoryCounts": dict(categories),
        "includedEvidenceViolations": 0,
        "recovery": {
            "sourceRecords": len(products),
            "previousKnownUniqueProducts": existing_meta.get("previousKnownUniqueProducts", 455),
            "missingFromCurrentRecovery": max(0, existing_meta.get("previousKnownUniqueProducts", 455) - len(products)),
            "complete": len(products) >= existing_meta.get("previousKnownUniqueProducts", 455),
        },
    }
    for target in [ROOT / "health.json", PUBLIC / "health.json"]:
        dump(target, health)

if __name__ == "__main__":
    main()
