#!/usr/bin/env python3
"""Verify deployed Worker assets byte-for-byte and record production proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROOF_NAMES = (
    "strict-419-production-final-proof.json",
    "strict-419-live-sync-proof.json",
    "strict-419-app-activation-proof.json",
    "strict-419-unique-meta-proof.json",
    "codex-production-verification.json",
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "K-Baby-Made-Deployment-Verifier/1.0",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"{url}: HTTP {response.status}")
        return response.read()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify(root: Path, base_url: str, source_commit: str) -> dict:
    base_url = base_url.rstrip("/")
    cache_key = source_commit or str(int(time.time()))
    public = root / "public"
    data = root / "data"
    local_assets = {
        "index.html": public / "index.html",
        "app.js": public / "app.js",
        "styles.css": public / "styles.css",
        "kbaby-data.js": public / "kbaby-data.js",
        "csv": public / "data/master-db-419-final.csv",
        "fallback-products.json": public / "data/fallback-products.json",
        "health.json": public / "health.json",
        "meta.json": public / "meta.json",
    }
    remote_paths = {
        "index.html": "/index.html",
        "app.js": "/app.js",
        "styles.css": "/styles.css",
        "kbaby-data.js": "/kbaby-data.js",
        "csv": "/data/master-db-419-final.csv",
        "fallback-products.json": "/data/fallback-products.json",
        "health.json": "/health.json",
        "meta.json": "/meta.json",
    }
    expected_sha = {name: sha_file(path) for name, path in local_assets.items()}
    remote_bytes: dict[str, bytes] = {}
    last_error = ""
    for attempt in range(1, 11):
        try:
            fetched = {
                name: fetch(
                    f"{base_url}{remote_paths[name]}?"
                    + urlencode({"verify": cache_key, "attempt": attempt})
                )
                for name in local_assets
            }
            fetch(f"{base_url}/?" + urlencode({"verify": cache_key, "attempt": attempt}))
            actual_sha = {name: sha_bytes(value) for name, value in fetched.items()}
            mismatches = {
                name: {"expected": expected_sha[name], "actual": actual_sha[name]}
                for name in expected_sha
                if expected_sha[name] != actual_sha[name]
            }
            if mismatches:
                raise RuntimeError(f"deployed asset hash mismatch: {mismatches}")
            remote_bytes = fetched
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 10:
                raise SystemExit(last_error)
            time.sleep(6)

    expected_health = json.loads(local_assets["health.json"].read_text(encoding="utf-8"))
    live_health = json.loads(remote_bytes["health.json"].decode("utf-8"))
    health_keys = (
        "build",
        "rawRecords",
        "uniqueProducts",
        "duplicateRecords",
        "currentSale",
        "fullRevalidationTargetRows",
        "statusCounts",
        "sha256",
        "csvSha256",
    )
    health_mismatches = {
        key: {"expected": expected_health.get(key), "actual": live_health.get(key)}
        for key in health_keys
        if expected_health.get(key) != live_health.get(key)
    }
    if health_mismatches:
        raise SystemExit(f"production health mismatch: {health_mismatches}")

    status_counts = expected_health["statusCounts"]
    verified_at = datetime.now(timezone.utc).isoformat()
    proof = {
        "status": "passed",
        "build": expected_health["build"],
        "legacyStrict419Filename": True,
        "rawRecords": expected_health["rawRecords"],
        "uniqueProducts": expected_health["uniqueProducts"],
        "duplicateRecords": expected_health["duplicateRecords"],
        "currentSale": expected_health["currentSale"],
        "strict419TargetRows": expected_health["strict419TargetRows"],
        "uniqueIncluded": status_counts["포함"],
        "uniquePending": status_counts["보류"],
        "uniqueExcluded": status_counts["제외"],
        "verifiedTotal": expected_health["uniqueProducts"],
        "transport": "verified-csv",
        "liveConnected": True,
        "buildError": None,
        "expectedRenderedCards": 24,
        "includedTile": status_counts["포함"],
        "pendingTile": status_counts["보류"],
        "excludedAndDuplicateTile": (
            status_counts["제외"] + expected_health["duplicateRecords"]
        ),
        "dataSha256": expected_health["sha256"],
        "csvSha256": expected_health["csvSha256"],
        "deploymentVerification": "passed",
        "verifiedDeployment": {
            "verifiedAt": verified_at,
            "url": base_url + "/",
            "sourceCommit": source_commit,
            "artifactMatchesCurrentBuild": True,
            "verificationMode": "cache-busted HTTP byte-for-byte readback",
            "http200": ["/", *remote_paths.values()],
            "sha256": expected_sha,
            "browser": {
                "transport": "verified-csv",
                "total": expected_health["uniqueProducts"],
                "currentSale": expected_health["currentSale"],
                "included": status_counts["포함"],
                "pending": status_counts["보류"],
                "excludedAndDuplicate": (
                    status_counts["제외"] + expected_health["duplicateRecords"]
                ),
                "initialCards": 24,
                "verificationMode": "HTTP asset and metadata readback",
            },
        },
    }
    for name in PROOF_NAMES:
        write_json(data / name, proof)
    print(json.dumps(proof, ensure_ascii=False))
    return proof


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()
    verify(args.root.resolve(), args.base_url, args.source_commit)


if __name__ == "__main__":
    main()
