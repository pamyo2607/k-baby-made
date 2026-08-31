#!/usr/bin/env python3
"""Apply and verify the promotion-ready IDs from the latest top-30 audit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> None:
    state = json.loads((DATA / "new-top30-research-state.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((DATA / "campaign-new50.json").read_text(encoding="utf-8"))
    campaign = str(checkpoint.get("campaignId", ""))
    wave = int(state.get("waveIndex", 0) or 0)
    ready_ids = [str(value) for value in state.get("promotionReadyCandidateIds", [])]
    if not campaign or wave <= 0:
        raise SystemExit("latest top-30 state has no valid campaign/wave")
    if str(state.get("campaignId", "")) != campaign:
        raise SystemExit("latest top-30 state campaign differs from checkpoint")

    if ready_ids:
        command = [
            sys.executable,
            str(ROOT / "scripts/promote_verified_candidates.py"),
            "--root", str(ROOT),
            "--campaign-id", campaign,
            "--wave-index", str(wave),
            "--audit-ref", str(state.get("auditRef", "")),
            "--apply",
        ]
        for candidate_id in ready_ids:
            command.extend(["--candidate-id", candidate_id])
        subprocess.run(command, check=True)
    else:
        print(json.dumps({
            "status": "no-op",
            "campaignId": campaign,
            "waveIndex": wave,
            "reason": "no promotion-ready candidates in this research wave",
        }, ensure_ascii=False))

    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/verify_promoted_candidates.py"),
        "--root", str(ROOT),
        "--campaign-id", campaign,
    ], check=True)


if __name__ == "__main__":
    main()
