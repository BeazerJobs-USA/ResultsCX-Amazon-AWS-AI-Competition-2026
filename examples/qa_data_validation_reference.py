"""Offline validation for an explicitly synthetic QA record.

An allowlist helps reject unexpected fields; it does not constitute an
automated PII detector, a full de-identification tool, or HIPAA compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


ALLOWED_KEYS = frozenset({"case_id", "focus_area", "defect_rate_pct"})


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    focus_area: str
    defect_rate_pct: float


def validate_synthetic_case(record: Mapping[str, Any]) -> SyntheticCase:
    """Reject unexpected fields and invalid synthetic QA measurements."""
    extra = set(record) - ALLOWED_KEYS
    missing = ALLOWED_KEYS - set(record)
    if extra:
        raise ValueError("Unexpected case fields; review input schema")
    if missing:
        raise ValueError("Missing required case fields")

    case_id = record["case_id"]
    focus = record["focus_area"]
    rate = record["defect_rate_pct"]

    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id must be a nonempty synthetic label")
    if not isinstance(focus, str) or not focus.strip():
        raise ValueError("focus_area must be a nonempty synthetic label")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError("defect_rate_pct must be numeric")
    if not 0 <= rate <= 100:
        raise ValueError("defect_rate_pct must be between 0 and 100")

    return SyntheticCase(case_id.strip(), focus.strip(), float(rate))

