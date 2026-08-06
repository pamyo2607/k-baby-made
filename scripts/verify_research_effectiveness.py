#!/usr/bin/env python3
"""Verify that the requested research scope ran and report valid output honestly."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
audit = json.loads((root / "data/research-last-run.json").read_text(encoding="utf-8"))
state = audit.get("state", {})
errors = []
if state.get("pendingSelected") != 50:
    errors.append(f"pendingSelected={state.get('pendingSelected')} expected=50")
categories = list(state.get("currentCategories", []))
if len(categories) != 6:
    errors.append("all six categories were not attempted")

effective = int(state.get("effectiveRevalidations", 0))
valid_candidates = int(state.get("newCandidates", 0))
raw_candidates = int(state.get("rawNewCandidates", valid_candidates))
valid_by_category = {
    str(key): int(value)
    for key, value in dict(state.get("newCandidatesByCategory", {})).items()
}
shortfall = {
    category: max(0, 20 - valid_by_category.get(category, 0))
    for category in categories
}
report = {
    "passed": not errors,
    "scopeExecuted": not errors,
    "effectiveRevalidations": effective,
    "rawCandidatesFound": raw_candidates,
    "validCandidates": valid_candidates,
    "validCandidatesByCategory": valid_by_category,
    "candidateTargetPerCategory": 20,
    "candidateTargetMet": bool(categories) and all(value == 0 for value in shortfall.values()),
    "candidateShortfallByCategory": shortfall,
    "errors": errors,
}
(root / "data/research-effectiveness-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False))
if errors:
    raise SystemExit("; ".join(errors))
