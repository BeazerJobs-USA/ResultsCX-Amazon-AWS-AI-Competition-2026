"""Issue categories, risk classification, and the review-flag state machine.

Three ideas are kept apart on purpose:

* A **QA failure** is what the evaluator scored: a required behavior was not
  demonstrated on a call.
* A **suspected privacy incident** is a supervisor's referral of a QA failure
  for closer review. It is a question, not a conclusion.
* A **compliance finding** can only be recorded by the compliance-reviewer
  role after a referral.

Nothing in this module decides whether a law or regulation was broken. The risk
levels are a triage aid produced by fixed, readable rules; they are not HIPAA
determinations and a person can override them with a recorded reason.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from workflow.common import NOTE_HINT, WorkflowError, can, is_meaningful_note, now_iso, require

# --------------------------------------------------------------------------------------
# Risk levels
# --------------------------------------------------------------------------------------
LOW, MODERATE, HIGH, CRITICAL = "Low", "Moderate", "High", "Critical"
RISK_LEVELS: tuple[str, ...] = (LOW, MODERATE, HIGH, CRITICAL)
RISK_DEFINITIONS: dict[str, str] = {
    LOW: "Minor operational or documentation concern.",
    MODERATE: "QA failure requiring corrective attention.",
    HIGH: "Significant procedural or suspected privacy concern.",
    CRITICAL: "Potentially serious incident requiring urgent authorized review.",
}
RISK_DISCLAIMER = (
    "Risk levels are produced by fixed triage rules in this prototype. They are not legal "
    "or HIPAA determinations, and an authorized person makes any final decision."
)


def risk_rank(level: str) -> int:
    """1 (Low) to 4 (Critical); 0 for an unknown or missing level."""
    return RISK_LEVELS.index(level) + 1 if level in RISK_LEVELS else 0


def _raise_level(level: str, steps: int = 1) -> str:
    return RISK_LEVELS[min(len(RISK_LEVELS) - 1, RISK_LEVELS.index(level) + steps)]


# --------------------------------------------------------------------------------------
# Issue categories
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Category:
    key: str
    label: str
    base_risk: str
    privacy_related: bool
    flag_when_failed: bool  # failed items appear in Compliance Review even when not repeated
    implication: str


_PRIVACY_NOTE = (
    "A missed QA step is not by itself a confirmed privacy incident. Whether any information "
    "reached someone not entitled to it is for an authorized reviewer to assess."
)

CATEGORIES: dict[str, Category] = {
    c.key: c
    for c in (
        Category(
            "identity_verification",
            "Identity verification",
            HIGH,
            True,
            True,
            "If account details were discussed before the caller was verified, information may "
            "have been shared with someone not entitled to it. " + _PRIVACY_NOTE,
        ),
        Category(
            "sensitive_information",
            "Handling of sensitive member information",
            HIGH,
            True,
            True,
            "Sensitive member information may not have been handled as the QA requirement "
            "describes. " + _PRIVACY_NOTE,
        ),
        Category(
            "unauthorized_disclosure",
            "Unauthorized account disclosure concern",
            HIGH,
            True,
            True,
            "The requirement concerns who may receive account information. " + _PRIVACY_NOTE,
        ),
        Category(
            "required_disclosure",
            "Required customer disclosure",
            MODERATE,
            False,
            True,
            "A required statement may not have been given to the customer. Whether that has "
            "regulatory or contractual consequences is for the compliance owner to decide.",
        ),
        Category(
            "documentation",
            "Documentation",
            LOW,
            False,
            True,
            "Incomplete records can delay follow-up and make later review harder. The category "
            "itself does not indicate a privacy concern.",
        ),
        Category(
            "other_compliance",
            "Other organization-defined QA control",
            MODERATE,
            False,
            True,
            "A compliance-section control was scored as missed. Its significance depends on the "
            "organization's own definition of that control.",
        ),
        Category(
            "procedural",
            "Process adherence",
            MODERATE,
            False,
            False,
            "A process step was not followed. The category itself does not indicate a privacy "
            "concern.",
        ),
        Category(
            "service_quality",
            "Service quality",
            LOW,
            False,
            False,
            "A service-quality behavior was missed. The category itself does not indicate a "
            "privacy or compliance concern.",
        ),
    )
}

# Checked in order against the QA requirement text. The requirement, not the evaluator's
# free-text comment, defines which control was scored. Required-disclosure phrases come
# first so "recording disclosure" is not read as an unauthorized disclosure.
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "required_disclosure",
        re.compile(
            r"required disclosure|recording disclosure|call recording|recorded line|"
            r"disclosure statement|disclaimer|required statement",
            re.I,
        ),
    ),
    (
        "unauthorized_disclosure",
        re.compile(
            r"unauthori[sz]ed|authori[sz]ed representative|third[- ]party|"
            r"release of information|\bdisclos",
            re.I,
        ),
    ),
    ("identity_verification", re.compile(r"identity|verif|authenticat|\bhipaa\b", re.I)),
    (
        "sensitive_information",
        re.compile(
            r"sensitive|protected health|\bphi\b|confidential|privacy|minimum necessary|safeguard",
            re.I,
        ),
    ),
    ("documentation", re.compile(r"\bdocument|case notes?\b|\bnotes\b|record the|log the", re.I)),
)
_SECTION_FALLBACK = {"CO": "other_compliance", "BP": "procedural", "ME": "service_quality"}

# Evaluator wording that suggests information may have reached someone other than the
# verified member. Only consulted for privacy-related categories.
_THIRD_PARTY = re.compile(
    r"third[- ]party|unauthori[sz]ed|not authori[sz]ed|wrong (?:member|person|account)|"
    r"another (?:member|person)|someone else|not the member|family member|spouse",
    re.I,
)

REPEAT_THRESHOLD = 2


def classify_requirement(source: str, requirement: str) -> str:
    """Map a scored QA requirement to an issue-category key."""
    text = str(requirement or "")
    for key, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return key
    return _SECTION_FALLBACK.get(str(source or "").upper(), "other_compliance")


def repeat_counts(records: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    """Failed evaluations per (employee, section code, requirement)."""
    counts: dict[tuple[str, str, str], int] = {}
    for record in records:
        if record.get("result") == "No":
            key = (record.get("agent", ""), record.get("source", ""), record.get("behavior", ""))
            counts[key] = counts.get(key, 0) + 1
    return counts


def risk_for_failure(category_key: str, repeat_count: int = 1, comment: str = "") -> dict[str, Any]:
    """Rule-based risk level for one failed QA item, with the reasons that produced it."""
    category = CATEGORIES.get(category_key, CATEGORIES["other_compliance"])
    level = category.base_risk
    reasons = [f"{category.label}: starts at {category.base_risk}."]
    if repeat_count >= REPEAT_THRESHOLD:
        level = _raise_level(level)
        reasons.append(
            f"The same employee missed this requirement on {repeat_count} evaluations in this "
            "dataset: raised one level."
        )
    if category.privacy_related and _THIRD_PARTY.search(str(comment or "")):
        level = _raise_level(level)
        reasons.append(
            "The evaluator's comment mentions someone other than the verified member: "
            "raised one level."
        )
    return {"level": level, "reasons": reasons}


def requirement_risk(queue_rows: list[dict[str, Any]], source: str, requirement: str) -> dict[str, Any]:
    """Highest rule-based risk among the failed records of one QA requirement.

    ``queue_rows`` are the rows from ``workflow.intake.build_queue``.
    """
    failed = [
        row
        for row in queue_rows
        if row["source"] == source and row["requirement"] == requirement and row["result"] == "Fail"
    ]
    category = CATEGORIES[classify_requirement(source, requirement)]
    if not failed:
        return {"level": "", "reasons": [], "category": category, "records": []}
    top = max(failed, key=lambda row: risk_rank(row["risk_level"]))
    reasons: list[str] = []
    for row in failed:
        reasons += [reason for reason in row["risk_reasons"] if reason not in reasons]
    return {
        "level": top["risk_level"],
        "reasons": reasons,
        "category": category,
        "records": [row["record_id"] for row in failed],
    }


# --------------------------------------------------------------------------------------
# Review flags
# --------------------------------------------------------------------------------------
OPEN = "open"
EVIDENCE_REQUESTED = "more_evidence_requested"
QA_FAILURE_CONFIRMED = "qa_failure_confirmed"
DISMISSED = "dismissed"
REFERRED = "referred_privacy"
FINDING_RECORDED = "compliance_finding_recorded"
NOT_SUBSTANTIATED = "compliance_not_substantiated"

STATUS_LABELS: dict[str, str] = {
    OPEN: "Open - awaiting supervisor review",
    EVIDENCE_REQUESTED: "Additional evidence requested",
    QA_FAILURE_CONFIRMED: "QA failure confirmed by supervisor",
    DISMISSED: "Flag dismissed - not supported",
    REFERRED: "Suspected privacy incident - referred for compliance review",
    FINDING_RECORDED: "Compliance finding recorded by authorized reviewer",
    NOT_SUBSTANTIATED: "Reviewed by compliance - not substantiated",
}
# Statuses that still need someone to act.
ATTENTION_STATUSES = frozenset({OPEN, EVIDENCE_REQUESTED, REFERRED})


@dataclass(frozen=True)
class FlagAction:
    key: str
    label: str
    permission: str
    from_statuses: frozenset[str]
    to_status: str
    note_required: bool
    attestation_required: bool
    help: str


FLAG_ACTIONS: dict[str, FlagAction] = {
    a.key: a
    for a in (
        FlagAction(
            "confirm_qa_failure",
            "Confirm the QA control was missed",
            "review_flag",
            frozenset({OPEN, EVIDENCE_REQUESTED}),
            QA_FAILURE_CONFIRMED,
            False,
            False,
            "Records that the evaluator's score stands. This is a QA finding only.",
        ),
        FlagAction(
            "request_evidence",
            "Request additional evidence",
            "request_evidence",
            frozenset({OPEN, QA_FAILURE_CONFIRMED}),
            EVIDENCE_REQUESTED,
            True,
            False,
            "Pauses the flag until the evidence described in the note is supplied.",
        ),
        FlagAction(
            "dismiss",
            "Dismiss the flag as unsupported",
            "review_flag",
            frozenset({OPEN, EVIDENCE_REQUESTED, QA_FAILURE_CONFIRMED}),
            DISMISSED,
            True,
            False,
            "Use when the evidence does not support the concern. The reason is kept.",
        ),
        FlagAction(
            "refer_privacy",
            "Refer as a suspected privacy incident",
            "refer_privacy",
            frozenset({OPEN, EVIDENCE_REQUESTED, QA_FAILURE_CONFIRMED}),
            REFERRED,
            True,
            True,
            "Sends the flag to compliance review. It records a suspicion, not a finding.",
        ),
        FlagAction(
            "record_finding",
            "Record a compliance finding",
            "record_determination",
            frozenset({REFERRED}),
            FINDING_RECORDED,
            True,
            True,
            "Only the compliance-reviewer role can record this, and only after a referral.",
        ),
        FlagAction(
            "record_not_substantiated",
            "Record that the concern was not substantiated",
            "record_determination",
            frozenset({REFERRED}),
            NOT_SUBSTANTIATED,
            True,
            True,
            "Only the compliance-reviewer role can record this, and only after a referral.",
        ),
        FlagAction(
            "reopen",
            "Reopen for supervisor review",
            "review_flag",
            frozenset({DISMISSED, QA_FAILURE_CONFIRMED, EVIDENCE_REQUESTED}),
            OPEN,
            True,
            False,
            "Returns the flag to the open queue with the reason recorded.",
        ),
    )
}
ATTESTATION_TEXT = (
    "I am authorized to take this action under my organization's policy and have reviewed "
    "the source record."
)


def build_flags(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Create one open flag per failed QA item that needs compliance attention.

    Flagged: every failed Compliance-section item, every failed item in a category marked
    ``flag_when_failed``, and any failed item the same employee has repeated.
    """
    repeats = repeat_counts(records)
    flags: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("result") != "No":
            continue
        source = record.get("source", "")
        requirement = record.get("behavior", "")
        category = CATEGORIES[classify_requirement(source, requirement)]
        repeat = repeats.get((record.get("agent", ""), source, requirement), 1)
        if not (source == "CO" or category.flag_when_failed or repeat >= REPEAT_THRESHOLD):
            continue
        risk = risk_for_failure(category.key, repeat, record.get("comment", ""))
        record_id = record.get("record_id", "")
        flags[record_id] = {
            "record_id": record_id,
            "employee": record.get("agent", ""),
            "team_lead": record.get("team_lead", ""),
            "call_date": record.get("call_date", ""),
            "source": source,
            "section": record.get("section", ""),
            "requirement": requirement,
            "comment": record.get("comment", ""),
            "category": category.key,
            "category_label": category.label,
            "privacy_related": category.privacy_related,
            "repeat_count": repeat,
            "rule_risk": risk["level"],
            "risk_reasons": risk["reasons"],
            "risk_override": None,
            "status": OPEN,
            "rationale": "",
            "follow_up_owner": "",
            "follow_up_due": "",
            "history": [],
        }
    return flags


def effective_risk(flag: dict[str, Any]) -> str:
    """The reviewer's override when one was recorded, otherwise the rule-based level."""
    return flag.get("risk_override") or flag.get("rule_risk") or MODERATE


def available_actions(flag: dict[str, Any], role: str) -> list[FlagAction]:
    """Actions that are valid for this flag's status *and* this role."""
    return [
        action
        for action in FLAG_ACTIONS.values()
        if flag.get("status") in action.from_statuses and can(role, action.permission)
    ]


def _history_entry(actor: str, role: str, action: str, note: str, **extra: Any) -> dict[str, Any]:
    return {"at": now_iso(), "actor": actor, "role": role, "action": action, "note": note, **extra}


def apply_action(
    flag: dict[str, Any],
    action_key: str,
    *,
    actor: str,
    role: str,
    note: str = "",
    attested: bool = False,
) -> dict[str, Any]:
    """Return an updated copy of ``flag`` after a validated status change."""
    action = FLAG_ACTIONS.get(action_key)
    if action is None:
        raise WorkflowError(f"Unknown flag action: {action_key}")
    require(role, action.permission)
    status = flag.get("status", OPEN)
    if status not in action.from_statuses:
        raise WorkflowError(
            f"'{action.label}' is not available while the flag is "
            f"'{STATUS_LABELS.get(status, status)}'."
        )
    if not str(actor or "").strip():
        raise WorkflowError("Enter the reviewer's name before recording a decision.")
    if action.note_required and not is_meaningful_note(note):
        raise WorkflowError(f"A rationale is required for this action. {NOTE_HINT}")
    if action.attestation_required and not attested:
        raise WorkflowError("Confirm the authorization statement before recording this action.")

    updated = copy.deepcopy(flag)
    updated["status"] = action.to_status
    clean_note = " ".join(str(note or "").split())
    if clean_note:
        updated["rationale"] = clean_note
    updated["history"].append(
        _history_entry(actor.strip(), role, action.key, clean_note, **{"from": status, "to": action.to_status})
    )
    return updated


def override_risk(
    flag: dict[str, Any], level: str, *, actor: str, role: str, note: str
) -> dict[str, Any]:
    """Record a reviewer's risk level in place of the rule-based one."""
    require(role, "override_risk")
    if level not in RISK_LEVELS:
        raise WorkflowError(f"Risk level must be one of: {', '.join(RISK_LEVELS)}.")
    if not str(actor or "").strip():
        raise WorkflowError("Enter the reviewer's name before changing the risk level.")
    if not is_meaningful_note(note):
        raise WorkflowError(f"A reason is required to change the risk level. {NOTE_HINT}")
    updated = copy.deepcopy(flag)
    previous = effective_risk(flag)
    updated["risk_override"] = None if level == flag.get("rule_risk") else level
    updated["history"].append(
        _history_entry(
            actor.strip(), role, "override_risk", " ".join(note.split()), **{"from": previous, "to": level}
        )
    )
    return updated


def assign_follow_up(
    flag: dict[str, Any],
    *,
    owner: str,
    due: str,
    actor: str,
    role: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Record who follows up on a flag and by when (ISO date, today or later)."""
    require(role, "assign_follow_up")
    owner = " ".join(str(owner or "").split())
    if len(owner) < 2:
        raise WorkflowError("Name the person or team responsible for the follow-up.")
    try:
        due_date = date.fromisoformat(str(due))
    except ValueError as exc:
        raise WorkflowError("The follow-up date must be a valid date (YYYY-MM-DD).") from exc
    if due_date < (today or date.today()):
        raise WorkflowError("The follow-up date cannot be in the past.")
    updated = copy.deepcopy(flag)
    updated["follow_up_owner"] = owner
    updated["follow_up_due"] = due_date.isoformat()
    updated["history"].append(
        _history_entry(
            str(actor or "").strip(), role, "assign_follow_up", f"{owner}, due {due_date.isoformat()}"
        )
    )
    return updated


def summarize_flags(flags: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Counts used by the dashboard and the compliance page."""
    by_status = {status: 0 for status in STATUS_LABELS}
    by_risk = {level: 0 for level in RISK_LEVELS}
    for flag in flags.values():
        by_status[flag.get("status", OPEN)] = by_status.get(flag.get("status", OPEN), 0) + 1
        by_risk[effective_risk(flag)] += 1
    return {
        "total": len(flags),
        "by_status": by_status,
        "by_risk": by_risk,
        "needs_attention": sum(by_status[s] for s in ATTENTION_STATUSES),
        "referred": by_status[REFERRED],
    }
