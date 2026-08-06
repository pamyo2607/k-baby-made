#!/usr/bin/env python3
"""Fail closed when requested valid research output is not produced."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/research-effectiveness-report.json"
report = json.loads(REPORT.read_text(encoding="utf-8"))
errors = list(report.get("errors", []))
if not report.get("scopeExecuted"):
    errors.append("requested research scope did not execute")
if not report.get("candidateTargetMet"):
    shortfall = report.get("candidateShortfallByCategory", {})
    errors.append(f"valid candidate target not met: {shortfall}")
if int(report.get("effectiveRevalidations", 0)) == 0:
    errors.append("no pending product was effectively revalidated")
report["passed"] = not errors
report["errors"] = errors
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
if errors:
    raise SystemExit("; ".join(errors))
