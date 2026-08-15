#!/usr/bin/env python3
"""Fail closed when a single research run exceeds its bounded scope."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/research-effectiveness-report.json"
MAX_PENDING = 50
MAX_NEW_CANDIDATES = 30
MAX_NEW_PER_CATEGORY = 5


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    errors = [str(value) for value in report.get("errors", [])]
    pending = int(report.get("pendingSelectedThisRun", -1))
    new_candidates = int(report.get("newCandidatesThisRun", -1))
    by_category = {
        str(key): int(value)
        for key, value in dict(report.get("newCandidatesByCategoryThisRun", {})).items()
    }

    if not report.get("scopeExecuted"):
        errors.append("research scope or metric verification failed")
    if not 0 <= pending <= MAX_PENDING:
        errors.append(f"pendingSelectedThisRun={pending} exceeds {MAX_PENDING}")
    if not 0 <= new_candidates <= MAX_NEW_CANDIDATES:
        errors.append(
            f"newCandidatesThisRun={new_candidates} exceeds {MAX_NEW_CANDIDATES}"
        )
    if any(value > MAX_NEW_PER_CATEGORY for value in by_category.values()):
        errors.append(
            f"new candidate category cap exceeded: {by_category} > {MAX_NEW_PER_CATEGORY}"
        )

    errors = list(dict.fromkeys(errors))
    report["perRunLimits"] = {
        "pendingProducts": MAX_PENDING,
        "newExactCandidates": MAX_NEW_CANDIDATES,
        "newExactCandidatesPerCategory": MAX_NEW_PER_CATEGORY,
    }
    report["limitsEnforced"] = not errors
    report["passed"] = not errors
    report["errors"] = errors
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()
