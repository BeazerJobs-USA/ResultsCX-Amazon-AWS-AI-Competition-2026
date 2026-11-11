"""Simulated administrative referral records.

Agent QA Review exists to turn QA findings into coaching. Serious or repeated
failures can still need action outside coaching, so a supervisor may write a
referral for an authorized HR reviewer.

Limits that are enforced here, not just documented:

* A referral is always written by a person. Nothing in the AI workflow proposes,
  ranks, or pre-selects an administrative action.
* No HR system is connected. "Submitted" means the record is marked ready for
  review inside this session. Nothing is transmitted.
* Referrals are never written to disk by the application, so no permanent
  employment record is created.
* The application cannot record an HR outcome. That decision belongs to HR.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from typing import Any

from workflow.common import WorkflowError, allowed_roles, can, is_meaningful_note, now_iso

SIMULATION_NOTICE = (
    "Simulated referral workflow using synthetic employee identifiers. No HR system is "
    "connected, nothing is transmitted, and no employment record is created."
)
ATTESTATION_TEXT = (
    "I wrote this referral myself, I have reviewed the cited QA case, and I understand that "
    "any administrative action is decided by authorized HR reviewers, not by this application."
)

HR_PARTNER = "HR business partner"
DEPARTMENT_MANAGER = "Department manager"
SENIOR_LEADER = "Senior operations leader"
LEGAL_COMPLIANCE = "Legal or compliance"
APPROVERS: tuple[str, ...] = (HR_PARTNER, DEPARTMENT_MANAGER, SENIOR_LEADER, LEGAL_COMPLIANCE)


@dataclass(frozen=True)
class ReferralAction:
    key: str
    label: str
    minimum_approvals: tuple[str, ...]
    description: str


ACTIONS: dict[str, ReferralAction] = {
    a.key: a
    for a in (
        ReferralAction(
            "verbal_counseling",
            "Verbal counseling",
            (),
            "A documented conversation between supervisor and employee.",
        ),
        ReferralAction(
            "written_warning",
            "Written warning recommendation",
            (HR_PARTNER,),
            "A recommendation only. HR decides whether a warning is issued.",
        ),
        ReferralAction(
            "performance_improvement_plan",
            "Performance improvement plan recommendation",
            (HR_PARTNER, DEPARTMENT_MANAGER),
            "A recommendation only. HR and the department manager decide.",
        ),
        ReferralAction(
            "suspension_pending_review",
            "Suspension pending authorized review",
            (HR_PARTNER, DEPARTMENT_MANAGER, LEGAL_COMPLIANCE),
            "A request for urgent authorized review. The supervisor cannot suspend anyone here.",
        ),
        ReferralAction(
            "termination_referral",
            "Termination referral for authorized HR review",
            (HR_PARTNER, DEPARTMENT_MANAGER, LEGAL_COMPLIANCE),
            "A referral for HR review only. No employment decision is made here.",
        ),
    )
}

DRAFT, SUBMITTED, WITHDRAWN = "draft", "submitted", "withdrawn"
STATUS_LABELS: dict[str, str] = {
    DRAFT: "Draft - not submitted",
    SUBMITTED: "Marked for authorized HR review (simulated, not transmitted)",
    WITHDRAWN: "Withdrawn by supervisor",
}
_TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({SUBMITTED, WITHDRAWN}),
    SUBMITTED: frozenset({WITHDRAWN}),
    WITHDRAWN: frozenset(),
}
JUSTIFICATION_HINT = "Write at least eight words (40 characters) describing the facts and the reason."


def _clean(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]


def validate(
    form: dict[str, Any],
    *,
    actor: str,
    role: str,
    employees: list[str],
    case_ids: list[str],
    submitting: bool,
    today: date | None = None,
) -> list[str]:
    """Problems that block saving this referral. Empty means it can be saved."""
    problems = []
    if not can(role, "create_referral"):
        problems.append(
            f"Only the {allowed_roles('create_referral')} role can write a referral "
            f"(current role: {role or 'not set'})."
        )
    if not str(actor or "").strip():
        problems.append("Enter the supervisor's name in the sidebar.")
    if form.get("employee") not in employees:
        problems.append("Choose an employee identifier from the loaded QA data.")
    if form.get("case_id") not in case_ids:
        problems.append("Choose the QA case this referral is based on.")
    action = ACTIONS.get(str(form.get("action")))
    if action is None:
        problems.append("Choose the proposed action.")
    if not is_meaningful_note(form.get("justification"), min_words=8, min_chars=40):
        problems.append(f"A supervisor justification is required. {JUSTIFICATION_HINT}")
    try:
        reviewed = date.fromisoformat(str(form.get("review_date")))
    except ValueError:
        problems.append("Enter the date of the supervisor's review (YYYY-MM-DD).")
    else:
        if reviewed > (today or date.today()):
            problems.append("The date of review cannot be in the future.")
    approvals = list(form.get("approvals") or [])
    if any(a not in APPROVERS for a in approvals):
        problems.append("Required approvals must come from the listed approver roles.")
    if action:
        missing = [a for a in action.minimum_approvals if a not in approvals]
        if missing:
            problems.append(f"'{action.label}' requires approval from: {', '.join(missing)}.")
    if form.get("witness_acknowledged") and not _clean(form.get("witness_name")):
        problems.append("Name the witness whose acknowledgment is being recorded.")
    if submitting and not form.get("attested"):
        problems.append("Confirm the statement before marking the referral for HR review.")
    return problems


def create(
    existing: list[dict[str, Any]],
    form: dict[str, Any],
    *,
    actor: str,
    role: str,
    employees: list[str],
    case_ids: list[str],
    submitting: bool,
    today: date | None = None,
) -> dict[str, Any]:
    """Build a referral record. Raises :class:`WorkflowError` when the form is invalid."""
    problems = validate(
        form,
        actor=actor,
        role=role,
        employees=employees,
        case_ids=case_ids,
        submitting=submitting,
        today=today,
    )
    if problems:
        raise WorkflowError(" ".join(problems))
    action = ACTIONS[form["action"]]
    status = SUBMITTED if submitting else DRAFT
    created_at = now_iso()
    return {
        "id": f"REF-{len(existing) + 1:04d}",
        "simulated": True,
        "employee": form["employee"],
        "case_id": form["case_id"],
        "action": action.key,
        "action_label": action.label,
        "justification": _clean(form.get("justification")),
        "review_date": date.fromisoformat(str(form["review_date"])).isoformat(),
        "approvals_required": [a for a in APPROVERS if a in (form.get("approvals") or [])],
        "witness_name": _clean(form.get("witness_name"), 120),
        "witness_acknowledged": bool(form.get("witness_acknowledged")),
        "status": status,
        "created_by": actor.strip(),
        "created_at": created_at,
        "history": [{"at": created_at, "actor": actor.strip(), "role": role, "status": status, "note": ""}],
    }


def change_status(
    referral: dict[str, Any],
    new_status: str,
    *,
    actor: str,
    role: str,
    note: str = "",
    attested: bool = False,
) -> dict[str, Any]:
    """Return an updated copy after a permitted status change."""
    if not can(role, "create_referral"):
        raise WorkflowError(f"Only the {allowed_roles('create_referral')} role can change a referral.")
    current = referral.get("status", DRAFT)
    if new_status not in _TRANSITIONS.get(current, frozenset()):
        raise WorkflowError(
            f"A referral that is '{STATUS_LABELS.get(current, current)}' cannot become "
            f"'{STATUS_LABELS.get(new_status, new_status)}'."
        )
    if new_status == SUBMITTED and not attested:
        raise WorkflowError("Confirm the statement before marking the referral for HR review.")
    if new_status == WITHDRAWN and not is_meaningful_note(note):
        raise WorkflowError("Explain why the referral is being withdrawn (at least four words).")
    updated = copy.deepcopy(referral)
    updated["status"] = new_status
    updated["history"].append(
        {"at": now_iso(), "actor": str(actor or "").strip(), "role": role, "status": new_status, "note": _clean(note)}
    )
    return updated


def summarize(referrals: list[dict[str, Any]]) -> dict[str, int]:
    """Counts by status for the dashboard."""
    counts = {status: 0 for status in STATUS_LABELS}
    for referral in referrals:
        counts[referral.get("status", DRAFT)] = counts.get(referral.get("status", DRAFT), 0) + 1
    return counts
