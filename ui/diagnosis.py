"""Stage 2: AI diagnosis and risk assessment.

For each finding the page keeps two things apart. On the left are recorded
facts: the QA requirement, the figures computed from the records, the evaluator
comments the analysis cited (each checked against the loaded data), and the
rule-based risk level. On the right is the AI's interpretation: what it thinks
happened, why, and what to do. The interpretation is a proposal. Nothing on this
page becomes a decision until a supervisor records one in stage 3.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from ui import actions, components as ui, state
from workflow import compliance, intake, review

DISTINCTION = (
    "A QA failure is what the evaluator scored. A suspected privacy incident is a supervisor's "
    "referral for closer review. A compliance finding can only be recorded by a compliance "
    "reviewer after a referral. This page shows QA failures and a triage risk level, nothing more."
)


def render() -> None:
    ui.page_header(
        state.DIAGNOSIS,
        "Examine what the analysis proposes for each QA requirement. Recorded facts are on the left. "
        "The AI's interpretation is on the right and has not been verified.",
    )
    diagnosis = st.session_state["diagnosis"]
    dataset = state.data()
    if not diagnosis or dataset is None:
        ui.empty_state(
            "No analysis has been loaded. Review the QA records and start the analysis in stage 1.",
            "Open intake",
            state.INTAKE,
            key="diag_to_intake",
        )
        return
    rows = intake.build_queue(dataset)
    _summary(diagnosis)
    finding = _findings_overview(diagnosis, rows)
    _finding_detail(finding, rows)
    _actions(finding)
    _other_notes(diagnosis)


def _source_label() -> str:
    return ui.DEMO_LABEL if state.mode() == state.DEMO else ui.AI_LABEL


def _summary(diagnosis: dict[str, Any]) -> None:
    meta = diagnosis.get("_meta", {})
    verified = sum(f["evidence_verified"] for f in diagnosis["findings"])
    cited = sum(f["evidence_total"] for f in diagnosis["findings"])
    with st.container(border=True):
        st.markdown(f"**Overall summary** {_source_label()}")
        ui.quote(diagnosis.get("summary"), "The analysis returned no overall summary.")
        details = [
            f"{len(diagnosis['findings'])} finding(s)",
            f"{verified} of {cited} cited comments matched to a record, its requirement, and its wording",
            f"produced: {meta.get('generated_at', 'unknown')}",
        ]
        if meta.get("input_tokens") is not None:
            details.append(f"tokens in/out: {meta.get('input_tokens')}/{meta.get('output_tokens')}")
        if meta.get("focus_behavior_ids"):
            details.append("scope limited to " + ", ".join(meta["focus_behavior_ids"]))
        if diagnosis.get("out_of_scope_dropped"):
            details.append(f"{diagnosis['out_of_scope_dropped']} out-of-scope finding(s) discarded")
        st.caption(ui.esc(" · ".join(details)))


def _review_status(finding_id: str) -> str:
    record = state.store()["decisions"].get(finding_id)
    if record is None:
        return ui.pending_badge("Awaiting supervisor decision")
    if not review.is_final(record):
        return ui.pending_badge(record["outcome_label"])
    tone = "gray" if record["outcome"] == "rejected" else "green"
    return ui.badge(record["outcome_label"], tone, ":material/person:")


def _evidence_check(finding: dict[str, Any]) -> str:
    total, verified = finding["evidence_total"], finding["evidence_verified"]
    if total == 0:
        return ui.insufficient_badge("No evidence cited")
    if verified == total:
        return ui.badge(f"{verified} of {total} matched", "green", ":material/check_circle:")
    return ui.badge(f"{verified} of {total} matched", "orange", ":material/warning:")


def _findings_overview(diagnosis: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    st.subheader("Findings")
    findings = diagnosis["findings"]
    table = []
    for finding in findings:
        risk = compliance.requirement_risk(rows, finding.get("source", ""), finding.get("behavior", ""))
        rate = (
            f"{finding['defect_rate_pct']}% ({finding['failed']} of {finding['evaluated']})"
            if finding.get("metrics_verified")
            else "Not matched"
        )
        table.append(
            [
                ui.esc(finding["id"]),
                ui.esc(finding["title"]),
                rate,
                ui.risk_badge(risk["level"]),
                _evidence_check(finding),
                _review_status(finding["id"]),
            ]
        )
    ui.data_table(["Finding", "Proposed title", "Defect rate", "Risk", "Evidence check", "Review status"], table)
    settings = state.page("diagnosis")
    by_id = {f["id"]: f for f in findings}
    if settings.get("finding_id") not in by_id:
        settings["finding_id"] = findings[0]["id"]
    chosen = ui.selectbox(
        "Finding to examine",
        list(by_id),
        settings,
        "finding_id",
        key="diag_finding",
        format_func=lambda fid: f"{fid} · {by_id[fid]['title']}",
        dynamic=True,
    )
    return by_id[chosen]


def _finding_detail(finding: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    risk = compliance.requirement_risk(rows, finding.get("source", ""), finding.get("behavior", ""))
    st.markdown(f"### {ui.esc(finding['id'])} · {ui.esc(finding['title'])}")
    st.markdown(
        " ".join(
            [
                ui.risk_badge(risk["level"]),
                _review_status(finding["id"]),
                ui.badge(f"AI confidence: {finding['confidence']}", "violet", ":material/smart_toy:"),
            ]
        )
    )
    facts_column, ai_column = st.columns(2)
    with facts_column:
        with st.container(border=True):
            st.markdown(f"#### Recorded facts\n{ui.FACT_LABEL}")
            if finding.get("metrics_verified"):
                ui.facts(
                    [
                        ("Failed QA requirement", finding["behavior"]),
                        ("Evaluation category", finding.get("section", "")),
                        ("Issue type", risk["category"].label),
                        (
                            "Defect rate",
                            f"{finding['defect_rate_pct']}% ({finding['failed']} of {finding['evaluated']} scored items)",
                        ),
                        ("Employees who missed it", ", ".join(finding.get("agents_failed", [])) or "None"),
                        ("Pattern", finding.get("computed_pattern", "")),
                    ]
                )
            else:
                st.warning(
                    "The analysis named a requirement that does not match the loaded data, so no "
                    "computed figures are shown for it. Treat this finding with caution."
                )
            st.markdown("**Evaluator comments cited as evidence**")
            _evidence_table(finding)
        with st.container(border=True):
            st.markdown(f"#### Operational risk\n{ui.RULE_LABEL}")
            if risk["level"]:
                st.markdown(ui.risk_badge(risk["level"]) + " " + ui.esc(compliance.RISK_DEFINITIONS[risk["level"]]))
                ui.bullets(risk["reasons"])
            else:
                st.caption("No failed record was found for this requirement, so no risk level applies.")
            st.markdown("**Possible privacy or compliance implications**")
            st.markdown(ui.esc(risk["category"].implication))
            st.caption(DISTINCTION + " " + compliance.RISK_DISCLAIMER)
    with ai_column:
        with st.container(border=True):
            st.markdown(f"#### AI interpretation\n{_source_label()}")
            st.markdown("**What the analysis says happened**")
            ui.quote(finding.get("observed"), "No description was returned.")
            st.markdown(f"**Proposed root cause:** {ui.badge(finding['root_cause_type'], 'violet')}")
            ui.quote(finding.get("root_cause"), "No explanation was returned.")
            st.markdown("**Recommended corrective actions**")
            training = "recommended" if finding.get("training_recommended") else "not recommended"
            ui.facts(
                [
                    ("Intervention", finding["recommended_intervention"]),
                    ("Training", training),
                    (
                        "Existing curriculum covers it",
                        f"{finding['curriculum_coverage']}. {finding.get('curriculum_note', '')}".strip(),
                    ),
                ]
            )
            if finding.get("corrective_actions"):
                ui.bullets(finding["corrective_actions"])
            else:
                st.caption("The analysis listed no specific corrective actions.")
        with st.container(border=True):
            st.markdown("#### Evidence limitations and uncertainty")
            ui.bullets(review.evidence_limitations(finding))


def _evidence_table(finding: dict[str, Any]) -> None:
    evidence = finding.get("evidence", [])
    if not evidence:
        st.markdown(ui.insufficient_badge("No evaluator comment was cited for this finding"))
        return
    table = []
    for item in evidence:
        if item["verified"]:
            check = ui.badge("Matched", "green", ":material/check_circle:")
        elif item.get("comment") is None:
            check = ui.badge("Record not found", "red", ":material/cancel:")
        elif not item.get("same_behavior"):
            check = ui.badge("Different requirement", "orange", ":material/warning:")
        else:
            check = ui.badge("Wording not found", "orange", ":material/warning:")
        result = {"No": "Fail", "Yes": "Pass"}.get(item.get("result", ""), "")
        table.append(
            [
                check,
                ui.esc(item["record_id"]),
                ui.result_badge(result) if result else "Unknown",
                ui.esc(item.get("agent", "")),
                ui.esc(item.get("date", "")),
                ui.esc(item["quote"]),
            ]
        )
    st.markdown(ui.md_table(["Check", "Record", "Result", "Employee", "Call date", "Wording cited"], table))
    st.caption(
        "A citation is 'Matched' only when the record exists, belongs to this requirement, and "
        "contains the cited wording. A match does not show that the AI's explanation is right."
    )


def _inspect_evidence(requirement: str) -> None:
    settings = state.page("intake")
    settings["filters"] = {"requirement": requirement}
    settings["filter_nonce"] = settings.get("filter_nonce", 0) + 1
    settings.pop("record_id", None)
    state.goto(state.INTAKE)


def _proceed(finding_id: str) -> None:
    state.page("review")["finding_id"] = finding_id
    state.goto(state.REVIEW)


def _actions(finding: dict[str, Any]) -> None:
    st.subheader("Next")
    inspect, again, proceed = st.columns(3)
    inspect.button(
        "Inspect the evidence in stage 1",
        key="diag_inspect",
        on_click=_inspect_evidence,
        args=(finding.get("behavior", ""),),
        help="Opens the QA records filtered to this requirement.",
        width="stretch",
    )
    proceed.button(
        "Proceed to supervisor review",
        key="diag_proceed",
        on_click=_proceed,
        args=(finding["id"],),
        type="primary",
        icon=":material/arrow_forward:",
        width="stretch",
    )
    mode = state.mode()
    with again:
        if mode != state.LIVE:
            st.button(
                "Request another analysis",
                key="diag_again",
                disabled=True,
                help="Only a live analysis can be requested again. The offline demonstration and a "
                "replayed run always return the same content.",
                width="stretch",
            )
            return
        settings = state.page("diagnosis")
        has_decisions = bool(state.store()["decisions"])
        confirmed = True
        if has_decisions:
            confirmed = ui.checkbox(
                "Discard the decisions recorded on these findings",
                settings,
                "confirm_again",
                key="diag_confirm_again",
            )
        if st.button(
            "Request another analysis",
            key="diag_again",
            disabled=not confirmed,
            help="Sends the same scope to the harness again and replaces these findings.",
            width="stretch",
        ):
            _request_again()


def _request_again() -> None:
    scope = (st.session_state["diagnosis"].get("_meta", {}).get("focus_behavior_ids")) or actions.default_scope()
    max_findings = int(state.page("intake").get("max_findings", 5))
    try:
        with st.status("Asking Amazon Bedrock AgentCore for another diagnosis...", expanded=True) as status:
            diagnosis = actions.run_analysis(scope, max_findings)
            status.update(label="Diagnosis received and validated.", state="complete", expanded=False)
    except actions.ACTION_ERRORS as exc:
        st.error("The analysis did not complete. The previous findings are unchanged. " + ui.esc(str(exc), keep_newlines=True))
        return
    state.page("diagnosis")["confirm_again"] = False
    state.flash("success", f"A new analysis replaced the previous one: {len(diagnosis['findings'])} finding(s).")
    st.rerun()


def _other_notes(diagnosis: dict[str, Any]) -> None:
    not_training = diagnosis.get("not_a_training_problem") or []
    questions = diagnosis.get("open_questions") or []
    if not (not_training or questions):
        return
    left, right = st.columns(2)
    with left:
        if not_training:
            st.markdown(f"**Where training is not the answer** {_source_label()}")
            ui.bullets(not_training)
    with right:
        if questions:
            st.markdown(f"**Open questions for the supervisor** {_source_label()}")
            ui.bullets(questions)
