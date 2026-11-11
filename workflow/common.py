"""Roles, permissions, and note-quality rules shared by the workflow modules.

Roles here model who may take which action in the demonstration. They are a
workflow simulation chosen in the sidebar, not authentication: nothing in this
application verifies who the person at the keyboard is.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


class WorkflowError(ValueError):
    """A requested action is not permitted, or its required inputs are missing."""


QA_ANALYST = "QA analyst"
SUPERVISOR = "Supervisor"
TRAINER = "Trainer"
COMPLIANCE_REVIEWER = "Compliance reviewer"
ROLES: tuple[str, ...] = (SUPERVISOR, QA_ANALYST, TRAINER, COMPLIANCE_REVIEWER)

ROLE_SUMMARY: dict[str, str] = {
    SUPERVISOR: "Reviews AI findings, decides next actions, reviews flags, prepares coaching.",
    QA_ANALYST: "Loads and checks QA data, starts the analysis, requests more evidence.",
    TRAINER: "Prepares coaching packages and records practice results.",
    COMPLIANCE_REVIEWER: "Records the outcome of referred privacy concerns.",
}

# permission -> roles allowed to use it
PERMISSIONS: dict[str, frozenset[str]] = {
    "run_analysis": frozenset({QA_ANALYST, SUPERVISOR}),
    "request_evidence": frozenset({QA_ANALYST, SUPERVISOR, COMPLIANCE_REVIEWER}),
    "decide_finding": frozenset({SUPERVISOR}),
    "review_flag": frozenset({SUPERVISOR}),
    "refer_privacy": frozenset({SUPERVISOR}),
    "record_determination": frozenset({COMPLIANCE_REVIEWER}),
    "override_risk": frozenset({SUPERVISOR, COMPLIANCE_REVIEWER}),
    "assign_follow_up": frozenset({SUPERVISOR, COMPLIANCE_REVIEWER}),
    "generate_coaching": frozenset({SUPERVISOR, TRAINER}),
    "record_practice": frozenset({SUPERVISOR, TRAINER}),
    "create_referral": frozenset({SUPERVISOR}),
}


def can(role: str, permission: str) -> bool:
    """True when ``role`` holds ``permission``. Unknown permissions are denied."""
    return role in PERMISSIONS.get(permission, frozenset())


def allowed_roles(permission: str) -> str:
    """Human-readable list of the roles that hold ``permission``."""
    roles = sorted(PERMISSIONS.get(permission, frozenset()))
    return " or ".join(roles) if roles else "no role"


def require(role: str, permission: str) -> None:
    """Raise :class:`WorkflowError` unless ``role`` holds ``permission``."""
    if not can(role, permission):
        raise WorkflowError(
            f"This action is limited to the {allowed_roles(permission)} role. "
            f"The current role is {role or 'not set'}."
        )


_WORD = re.compile(r"[A-Za-z0-9']+")


def is_meaningful_note(text: object, min_words: int = 4, min_chars: int = 20) -> bool:
    """A note counts when it has enough distinct words to explain a decision.

    The check blocks empty text, single words, and keyboard filler such as
    "aaaa aaaa aaaa aaaa". It cannot judge whether the explanation is correct.
    """
    cleaned = " ".join(str(text or "").split())
    words = _WORD.findall(cleaned)
    if len(cleaned) < min_chars or len(words) < min_words:
        return False
    return len({w.lower() for w in words}) >= min(min_words, 3)


NOTE_HINT = "Write at least four words (20 characters) explaining the decision."


def now_iso() -> str:
    """Current UTC time, to the second, in ISO 8601 form."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
