"""Supervisor decisions on AI-proposed findings.

The AI drafts a finding; a supervisor decides what happens to it. Every
recorded decision keeps the AI's original values next to the supervisor's final
values, so a later reader can see exactly what the model proposed and what a
person changed.

A note is mandatory whenever the supervisor rejects, overturns, or materially
changes what the AI proposed. :func:`note_reasons` returns why, so the
interface can explain the requirement before the supervisor submits.
"""

from __future__ import annotations

from typing import Any

import pipeline
from workflow.common import NOTE_HINT, WorkflowError, allowed_roles, can, is_meaningful_note, now_iso

DECISION_OPTIONS: dict[str, str] = {
    "approve": "Approve the diagnosis",
    "reject": "Reject the diagnosis",
    "request_evidence": "Request additional evidence",
}
CONTROL_OPTIONS: dict[str, str] = {
    "undetermined": "Not determined yet",
    "confirmed": "Yes - the QA control was missed",
    "not_supported": "No - the evidence does not support the flag",
}
NEXT_ACTIONS: dict[str, str] = {
    "coaching": "Targeted coaching",
    "remediation": "Remediation plan",
    "follow_up": "Follow-up evaluation only",
    "escalate_operations": "Escalate to operations (process issue)",
    "close": "Close the case",
}
PENDING_EVIDENCE = "pending_evidence"
OUTCOME_LABELS: dict[str, str] = {
    "approved": "Approved as proposed",
    "approved_with_corrections": "Approved with supervisor corrections",
    "rejected": "Rejected by supervisor",
    "evidence_requested": "Additional evidence requested",
}
# Fields of an AI finding the supervisor may correct.
EDITABLE_FIELDS: tuple[str, ...] = (
    "title",
    "root_cause_type",
    "root_cause",
    "recommended_intervention",
    "curriculum_coverage",
)
FIELD_LABELS: dict[str, str] = {
    "title": "Finding title",
    "root_cause_type": "Root cause type",
    "root_cause": "Root cause explanation",
    "recommended_intervention": "Recommended intervention",
    "curriculum_coverage": "Curriculum coverage",
}
_SNAPSHOT_FIELDS = EDITABLE_FIELDS + (
    "observed",
    "training_recommended",
    "confidence",
    "confidence_note",
    "curriculum_note",
    "corrective_actions",
)
_COACHING_ACTIONS = frozenset({"coaching", "remediation"})


def ai_snapshot(finding: dict[str, Any]) -> dict[str, Any]:
    """The AI's proposal exactly as received, kept with every decision."""
    return {field: finding.get(field) for field in _SNAPSHOT_FIELDS}


def suggested_next_action(finding: dict[str, Any]) -> str:
    """Translate the AI's recommendation into the supervisor's next-action vocabulary."""
    wants_training = bool(finding.get("training_recommended"))
    if not wants_training or finding.get("recommended_intervention") == "escalate to operations":
        return "escalate_operations"
    return "coaching"


def new_draft(finding: dict[str, Any]) -> dict[str, Any]:
    """Starting values for the review form: nothing decided, the AI's text unchanged."""
    return {
        "decision": "",
        "control": "undetermined",
        "privacy_referral": False,
        "attested": False,
        "next_action": suggested_next_action(finding),
        "note": "",
        **{field: finding.get(field, "") for field in EDITABLE_FIELDS},
    }


_DECISION_OF_OUTCOME = {
    "approved": "approve",
    "approved_with_corrections": "approve",
    "rejected": "reject",
    "evidence_requested": "request_evidence",
}
SMALL_SAMPLE = 30


def draft_from_record(finding: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Starting values for revising a recorded decision. The note always starts empty."""
    draft = new_draft(finding)
    draft.update({field: record["final"].get(field, draft[field]) for field in EDITABLE_FIELDS})
    draft["decision"] = _DECISION_OF_OUTCOME.get(record.get("outcome", ""), "")
    draft["control"] = record.get("control", "undetermined")
    if record.get("next_action") in NEXT_ACTIONS:
        draft["next_action"] = record["next_action"]
    return draft


def evidence_limitations(finding: dict[str, Any]) -> list[str]:
    """Reasons to be cautious about a finding, stated plainly for the supervisor."""
    notes = []
    confidence = finding.get("confidence", "medium")
    note = str(finding.get("confidence_note") or "").strip()
    notes.append(f"Stated confidence: {confidence}." + (f" {note}" if note else ""))
    total, verified = finding.get("evidence_total", 0), finding.get("evidence_verified", 0)
    if total == 0:
        notes.append("No evaluator comments were cited for this finding.")
    elif verified < total:
        notes.append(
            f"{total - verified} of {total} cited comments could not be matched to a record for this "
            "requirement and need to be checked by hand."
        )
    if not finding.get("metrics_verified"):
        notes.append(
            "The finding does not match a QA requirement in the loaded data, so no computed figures apply."
        )
    else:
        evaluated = finding.get("evaluated", 0)
        if evaluated < SMALL_SAMPLE:
            notes.append(
                f"Based on {evaluated} scored items from {len(finding.get('agents_failed', []))} "
                "employee(s) with a miss. That is too few to establish a pattern."
            )
    notes.append(
        "The root cause is an inference from evaluator comments. It has not been confirmed with the employees."
    )
    return notes


def _same(left: Any, right: Any) -> bool:
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def changes(finding: dict[str, Any], draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields where the supervisor's value differs from the AI's."""
    return [
        {
            "field": field,
            "label": FIELD_LABELS[field],
            "ai_value": finding.get(field, ""),
            "supervisor_value": draft.get(field, ""),
        }
        for field in EDITABLE_FIELDS
        if not _same(finding.get(field), draft.get(field))
    ]


def _overturns_recommendation(finding: dict[str, Any], next_action: str) -> bool:
    suggested = suggested_next_action(finding)
    if suggested == "coaching":
        return next_action not in _COACHING_ACTIONS
    return next_action in _COACHING_ACTIONS


def note_reasons(finding: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    """Why a supervisor note is mandatory for this draft. Empty when it is optional."""
    reasons = []
    decision = draft.get("decision")
    if decision == "reject":
        reasons.append("the AI's diagnosis is being rejected")
    if decision == "request_evidence":
        reasons.append("the evidence still needed must be described")
    changed = changes(finding, draft)
    if decision == "approve" and changed:
        reasons.append("the AI's finding was changed (" + ", ".join(c["label"] for c in changed) + ")")
    if draft.get("control") == "not_supported":
        reasons.append("a QA flag is being dismissed")
    if draft.get("privacy_referral"):
        reasons.append("a suspected privacy incident is being referred")
    if decision == "approve" and _overturns_recommendation(finding, draft.get("next_action", "")):
        reasons.append("the next action differs from the AI's recommendation")
    return reasons


def validate(finding: dict[str, Any], draft: dict[str, Any], *, actor: str, role: str) -> list[str]:
    """Problems that block recording this decision. Empty means it can be recorded."""
    problems = []
    if not can(role, "decide_finding"):
        problems.append(
            f"Only the {allowed_roles('decide_finding')} role can record a decision "
            f"(current role: {role or 'not set'})."
        )
    if not str(actor or "").strip():
        problems.append("Enter the reviewer's name in the sidebar.")
    decision = draft.get("decision")
    if decision not in DECISION_OPTIONS:
        problems.append("Choose a decision: approve, reject, or request additional evidence.")
        return problems
    control = draft.get("control")
    if control not in CONTROL_OPTIONS:
        problems.append("State whether the QA control was missed.")
    next_action = draft.get("next_action")

    if decision == "approve":
        if control != "confirmed":
            problems.append(
                "Approving a diagnosis accepts that the QA control was missed. Confirm the "
                "control was missed, or reject the diagnosis or request evidence instead."
            )
        if next_action not in NEXT_ACTIONS:
            problems.append("Choose the next action for this case.")
        if not str(draft.get("title") or "").strip():
            problems.append("The finding title cannot be empty.")
        if not str(draft.get("root_cause") or "").strip():
            problems.append("The root cause explanation cannot be empty.")
        for field, allowed in (
            ("root_cause_type", pipeline.ROOT_CAUSE_TYPES),
            ("recommended_intervention", pipeline.INTERVENTIONS),
            ("curriculum_coverage", pipeline.COVERAGE),
        ):
            if draft.get(field) not in allowed:
                problems.append(f"{FIELD_LABELS[field]} must be one of: {', '.join(allowed)}.")
    elif decision == "reject":
        if next_action in _COACHING_ACTIONS:
            problems.append(
                "A rejected diagnosis cannot lead to coaching. Choose follow-up evaluation, "
                "escalation to operations, or case closure."
            )
        elif next_action not in NEXT_ACTIONS:
            problems.append("Choose the next action for this case.")

    if draft.get("privacy_referral"):
        if control == "not_supported":
            problems.append("A flag cannot be dismissed and referred as a privacy concern at once.")
        if not draft.get("attested"):
            problems.append("Confirm the authorization statement to refer a suspected privacy incident.")

    reasons = note_reasons(finding, draft)
    if reasons and not is_meaningful_note(draft.get("note")):
        problems.append(f"A supervisor note is required because {'; '.join(reasons)}. {NOTE_HINT}")
    return problems


def build_record(
    finding: dict[str, Any],
    draft: dict[str, Any],
    *,
    actor: str,
    role: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the decision record. Raises :class:`WorkflowError` when the draft is invalid."""
    problems = validate(finding, draft, actor=actor, role=role)
    if problems:
        raise WorkflowError(" ".join(problems))
    decision = draft["decision"]
    changed = changes(finding, draft) if decision == "approve" else []
    if decision == "approve":
        outcome = "approved_with_corrections" if changed else "approved"
        final = {field: draft.get(field) for field in EDITABLE_FIELDS}
        next_action = draft["next_action"]
    elif decision == "reject":
        outcome = "rejected"
        final = {field: finding.get(field) for field in EDITABLE_FIELDS}
        next_action = draft["next_action"]
    else:
        outcome = "evidence_requested"
        final = {field: finding.get(field) for field in EDITABLE_FIELDS}
        next_action = PENDING_EVIDENCE
    return {
        "finding_id": finding["id"],
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS[outcome],
        "control": draft.get("control", "undetermined"),
        "privacy_referral": bool(draft.get("privacy_referral")),
        "next_action": next_action,
        "note": " ".join(str(draft.get("note") or "").split()),
        "ai_original": ai_snapshot(finding),
        "ai_suggested_next_action": suggested_next_action(finding),
        "final": final,
        "changes": changed,
        "decided_by": actor.strip(),
        "role": role,
        "decided_at": now_iso(),
        "revision": int(previous.get("revision", 0)) + 1 if previous else 1,
    }


def is_final(record: dict[str, Any] | None) -> bool:
    """True once a finding is approved or rejected. Requested evidence keeps it open."""
    return bool(record) and record.get("outcome") in ("approved", "approved_with_corrections", "rejected")  # type: ignore[union-attr]


def coaching_ready(record: dict[str, Any] | None) -> bool:
    """True when an approved finding was sent to coaching or remediation."""
    return bool(
        record
        and record.get("outcome") in ("approved", "approved_with_corrections")
        and record.get("next_action") in _COACHING_ACTIONS
    )


def flag_actions_for(record: dict[str, Any]) -> list[str]:
    """Flag actions implied by a finding-level decision, applied to its related flags."""
    actions = []
    if record.get("control") == "confirmed":
        actions.append("confirm_qa_failure")
    elif record.get("control") == "not_supported":
        actions.append("dismiss")
    elif record.get("outcome") == "evidence_requested":
        actions.append("request_evidence")
    if record.get("privacy_referral"):
        actions.append("refer_privacy")
    return actions


def approved_findings(
    diagnosis: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    approval_mode: str = "human_review",
) -> list[dict[str, Any]]:
    """Findings cleared for coaching, with the supervisor's corrections applied."""
    edits = {}
    for finding in diagnosis.get("findings", []):
        record = decisions.get(finding["id"])
        ready = coaching_ready(record)
        edit: dict[str, Any] = {"include": ready}
        if ready and record:
            edit.update(record["final"])
            edit["supervisor_note"] = record.get("note", "")
        edits[finding["id"]] = edit
    return pipeline.build_approved_findings(diagnosis, edits, approval_mode=approval_mode)


def progress(diagnosis: dict[str, Any] | None, decisions: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Counts for the stage tracker and the dashboard."""
    findings = (diagnosis or {}).get("findings", [])
    records = [decisions.get(f["id"]) for f in findings]
    return {
        "findings": len(findings),
        "final": sum(1 for r in records if is_final(r)),
        "approved": sum(1 for r in records if r and r["outcome"].startswith("approved")),
        "rejected": sum(1 for r in records if r and r["outcome"] == "rejected"),
        "evidence_requested": sum(1 for r in records if r and r["outcome"] == "evidence_requested"),
        "open": sum(1 for r in records if not is_final(r)),
        "coaching_ready": sum(1 for r in records if coaching_ready(r)),
    }
