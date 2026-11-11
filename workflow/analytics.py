"""Dashboard figures.

Every number here is counted from the loaded QA records or from what has been
done in the current session. Nothing is estimated in this module; the labor
estimate lives in ``business_impact.py`` and is labeled as an estimate.
"""

from __future__ import annotations

from typing import Any

from workflow import coaching, compliance, referrals, review


def _rate(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def qa_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """QA performance figures for a set of queue rows (see ``intake.build_queue``)."""
    total = len(rows)
    failed_rows = [r for r in rows if r["result"] == "Fail"]
    failed = len(failed_rows)

    def grouped(key: str) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            group = groups.setdefault(row[key], {key: row[key], "evaluated": 0, "failed": 0})
            group["evaluated"] += 1
            group["failed"] += row["result"] == "Fail"
        for group in groups.values():
            group["fail_rate_pct"] = _rate(group["failed"], group["evaluated"])
        return sorted(groups.values(), key=lambda g: (-g["failed"], str(g[key])))

    behaviors: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        entry = behaviors.setdefault(
            (row["source"], row["requirement"]),
            {
                "requirement": row["requirement"],
                "section": row["section"],
                "issue_type": row["issue_type"],
                "evaluated": 0,
                "failed": 0,
            },
        )
        entry["evaluated"] += 1
        entry["failed"] += row["result"] == "Fail"
    missed = sorted(
        (b for b in behaviors.values() if b["failed"]),
        key=lambda b: (-b["failed"], b["requirement"]),
    )
    for entry in missed:
        entry["fail_rate_pct"] = _rate(entry["failed"], entry["evaluated"])

    # A repeat finding: the same employee missed the same requirement more than once.
    repeats: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in failed_rows:
        key = (row["employee"], row["source"], row["requirement"])
        entry = repeats.setdefault(
            key,
            {
                "employee": row["employee"],
                "requirement": row["requirement"],
                "section": row["section"],
                "issue_type": row["issue_type"],
                "times": 0,
                "records": [],
            },
        )
        entry["times"] += 1
        entry["records"].append(row["record_id"])
    repeat_findings = sorted(
        (r for r in repeats.values() if r["times"] >= compliance.REPEAT_THRESHOLD),
        key=lambda r: (-r["times"], r["employee"]),
    )
    by_category: dict[str, dict[str, Any]] = {}
    for entry in repeat_findings:
        category = by_category.setdefault(
            entry["issue_type"], {"issue_type": entry["issue_type"], "employees": 0, "failed_items": 0}
        )
        category["employees"] += 1
        category["failed_items"] += entry["times"]

    return {
        "total": total,
        "passed": total - failed,
        "failed": failed,
        "pass_rate_pct": _rate(total - failed, total),
        "fail_rate_pct": _rate(failed, total),
        "employees": len({r["employee"] for r in rows}),
        # An employee/date pair is a proxy for one evaluated call; the export has no call ID.
        "evaluations": len({(r["employee"], r["call_date"]) for r in rows}),
        "by_section": grouped("section"),
        "by_employee": grouped("employee"),
        "missed_behaviors": missed,
        "by_risk": {
            level: sum(1 for r in failed_rows if r["risk_level"] == level)
            for level in compliance.RISK_LEVELS
        },
        "repeat_findings": repeat_findings,
        "repeat_by_category": sorted(by_category.values(), key=lambda c: -c["failed_items"]),
    }


def workflow_overview(
    diagnosis: dict[str, Any] | None,
    store: dict[str, Any],
    training: dict[str, Any] | None,
) -> dict[str, Any]:
    """Where the review work stands in this session."""
    decisions = store.get("decisions", {})
    progress = review.progress(diagnosis, decisions)
    flags = compliance.summarize_flags(store.get("flags", {}))
    generated = {m.get("finding_id") for m in (training or {}).get("modules", [])}
    ready = [
        f["id"] for f in (diagnosis or {}).get("findings", []) if review.coaching_ready(decisions.get(f["id"]))
    ]
    done = total = 0
    follow_up = {coaching.PENDING: 0, coaching.SCHEDULED: 0, coaching.PASSED: 0, coaching.FAILED: 0}
    for finding_id, tracker in store.get("trackers", {}).items():
        if finding_id not in generated:
            continue
        summary = coaching.tracker_summary(tracker)
        done += summary["done"]
        total += summary["total"]
        for status, count in summary["follow_up"].items():
            follow_up[status] += count
    return {
        "analysis_loaded": diagnosis is not None,
        "findings": progress["findings"],
        "open_investigations": progress["open"],
        "approved": progress["approved"],
        "rejected": progress["rejected"],
        "evidence_requested": progress["evidence_requested"],
        "coaching_ready": len(ready),
        "packages_generated": sum(1 for fid in ready if fid in generated),
        "packages_pending": sum(1 for fid in ready if fid not in generated),
        "flags_total": flags["total"],
        "flags_needing_attention": flags["needs_attention"],
        "flags_referred": flags["referred"],
        "flags_by_status": flags["by_status"],
        "flags_by_risk": flags["by_risk"],
        "training_done": done,
        "training_total": total,
        "training_percent": round(100 * done / total) if total else 0,
        "follow_up": follow_up,
        "referrals": referrals.summarize(store.get("referrals", [])),
        "audit_entries": len(store.get("audit", [])),
    }


def employee_context(rows: list[dict[str, Any]], employee: str) -> dict[str, Any]:
    """Recorded QA facts about one employee. Facts only; no recommendation is derived."""
    mine = [r for r in rows if r["employee"] == employee]
    failed = [r for r in mine if r["result"] == "Fail"]
    overview = qa_overview(mine)
    return {
        "evaluated": len(mine),
        "failed": len(failed),
        "failed_records": [r["record_id"] for r in failed],
        "repeat_findings": overview["repeat_findings"],
    }
