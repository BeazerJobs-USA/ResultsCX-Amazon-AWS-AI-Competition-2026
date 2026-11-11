"""Dashboard: where the review work stands, QA performance, and the efficiency estimate.

Every figure on this page is either counted from the loaded QA records, counted
from actions taken in this session, or calculated from assumptions the user
types into the efficiency estimate. Nothing is prefilled to look like a result.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import business_impact
from ui import components as ui, state
from workflow import analytics, coaching, compliance, intake, referrals, review

VIEWS = ("Operational status", "QA performance", "Efficiency estimate")


def render() -> None:
    ui.page_header(
        state.DASHBOARD,
        "The current state of QA review in this session. Figures are counted from the loaded "
        "records and from actions taken here.",
    )
    dataset = state.data()
    if dataset is None:
        st.error("No QA dataset is loaded. " + ui.esc(st.session_state.get("data_error", "")))
        return
    rows = intake.build_queue(dataset)
    overview = analytics.qa_overview(rows)
    workflow = analytics.workflow_overview(
        st.session_state["diagnosis"], state.store(), st.session_state["training"]
    )
    _headline(overview, workflow)
    _next_step(workflow)
    settings = state.page("dashboard")
    view = ui.radio("Dashboard view", VIEWS, settings, "view", key="dash_view", horizontal=True)
    if view == VIEWS[0]:
        _operational_status(workflow, rows)
    elif view == VIEWS[1]:
        _qa_performance(rows, settings)
    else:
        _efficiency(settings, overview, workflow)


def _headline(overview: dict[str, Any], workflow: dict[str, Any]) -> None:
    loaded = workflow["analysis_loaded"]
    first = st.columns(4)
    first[0].metric("QA items evaluated", overview["total"], help="Scored line items in the loaded dataset.", border=True)
    first[1].metric("Pass rate", f"{overview['pass_rate_pct']:.0f}%", help="Share of scored items answered Yes.", border=True)
    first[2].metric("Failed items", overview["failed"], help="Scored items answered No.", border=True)
    first[3].metric(
        "Flags needing review",
        workflow["flags_needing_attention"],
        help="Compliance flags that are open, waiting for evidence, or referred.",
        border=True,
    )
    second = st.columns(4)
    second[0].metric(
        "Open investigations",
        workflow["open_investigations"] if loaded else "Not started",
        help="AI findings without a final supervisor decision.",
        border=True,
    )
    second[1].metric(
        "Supervisor-approved findings",
        workflow["approved"] if loaded else "Not started",
        help="Findings a supervisor approved, with or without corrections.",
        border=True,
    )
    second[2].metric(
        "Coaching packages",
        f"{workflow['packages_generated']} of {workflow['coaching_ready']}",
        help="Packages generated, out of findings sent to coaching or remediation.",
        border=True,
    )
    second[3].metric(
        "Practice recorded",
        f"{workflow['training_done']} of {workflow['training_total']}",
        help="Practice and follow-up items completed across generated packages.",
        border=True,
    )


def _next_step(workflow: dict[str, Any]) -> None:
    if not workflow["analysis_loaded"]:
        message, label, target = "Review the QA records, then start the analysis.", "Open intake", state.INTAKE
    elif workflow["open_investigations"]:
        message = f"{workflow['open_investigations']} finding(s) are waiting for a supervisor decision."
        label, target = "Open supervisor review", state.REVIEW
    elif workflow["packages_pending"]:
        message = f"{workflow['packages_pending']} approved finding(s) have no coaching package yet."
        label, target = "Open coaching", state.COACHING
    elif workflow["flags_needing_attention"]:
        message = f"{workflow['flags_needing_attention']} compliance flag(s) still need review."
        label, target = "Open compliance review", state.COMPLIANCE
    elif workflow["training_total"] and workflow["training_done"] < workflow["training_total"]:
        message = "Every finding is decided. Record practice and follow-up results as coaching happens."
        label, target = "Open coaching", state.COACHING
    else:
        message, label, target = "Nothing is waiting for action in this session.", "Open the audit trail", state.AUDIT
    with st.container(border=True):
        left, right = st.columns([4, 1], vertical_alignment="center")
        left.markdown(f"**Next step:** {message}")
        with right:
            ui.goto_button(label, target, key="dash_next")


# -- operational status ------------------------------------------------------------------
def _operational_status(workflow: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    store = state.store()
    decisions = store["decisions"]
    modules = state.modules_by_finding()

    st.subheader("Findings")
    findings = state.findings()
    if not findings:
        st.caption("No analysis has been loaded yet.")
    else:
        table = []
        for finding in findings:
            record = decisions.get(finding["id"])
            risk = compliance.requirement_risk(rows, finding.get("source", ""), finding.get("behavior", ""))
            if record is None:
                status = ui.pending_badge("Awaiting supervisor decision")
            elif review.is_final(record):
                status = ui.badge(record["outcome_label"], "green" if record["outcome"] != "rejected" else "gray", ":material/person:")
            else:
                status = ui.pending_badge(record["outcome_label"])
            if review.coaching_ready(record):
                package = (
                    ui.badge("Generated", "green", ":material/task_alt:")
                    if finding["id"] in modules
                    else ui.pending_badge("Not generated")
                )
            else:
                package = "Not applicable"
            next_action = review.NEXT_ACTIONS.get((record or {}).get("next_action", ""), "Not decided")
            table.append(
                [ui.esc(finding["id"]), ui.esc(finding["title"]), ui.risk_badge(risk["level"]), status, ui.esc(next_action), package]
            )
        ui.data_table(["Finding", "Title", "Risk", "Review status", "Next action", "Coaching package"], table)

    left, right = st.columns(2)
    with left:
        st.subheader("Compliance flags")
        flag_rows = [
            [ui.flag_status_badge(status), str(count)]
            for status, count in workflow["flags_by_status"].items()
            if count
        ]
        if flag_rows:
            st.markdown(ui.md_table(["Status", "Flags"], flag_rows))
            st.caption(
                "By risk level: "
                + ", ".join(f"{level} {count}" for level, count in workflow["flags_by_risk"].items())
                + ". "
                + compliance.RISK_DISCLAIMER
            )
        else:
            st.caption("No failed item in this dataset needs compliance review.")
    with right:
        st.subheader("Training and follow-up")
        tracker_rows = []
        for finding_id, tracker in store["trackers"].items():
            if finding_id not in modules:
                continue
            summary = coaching.tracker_summary(tracker)
            follow = summary["follow_up"]
            tracker_rows.append(
                [
                    ui.esc(finding_id),
                    str(summary["employees"]),
                    f"{summary['done']} of {summary['total']}",
                    f"{follow[coaching.SCHEDULED]} scheduled, {follow[coaching.PASSED]} passed, "
                    f"{follow[coaching.FAILED]} missed again, {follow[coaching.PENDING]} not scheduled",
                ]
            )
        if tracker_rows:
            st.markdown(
                ui.md_table(["Package", "Employees", "Practice recorded", "Follow-up evaluations"], tracker_rows)
            )
        else:
            st.caption("No coaching package has been generated yet.")
        counts = workflow["referrals"]
        st.caption(
            f"Simulated administrative referrals: {counts[referrals.DRAFT]} draft, "
            f"{counts[referrals.SUBMITTED]} marked for HR review, {counts[referrals.WITHDRAWN]} withdrawn. "
            f"Audit trail: {workflow['audit_entries']} entries."
        )


# -- QA performance ----------------------------------------------------------------------
def _qa_performance(rows: list[dict[str, Any]], settings: dict[str, Any]) -> None:
    options = intake.filter_options(rows)
    filters = settings.setdefault("filters", {})
    columns = st.columns(4)
    with columns[0]:
        sections = ui.multiselect("Evaluation category", options["sections"], filters, "sections", key="dash_f_sections")
    with columns[1]:
        employees = ui.multiselect("Employee", options["employees"], filters, "employees", key="dash_f_employees")
    date_from = date_to = None
    if options["date_min"] and options["date_max"]:
        with columns[2]:
            date_from = ui.date_input(
                "From", filters, "date_from", key="dash_f_from", default=options["date_min"],
                min_value=options["date_min"], max_value=options["date_max"],
            )
        with columns[3]:
            date_to = ui.date_input(
                "To", filters, "date_to", key="dash_f_to", default=options["date_max"],
                min_value=options["date_min"], max_value=options["date_max"],
            )
    selected = intake.filter_queue(
        rows, sections=sections, employees=employees, date_from=date_from, date_to=date_to
    )
    if not selected:
        st.info("No QA records match these filters.")
        return
    overview = analytics.qa_overview(selected)
    st.caption(
        f"{overview['total']} scored items from {overview['employees']} employee(s) across "
        f"{overview['evaluations']} employee-date group(s). An employee-date group stands in for one "
        "evaluated call because the data has no call identifier."
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Most frequently missed requirements")
        if overview["missed_behaviors"]:
            chart = pd.DataFrame(
                {
                    "QA requirement": [b["requirement"] for b in overview["missed_behaviors"]],
                    "Failed items": [b["failed"] for b in overview["missed_behaviors"]],
                }
            )
            st.bar_chart(chart, x="QA requirement", y="Failed items", horizontal=True)
        else:
            st.caption("No failed items in this selection.")
    with right:
        st.subheader("Failed items by employee")
        by_employee = [e for e in overview["by_employee"] if e["failed"]]
        if by_employee:
            chart = pd.DataFrame(
                {
                    "Employee": [e["employee"] for e in by_employee],
                    "Failed items": [e["failed"] for e in by_employee],
                }
            )
            st.bar_chart(chart, x="Employee", y="Failed items", horizontal=True)
        else:
            st.caption("No failed items in this selection.")

    left, right = st.columns(2)
    with left:
        st.subheader("Pass and fail by category")
        st.markdown(
            ui.md_table(
                ["Evaluation category", "Evaluated", "Failed", "Fail rate"],
                [
                    [ui.esc(s["section"]), str(s["evaluated"]), str(s["failed"]), f"{s['fail_rate_pct']:.0f}%"]
                    for s in overview["by_section"]
                ],
            )
        )
        st.caption(
            "Failed items by rule-based risk level: "
            + ", ".join(f"{level} {count}" for level, count in overview["by_risk"].items())
            + "."
        )
    with right:
        st.subheader("Repeat findings")
        if overview["repeat_findings"]:
            st.markdown(
                ui.md_table(
                    ["Employee", "QA requirement", "Issue type", "Times missed", "Records"],
                    [
                        [
                            ui.esc(r["employee"]),
                            ui.esc(r["requirement"]),
                            ui.esc(r["issue_type"]),
                            str(r["times"]),
                            ui.esc(", ".join(r["records"])),
                        ]
                        for r in overview["repeat_findings"]
                    ],
                )
            )
            st.caption(
                "A repeat finding is the same employee missing the same requirement on more than one "
                "evaluation. By issue type: "
                + "; ".join(
                    f"{ui.esc(c['issue_type'])}: {c['employees']} employee(s), {c['failed_items']} items"
                    for c in overview["repeat_by_category"]
                )
                + "."
            )
        else:
            st.caption("No employee missed the same requirement more than once in this selection.")


# -- efficiency estimate -----------------------------------------------------------------
def _efficiency(settings: dict[str, Any], overview: dict[str, Any], workflow: dict[str, Any]) -> None:
    st.subheader("Supervisor preparation time: an estimate from your assumptions")
    st.markdown(
        "A failed QA item still has to be turned into a coaching conversation, a knowledge check, "
        "and practice. This calculator estimates the preparation time an AI-drafted package might "
        "free up. **Every input is an assumption you enter. No figure here was measured.**"
    )
    inputs = settings.setdefault("estimate", {})
    left, right = st.columns(2)
    with left:
        analyzed = ui.number_input(
            "QA cases analyzed per week", inputs, "cases_analyzed", key="eff_analyzed", default=200,
            min_value=0, max_value=business_impact.MAX_CASES_PER_WEEK, step=10,
        )
        coaching_cases = ui.number_input(
            "Cases requiring coaching per week", inputs, "cases_requiring_coaching", key="eff_coaching",
            default=40, min_value=0, max_value=business_impact.MAX_CASES_PER_WEEK, step=5,
        )
        employees = ui.number_input(
            "Employees covered", inputs, "employees", key="eff_employees", default=25,
            min_value=0, max_value=business_impact.MAX_EMPLOYEES, step=5,
        )
        weeks = ui.number_input(
            "Working weeks per year", inputs, "weeks_per_year", key="eff_weeks", default=50,
            min_value=0, max_value=52, step=1,
        )
    with right:
        manual = ui.number_input(
            "Minutes to prepare one coaching package manually", inputs, "manual_minutes", key="eff_manual",
            default=30, min_value=0, max_value=business_impact.MAX_MINUTES_PER_CASE, step=5,
        )
        assisted = ui.number_input(
            "Minutes with AI assistance, including supervisor review", inputs, "assisted_minutes",
            key="eff_assisted", default=18, min_value=0, max_value=business_impact.MAX_MINUTES_PER_CASE, step=1,
        )
        rate = ui.number_input(
            "Supervisor hourly labor rate (USD, fully loaded)", inputs, "hourly_rate", key="eff_rate",
            default=45.0, min_value=0.0, max_value=float(business_impact.MAX_HOURLY_RATE), step=5.0,
        )
    scenario = {
        "cases_analyzed": analyzed,
        "cases_requiring_coaching": coaching_cases,
        "manual_minutes": manual,
        "assisted_minutes": assisted,
        "hourly_rate": rate,
        "weeks_per_year": weeks,
        "employees": employees,
    }
    problems = business_impact.validate_scenario(**scenario)
    if problems:
        ui.show_problems(problems, "The estimate cannot be calculated with these inputs:")
        return
    estimate = business_impact.efficiency_estimate(**scenario)
    results = st.columns(4)
    results[0].metric("Estimated hours freed per week", f"{estimate['saved_weekly_hours']:,.1f}", border=True)
    results[1].metric("Estimated hours freed per year", f"{estimate['saved_annual_hours']:,.0f}", border=True)
    results[2].metric("Estimated labor capacity per year", f"${estimate['annual_capacity_value']:,.0f}", border=True)
    results[3].metric(
        "Hours freed per employee per year", f"{estimate['annual_hours_per_employee']:,.1f}", border=True
    )
    if estimate["assisted_is_slower"]:
        st.warning(
            "With these inputs the AI-assisted process takes longer than manual preparation, so the "
            "estimate is negative."
        )
    with st.expander("How these figures are calculated", expanded=True):
        st.markdown("\n".join(f"- {ui.esc(line)}" for line in estimate["formulas"]))
    st.caption(
        "Assumptions and limits: the inputs are illustrative defaults, not ResultsCX figures. Time freed "
        "is capacity, not payroll savings. The estimate says nothing about whether coaching improves QA "
        "results or reduces compliance risk; that needs before-and-after QA data from a real pilot. "
        f"For reference only, this session's dataset has {overview['total']} scored items and "
        f"{workflow['coaching_ready']} finding(s) sent to coaching; those are not weekly volumes."
    )
