"""Hash-chained audit trail for review decisions.

Each entry stores the SHA-256 of the entry before it, so editing, removing, or
reordering an earlier entry breaks every hash after it and :func:`verify`
reports where. This makes changes *evident*; it does not prevent them. The log
lives in the Streamlit session, so anyone who can alter that memory can rebuild
the chain. A real deployment needs an append-only store outside the app.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from workflow.common import now_iso

GENESIS_HASH = "0" * 64
_HASHED_FIELDS = (
    "seq",
    "timestamp",
    "actor",
    "role",
    "action",
    "subject_type",
    "subject_id",
    "details",
    "prev_hash",
)


def _digest(entry: dict[str, Any]) -> str:
    body = {field: entry.get(field) for field in _HASHED_FIELDS}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def append(
    log: list[dict[str, Any]],
    *,
    actor: str,
    role: str,
    action: str,
    subject_type: str,
    subject_id: str,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Add one entry to ``log`` and return it."""
    entry: dict[str, Any] = {
        "seq": len(log) + 1,
        "timestamp": timestamp or now_iso(),
        "actor": actor.strip() or "(name not given)",
        "role": role,
        "action": action,
        "subject_type": subject_type,
        "subject_id": subject_id,
        # Round-trip through JSON so the stored details are exactly what is hashed.
        "details": json.loads(json.dumps(details or {}, default=str)),
        "prev_hash": log[-1]["hash"] if log else GENESIS_HASH,
    }
    entry["hash"] = _digest(entry)
    log.append(entry)
    return entry


def verify(log: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute the chain. ``first_invalid`` is the 1-based position of the first bad entry."""
    previous = GENESIS_HASH
    for position, entry in enumerate(log, start=1):
        intact = (
            isinstance(entry, dict)
            and entry.get("seq") == position
            and entry.get("prev_hash") == previous
            and entry.get("hash") == _digest(entry)
        )
        if not intact:
            return {"ok": False, "checked": position - 1, "first_invalid": position}
        previous = entry["hash"]
    return {"ok": True, "checked": len(log), "first_invalid": None}


def entries_for(log: list[dict[str, Any]], subject_type: str, subject_id: str) -> list[dict[str, Any]]:
    """Entries about one finding, flag, package, or referral, oldest first."""
    return [
        entry
        for entry in log
        if entry.get("subject_type") == subject_type and entry.get("subject_id") == subject_id
    ]
