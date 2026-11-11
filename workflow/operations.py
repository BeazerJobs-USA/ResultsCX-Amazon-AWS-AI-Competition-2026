"""State-changing operations on the case store.

The *case store* is a plain dictionary holding everything people decide during
a session: flags, supervisor decisions, coaching packages, practice results,
referrals, and the audit trail. The interface never edits it directly. It calls
one of the functions below, each of which validates the request, applies it,
and writes an audit entry. An operation that fails validation raises
:class:`workflow.common.WorkflowError` and leaves the store untouched.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

from workflow import audit, coaching, compliance, referrals, review
from workflow.common import WorkflowError, require

STORE_VERSION = 1


def new_store(records: list[dict[str, Any]]) -> dict[str, Any]:
    """An empty case store for a dataset, with its compliance flags opened."""
    return {
        "version": STORE_VERSION,
        "flags": compliance.build_flags(records),
        "decisions": {},
        "packages": {},
        "trackers": {},
        "referrals": [],
        "audit": [],
    }


def log_event(
    store: dict[str, Any],
    action: str,
    *,
    actor: str,
    role: str,
    subject_type: str = "session",
    subject_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Record something that happened but changed no decision (data loaded, analysis run)."""
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        details=details,
    )


# --------------------------------------------------------------------------------------
# Findings and their related flags
# --------------------------------------------------------------------------------------
def related_flags(store: dict[str, Any], finding: dict[str, Any]) -> list[dict[str, Any]]:
    """Flags raised on the failed records behind a finding's QA requirement."""
    return [
        flag
        for flag in store["flags"].values()
        if flag["requirement"] == finding.get("behavior") and flag["source"] == finding.get("source")
    ]


def record_decision(
    store: dict[str, Any],
    finding: dict[str, Any],
    draft: dict[str, Any],
    *,
    actor: str,
    role: str,
) -> dict[str, Any]:
    """Record a supervisor decision and carry its consequences to the related flags.

    Returns ``{"record": ..., "flags_updated": [...], "flags_unchanged": [...]}``.
    """
    previous = store["decisions"].get(finding["id"])
    record = review.build_record(finding, draft, actor=actor, role=role, previous=previous)

    # Work on copies so a failure part-way through cannot leave half-applied flag changes.
    flags = copy.deepcopy(store["flags"])
    updated, unchanged = [], []
    default_note = f"Recorded with the supervisor decision on finding {finding['id']}."
    for flag in related_flags(store, finding):
        current = flags[flag["record_id"]]
        for action_key in review.flag_actions_for(record):
            action = compliance.FLAG_ACTIONS[action_key]
            if current["status"] not in action.from_statuses:
                unchanged.append({"record_id": current["record_id"], "action": action_key})
                continue
            current = compliance.apply_action(
                current,
                action_key,
                actor=actor,
                role=role,
                note=record["note"] or default_note,
                attested=bool(draft.get("attested")),
            )
            updated.append({"record_id": current["record_id"], "action": action_key, "status": current["status"]})
        flags[flag["record_id"]] = current

    store["flags"] = flags
    store["decisions"][finding["id"]] = record
    package = store["packages"].get(finding["id"])
    if package and previous:
        # The package was built from an earlier decision. Keep it, but say so.
        package["stale"] = True
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="finding.decision_recorded",
        subject_type="finding",
        subject_id=finding["id"],
        details={
            "outcome": record["outcome"],
            "control": record["control"],
            "next_action": record["next_action"],
            "privacy_referral": record["privacy_referral"],
            "revision": record["revision"],
            "note": record["note"],
            "ai_original": record["ai_original"],
            "supervisor_final": record["final"],
            "changes": [
                {"field": c["field"], "ai_value": c["ai_value"], "supervisor_value": c["supervisor_value"]}
                for c in record["changes"]
            ],
        },
    )
    for change in updated:
        audit.append(
            store["audit"],
            actor=actor,
            role=role,
            action=f"flag.{change['action']}",
            subject_type="flag",
            subject_id=change["record_id"],
            details={"status": change["status"], "via_finding": finding["id"], "note": record["note"] or default_note},
        )
    return {"record": record, "flags_updated": updated, "flags_unchanged": unchanged}


def flag_action(
    store: dict[str, Any],
    record_id: str,
    action_key: str,
    *,
    actor: str,
    role: str,
    note: str = "",
    attested: bool = False,
) -> dict[str, Any]:
    """Apply one review action to one flag."""
    flag = _flag(store, record_id)
    updated = compliance.apply_action(flag, action_key, actor=actor, role=role, note=note, attested=attested)
    store["flags"][record_id] = updated
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action=f"flag.{action_key}",
        subject_type="flag",
        subject_id=record_id,
        details={"from": flag["status"], "status": updated["status"], "note": updated["history"][-1]["note"]},
    )
    return updated


def flag_override_risk(
    store: dict[str, Any], record_id: str, level: str, *, actor: str, role: str, note: str
) -> dict[str, Any]:
    """Replace a flag's rule-based risk level with a reviewer's, keeping the reason."""
    flag = _flag(store, record_id)
    updated = compliance.override_risk(flag, level, actor=actor, role=role, note=note)
    store["flags"][record_id] = updated
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="flag.risk_override",
        subject_type="flag",
        subject_id=record_id,
        details={
            "rule_level": flag["rule_risk"],
            "from": compliance.effective_risk(flag),
            "to": level,
            "note": " ".join(note.split()),
        },
    )
    return updated


def flag_follow_up(
    store: dict[str, Any],
    record_id: str,
    *,
    owner: str,
    due: str,
    actor: str,
    role: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Assign follow-up responsibility for a flag."""
    flag = _flag(store, record_id)
    updated = compliance.assign_follow_up(flag, owner=owner, due=due, actor=actor, role=role, today=today)
    store["flags"][record_id] = updated
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="flag.follow_up_assigned",
        subject_type="flag",
        subject_id=record_id,
        details={"owner": updated["follow_up_owner"], "due": updated["follow_up_due"]},
    )
    return updated


def _flag(store: dict[str, Any], record_id: str) -> dict[str, Any]:
    flag = store["flags"].get(record_id)
    if flag is None:
        raise WorkflowError(f"There is no review flag for record {record_id}.")
    return flag


# --------------------------------------------------------------------------------------
# Coaching packages and practice
# --------------------------------------------------------------------------------------
def register_package(
    store: dict[str, Any],
    finding: dict[str, Any],
    *,
    components: list[str],
    employees: list[str],
    source: str,
    actor: str,
    role: str,
) -> dict[str, Any]:
    """Record that a coaching package now exists for an approved finding."""
    require(role, "generate_coaching")
    decision = store["decisions"].get(finding["id"])
    if not review.coaching_ready(decision):
        raise WorkflowError(
            "A coaching package needs a supervisor-approved finding whose next action is "
            "coaching or remediation."
        )
    chosen = coaching.normalize_components(components)
    assigned = [e for e in employees if e in finding.get("agents_failed", [])]
    if not assigned:
        raise WorkflowError("Assign at least one employee who missed this requirement.")
    fresh = coaching.new_tracker(assigned, chosen)
    existing = store["trackers"].get(finding["id"], {})
    for employee, items in fresh.items():
        for item in items:
            # Regenerating a package keeps practice already recorded for the same activity.
            if existing.get(employee, {}).get(item):
                items[item] = existing[employee][item]
    package = {
        "finding_id": finding["id"],
        "components": chosen,
        "employees": assigned,
        "source": source,
        "decision_revision": decision["revision"] if decision else 0,
        "generated_by": str(actor or "").strip(),
        "stale": False,
    }
    store["packages"][finding["id"]] = package
    store["trackers"][finding["id"]] = fresh
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="package.generated",
        subject_type="package",
        subject_id=finding["id"],
        details={"components": chosen, "employees": assigned, "source": source},
    )
    return package


def record_practice(
    store: dict[str, Any],
    finding_id: str,
    employee: str,
    item: str,
    *,
    status: str,
    detail: str = "",
    actor: str,
    role: str,
) -> dict[str, Any]:
    """Record one practice or follow-up result for one employee."""
    tracker = store["trackers"].get(finding_id)
    if tracker is None:
        raise WorkflowError("Generate the coaching package before recording practice.")
    updated = coaching.record_item(
        tracker, employee, item, status=status, detail=detail, actor=actor, role=role
    )
    store["trackers"][finding_id] = updated
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="practice.recorded",
        subject_type="package",
        subject_id=finding_id,
        details={"employee": employee, "item": item, "status": status, "detail": updated[employee][item]["detail"]},
    )
    return updated


# --------------------------------------------------------------------------------------
# Simulated referrals
# --------------------------------------------------------------------------------------
def create_referral(
    store: dict[str, Any],
    form: dict[str, Any],
    *,
    actor: str,
    role: str,
    employees: list[str],
    case_ids: list[str],
    submitting: bool,
    today: date | None = None,
) -> dict[str, Any]:
    """Save a supervisor-written referral as a draft or mark it for HR review."""
    referral = referrals.create(
        store["referrals"],
        form,
        actor=actor,
        role=role,
        employees=employees,
        case_ids=case_ids,
        submitting=submitting,
        today=today,
    )
    store["referrals"].append(referral)
    audit.append(
        store["audit"],
        actor=actor,
        role=role,
        action="referral.created",
        subject_type="referral",
        subject_id=referral["id"],
        # Deliberately minimal. The audit trail can be saved with a live run, and a
        # referral's employee and proposed action must never reach disk.
        details={"simulated": True, "status": referral["status"]},
    )
    return referral


def change_referral_status(
    store: dict[str, Any],
    referral_id: str,
    new_status: str,
    *,
    actor: str,
    role: str,
    note: str = "",
    attested: bool = False,
) -> dict[str, Any]:
    """Submit or withdraw an existing referral."""
    for index, referral in enumerate(store["referrals"]):
        if referral["id"] == referral_id:
            updated = referrals.change_status(
                referral, new_status, actor=actor, role=role, note=note, attested=attested
            )
            store["referrals"][index] = updated
            audit.append(
                store["audit"],
                actor=actor,
                role=role,
                action=f"referral.{new_status}",
                subject_type="referral",
                subject_id=referral_id,
                details={"simulated": True, "from": referral["status"], "status": new_status},
            )
            return updated
    raise WorkflowError(f"There is no referral {referral_id}.")


def case_ids(diagnosis: dict[str, Any] | None, store: dict[str, Any]) -> list[str]:
    """Identifiers a referral may cite: reviewed findings first, then flagged records."""
    findings = [f["id"] for f in (diagnosis or {}).get("findings", [])]
    return findings + sorted(store["flags"])
