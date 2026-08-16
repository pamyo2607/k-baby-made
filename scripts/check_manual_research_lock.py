#!/usr/bin/env python3
"""Fail-closed gate for the shared manual research campaign lock.

The mere presence of a valid lock blocks automated research, even after its TTL.
Expired locks require human review and explicit removal. A malformed lock is a
hard error, so automation can never interpret damaged coordination state as an
open gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "data/manual-research-lock.json"
REQUIRED_FIELDS = ("campaignId", "owner", "baseSha", "createdAt", "expiresAt")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class LockValidationError(ValueError):
    """Raised when the lock exists but cannot be trusted."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK,
        help="Lock file to inspect (default: data/manual-research-lock.json)",
    )
    return parser.parse_args()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LockValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise LockValidationError(f"{field} must be an RFC 3339 timestamp with timezone")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LockValidationError(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LockValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_and_validate(lock_file: Path) -> tuple[dict[str, Any], datetime, datetime]:
    try:
        raw = lock_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LockValidationError(f"lock file is unreadable: {exc}") from exc

    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except LockValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise LockValidationError(
            f"lock file is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc

    if not isinstance(payload, dict):
        raise LockValidationError("lock JSON root must be an object")

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise LockValidationError(f"missing required fields: {', '.join(missing)}")

    campaign_id = payload["campaignId"]
    if not isinstance(campaign_id, str) or not CAMPAIGN_RE.fullmatch(campaign_id):
        raise LockValidationError(
            "campaignId must be 1-200 safe identifier characters"
        )

    owner = payload["owner"]
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or len(owner) > 200
        or any(ord(character) < 32 for character in owner)
    ):
        raise LockValidationError("owner must be a non-empty single-line string")

    base_sha = payload["baseSha"]
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        raise LockValidationError("baseSha must be a 40-character Git commit SHA")

    created_at = _parse_timestamp(payload["createdAt"], "createdAt")
    expires_at = _parse_timestamp(payload["expiresAt"], "expiresAt")
    if expires_at <= created_at:
        raise LockValidationError("expiresAt must be later than createdAt")

    return payload, created_at, expires_at


def _append_env_file(variable: str, content: str) -> None:
    destination = os.environ.get(variable, "").strip()
    if not destination:
        return
    with Path(destination).open("a", encoding="utf-8") as handle:
        handle.write(content)


def _set_outputs(*, locked: bool, state: str) -> None:
    _append_env_file(
        "GITHUB_OUTPUT",
        f"locked={'true' if locked else 'false'}\nlock_state={state}\n",
    )


def _summary(lines: list[str]) -> None:
    text = "\n".join(["### Manual research lock gate", "", *lines, ""])
    _append_env_file("GITHUB_STEP_SUMMARY", text)


def _safe_markdown(value: Any) -> str:
    return (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
    )


def main() -> int:
    lock_file = parse_args().lock_file

    if not os.path.lexists(lock_file):
        _set_outputs(locked=False, state="absent")
        _summary(
            [
                "- Result: **OPEN**",
                "- `data/manual-research-lock.json` is absent.",
                "- Automated research may proceed.",
            ]
        )
        print("Manual research lock gate OPEN: lock file is absent.")
        return 0

    try:
        payload, _created_at, expires_at = load_and_validate(lock_file)
    except LockValidationError as exc:
        _set_outputs(locked=True, state="malformed")
        _summary(
            [
                "- Result: **BLOCKED — MALFORMED LOCK**",
                f"- Validation error: `{_safe_markdown(exc)}`",
                "- Automated mutation is disabled. The lock was not changed or removed.",
            ]
        )
        print(f"Manual research lock gate BLOCKED: malformed lock: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    expired = expires_at <= now
    state = "expired" if expired else "active"
    _set_outputs(locked=True, state=state)
    expiry_note = (
        "TTL has expired, but the lock remains blocking until a human verifies "
        "remote state and explicitly removes it."
        if expired
        else "The manual campaign lock is active."
    )
    _summary(
        [
            f"- Result: **BLOCKED — {state.upper()} LOCK**",
            f"- Campaign: `{_safe_markdown(payload['campaignId'])}`",
            f"- Owner: `{_safe_markdown(payload['owner'])}`",
            f"- Base SHA: `{payload['baseSha']}`",
            f"- Created: `{payload['createdAt']}`",
            f"- Expires: `{payload['expiresAt']}`",
            f"- {expiry_note}",
            "- All automated setup, research, build, and commit steps are skipped.",
        ]
    )
    print(
        f"Manual research lock gate BLOCKED ({state}): "
        f"campaign={payload['campaignId']} owner={payload['owner']} "
        f"expiresAt={payload['expiresAt']}"
    )
    if expired:
        print("Expired locks are never cleared automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
