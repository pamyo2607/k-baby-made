#!/usr/bin/env python3
"""Fail closed until cumulative valid candidates and all 50 rechecks are done."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/research-effectiveness-report.json"
SOURCE = ROOT / "data/master-products.json"
CATEGORIES = ["완구", "구강치발기", "턱받이", "수유용품", "이유식용품", "위생용품"]
TARGET = 20

report = json.loads(REPORT.read_text(encoding="utf-8"))
products = json.loads(SOURCE.read_text(encoding="utf-8"))
counts = Counter(
    str(item.get("category", ""))
    for item in products
    if str(item.get("id", "")).startswith("DISC-") and item.get("status") == "보류"
)
shortfall = {category: max(0, TARGET - counts.get(category, 0)) for category in CATEGORIES}
errors = list(report.get("errors", []))
if not report.get("scopeExecuted"):
    errors.append("requested research scope did not execute")
if int(report.get("effectiveRevalidations", 0)) != 50:
    errors.append(
        f"effectiveRevalidations={report.get('effectiveRevalidations')} expected=50"
    )
if any(shortfall.values()):
    errors.append(f"cumulative valid candidate target not met: {shortfall}")

report["cumulativeValidCandidatesByCategory"] = {
    category: counts.get(category, 0) for category in CATEGORIES
}
report["candidateTargetPerCategory"] = TARGET
report["candidateTargetMet"] = not any(shortfall.values())
report["candidateShortfallByCategory"] = shortfall
report["passed"] = not errors
report["errors"] = errors
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
if errors:
    raise SystemExit("; ".join(errors))
