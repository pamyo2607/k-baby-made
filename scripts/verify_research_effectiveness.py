#!/usr/bin/env python3
"""Reject technically green runs that performed no effective research."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
audit = json.loads((root / "data/research-last-run.json").read_text(encoding="utf-8"))
state = audit.get("state", {})
errors = []
if state.get("pendingSelected") != 50:
    errors.append(f"pendingSelected={state.get('pendingSelected')} expected=50")
if len(state.get("currentCategories", [])) != 6:
    errors.append("all six categories were not attempted")
effective = int(state.get("effectiveRevalidations", 0))
new_candidates = int(state.get("newCandidates", 0))
if effective == 0 and new_candidates == 0:
    errors.append("zero effective revalidations and zero new candidates")
report = {
    "passed": not errors,
    "effectiveRevalidations": effective,
    "newCandidates": new_candidates,
    "errors": errors,
}
(root / "data/research-effectiveness-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False))
if errors:
    raise SystemExit("; ".join(errors))
