"""Stage 1: QA intake and evidence review.

Load a dataset, confirm it is complete, look at the scored records and the
evidence behind any one of them, then choose which QA requirements to analyze.

The evidence panel shows only what the dataset contains: the requirement, the
evaluator's score, and the evaluator's comment. Where a dataset supplies a
transcript excerpt, a timestamp, or a policy reference, it is shown; where it
does not, the panel says so. Nothing is filled in to make a record look fuller.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd
import streamlit as st

import get_data
import pipeline
import security_controls
from ui import actions, components as ui, state
from workflow import compliance, intake

_SOURCE_LABELS = {
    state.SYNTHETIC: "Built-in synthetic dataset",
    state.UPLOAD: "Upload a QA file (JSON or CSV)",
    state.PRIVATE: "Authorized local exports",
}
_SUPPORTING_LABELS = {
    "transcript_excerpt": "Transcript excerpt",
    "call_timestamp": "Call timestamp",
    "policy_reference": "Policy reference",
}
ALL_REQUIREMENTS = "All requirements"


def render() -> None:
    ui.page_header(
        state.INTAKE,
        "Load a QA dataset, check that it is complete, review the scored records and the evidence "
        "behind them, then choose which requirements to analyze.",
    )
    _dataset_section()
    dataset = state.data()
    if dataset is None:
        st.error("No QA dataset is loaded. " + ui.esc(st.session_state.get("data_error", "")))
        return
    rows = intake.build_queue(dataset)
    _readiness_section(dataset)
    filtered = _records_section(rows)
    _evidence_section(dataset, filtered)
    _analysis_section(dataset)


# -- dataset -----------------------------------------------------------------------------
def _dataset_section() -> None:
    st.subheader("QA dataset")
    settings = state.page("intake")
    dataset = state.data()
    loaded_kind = dataset["source_kind"] if dataset else state.SYNTHETIC
    options = [state.SYNTHETIC, state.UPLOAD]
    if get_data.private_source_enabled() and state.mode() != state.DEMO:
        options.append(state.PRIVATE)
    settings.setdefault("source_choice", loaded_kind)
    choice = ui.radio(
        "Data source",
        options,
        settings,
        "source_choice",
        key="intake_source",
        format_func=_SOURCE_LABELS.get,
        horizontal=True,
        dynamic=True,
    )
    if dataset:
        totals = dataset["totals"]
        st.caption(
            f"Loaded: {ui.esc(dataset.get('source_label', loaded_kind))}. {totals['line_items']} scored "
            f"items, {totals['agents']} employee(s), call dates {ui.esc(totals['date_range'][0])} to "
            f"{ui.esc(totals['date_range'][1])}."
        )
    replacing = state.has_work()
    if replacing and choice != loaded_kind:
        st.warning("Loading a different dataset clears the current analysis, decisions, and audit trail.")

    if choice == state.SYNTHETIC and loaded_kind != state.SYNTHETIC:
        if st.button("Load the built-in synthetic dataset", key="intake_load_builtin", type="primary"):
            _load(actions.load_builtin, "The built-in synthetic dataset is loaded.", state.SYNTHETIC)
    elif choice == state.PRIVATE and loaded_kind != state.PRIVATE:
        st.caption(
            "Reads the Excel exports in the local data folder. Names are replaced with codes and common "
            "identifier patterns are masked on load. Sending this data to AWS needs a separate setting."
        )
        if st.button("Load authorized local exports", key="intake_load_private", type="primary"):
            _load(actions.load_private, "The local exports are loaded.", state.PRIVATE)
    elif choice == state.UPLOAD:
        _upload_controls(settings)


def _load(loader: Any, message: str, kind: str) -> None:
    try:
        loader()
    except actions.ACTION_ERRORS as exc:
        st.error("The dataset could not be loaded. " + ui.esc(str(exc)))
        return
    state.page("intake")["source_choice"] = kind
    state.flash("success", message)
    st.rerun()


def _upload_controls(settings: dict[str, Any]) -> None:
    st.caption(
        "Accepted: a .json file with a list of records, or a .csv file with one scored item per row. "
        "Required columns: section, agent, call_date, behavior, result. Optional: record_id, team_lead, "
        "comment, score, max_score, transcript_excerpt, call_timestamp, policy_reference."
    )
    st.caption(
        "An uploaded file is validated and held in memory for this session only. Employee and team-lead "
        "names are replaced with codes and common identifier patterns are masked before anything is "
        "shown. Masking is pattern-based and incomplete, so do not upload real protected health "
        "information to this demonstration."
    )
    st.download_button(
        "Download a CSV template",
        intake.UPLOAD_TEMPLATE_CSV,
        file_name="qa_upload_template.csv",
        mime="text/csv",
        key="intake_template",
        icon=":material/download:",
    )
    uploaded = st.file_uploader("QA file", type=["json", "csv"], key="intake_upload")
    if uploaded is None:
        return
    raw = uploaded.getvalue()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    if settings.get("upload_digest") == digest:
        st.caption(f"{ui.esc(uploaded.name)} is the loaded dataset.")
        return
    st.markdown(f"**Selected file:** {ui.esc(uploaded.name)} ({len(raw):,} bytes)")
    if st.button("Validate and load this file", key="intake_load_upload", type="primary"):
        try:
            warnings = actions.load_upload(uploaded.name, raw)
        except intake.UploadError as exc:
            ui.show_problems(exc.problems, "The file was rejected. Nothing was loaded.")
            return
        fresh = state.page("intake")  # loading a dataset starts a new run, so reselect the page state
        fresh["source_choice"] = state.UPLOAD
        fresh["upload_digest"] = digest
        state.flash("success", " ".join([f"{uploaded.name} passed validation and is loaded.", *warnings]))
        st.rerun()


# -- readiness ---------------------------------------------------------------------------
def _readiness_section(dataset: dict[str, Any]) -> None:
    st.subheader("Data readiness")
    checks = intake.data_checks(dataset)
    passed = sum(1 for c in checks if c["ok"])
    if passed == len(checks):
        st.markdown(
            ui.badge(f"{passed} of {len(checks)} data checks passed", "green", ":material/check_circle:")
        )
    else:
        st.markdown(
            ui.badge(f"{len(checks) - passed} of {len(checks)} data checks failed", "red", ":material/cancel:")
        )
    with st.expander("Data checks and privacy handling", expanded=passed != len(checks)):
        st.markdown(
            ui.md_table(
                ["Check", "Result", "Detail"],
                [
                    [
                        ui.esc(c["name"]),
                        ui.badge("Pass", "green", ":material/check_circle:")
                        if c["ok"]
                        else ui.badge("Fail", "red", ":material/cancel:"),
                        ui.esc(c["detail"]),
                    ]
                    for c in checks
                ],
            )
        )
        masked_on_load = dataset.get("scrub_log") or {}
        report = security_controls.mask_report(
            get_data.build_model_payload(dataset), pipeline.known_staff_names(dataset)
        )
        by_rule = ", ".join(f"{count} {label}" for label, count in report["free_text"].items()) or "none"
        ui.facts(
            [
                (
                    "Masked when the data was loaded",
                    ", ".join(f"{count} {label}" for label, count in masked_on_load.items())
                    or "nothing (the data arrived without these patterns)",
                ),
                ("Identifier patterns the pre-model check would still mask in free text", by_rule),
                ("Call-date fields withheld from the model", report["structured_dates"]),
            ]
        )
        st.caption(
            "Before any model call the whole payload and the final prompt are checked again. Pattern "
            "matching reduces exposure; it does not make data de-identified."
        )


# -- records -----------------------------------------------------------------------------
def _clear_filters() -> None:
    settings = state.page("intake")
    settings["filters"] = {}
    settings["filter_nonce"] = settings.get("filter_nonce", 0) + 1


def _records_section(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st.subheader("QA records")
    settings = state.page("intake")
    filters = settings.setdefault("filters", {})
    nonce = settings.setdefault("filter_nonce", 0)
    options = intake.filter_options(rows)
    requirements = [ALL_REQUIREMENTS] + sorted({r["requirement"] for r in rows})

    top = st.columns(4)
    with top[0]:
        employees = ui.multiselect("Employee", options["employees"], filters, "employees", key=f"intake_f_emp_{nonce}")
    with top[1]:
        sections = ui.multiselect(
            "Evaluation category", options["sections"], filters, "sections", key=f"intake_f_sec_{nonce}"
        )
    with top[2]:
        issue_types = ui.multiselect(
            "Issue type", options["issue_types"], filters, "issue_types", key=f"intake_f_issue_{nonce}"
        )
    with top[3]:
        risk_levels = ui.multiselect(
            "Risk level", options["risk_levels"], filters, "risk_levels", key=f"intake_f_risk_{nonce}"
        )
    bottom = st.columns(4)
    with bottom[0]:
        results = ui.multiselect("Result", options["results"], filters, "results", key=f"intake_f_res_{nonce}")
    date_from = date_to = None
    if options["date_min"] and options["date_max"]:
        with bottom[1]:
            date_from = ui.date_input(
                "Call date from", filters, "date_from", key=f"intake_f_from_{nonce}",
                default=options["date_min"], min_value=options["date_min"], max_value=options["date_max"],
            )
        with bottom[2]:
            date_to = ui.date_input(
                "Call date to", filters, "date_to", key=f"intake_f_to_{nonce}",
                default=options["date_max"], min_value=options["date_min"], max_value=options["date_max"],
            )
    with bottom[3]:
        requirement = ui.selectbox(
            "QA requirement", requirements, filters, "requirement", key=f"intake_f_req_{nonce}", dynamic=True
        )
    flagged_only = ui.checkbox(
        "Only items flagged for compliance review", filters, "flagged_only", key=f"intake_f_flag_{nonce}"
    )

    filtered = intake.filter_queue(
        rows,
        employees=employees,
        sections=sections,
        issue_types=issue_types,
        risk_levels=risk_levels,
        results=results,
        date_from=date_from,
        date_to=date_to,
        flagged_only=flagged_only,
        requirement="" if requirement == ALL_REQUIREMENTS else requirement,
    )
    failed = sum(1 for r in filtered if r["result"] == "Fail")
    summary, clear = st.columns([4, 1], vertical_alignment="center")
    summary.caption(
        f"Showing {len(filtered)} of {len(rows)} records: {failed} failed, {len(filtered) - failed} passed. "
        "Risk levels are rule-based triage, shown for failed items only."
    )
    clear.button("Clear filters", key="intake_clear_filters", on_click=_clear_filters)
    if not filtered:
        st.info("No records match these filters.")
        return filtered
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Result": ui.RESULT_TEXT.get(r["result"], r["result"]),
                    "Record": r["record_id"],
                    "Employee": r["employee"],
                    "Call date": r["call_date"],
                    "Category": r["section"],
                    "QA requirement": r["requirement"],
                    "Issue type": r["issue_type"],
                    "Risk": r["risk_level"] or "",
                    "Repeat": f"{r['repeat_count']} misses" if r["repeat_count"] >= compliance.REPEAT_THRESHOLD else "",
                    "Flagged": "Yes" if r["flagged"] else "",
                    "Evaluator comment": r["comment"],
                }
                for r in filtered
            ]
        ),
        hide_index=True,
        column_config={
            "QA requirement": st.column_config.TextColumn("QA requirement", width="large"),
            "Evaluator comment": st.column_config.TextColumn("Evaluator comment", width="large"),
        },
    )
    return filtered


# -- evidence ----------------------------------------------------------------------------
def _open_flag(record_id: str) -> None:
    state.page("compliance")["record_id"] = record_id
    state.goto(state.COMPLIANCE)


def _scope_to(behavior_id: str) -> None:
    settings = state.page("intake")
    settings["scope"] = [behavior_id]
    settings["scope_nonce"] = settings.get("scope_nonce", 0) + 1


def _record_label(rows: dict[str, dict[str, Any]], record_id: str) -> str:
    row = rows[record_id]
    return (
        f"{record_id} · {ui.RESULT_TEXT.get(row['result'], row['result'])} · {row['employee']} · "
        f"{row['call_date']} · {row['requirement']}"
    )


def _history_table(rows: list[dict[str, Any]]) -> str:
    return ui.md_table(
        ["Record", "Employee", "Call date", "Result", "Evaluator comment"],
        [
            [
                ui.esc(r["record_id"]),
                ui.esc(r["employee"]),
                ui.esc(r["call_date"]),
                ui.result_badge(r["result"]),
                ui.esc(r["comment"]) or "No comment",
            ]
            for r in rows
        ],
    )


def _evidence_section(dataset: dict[str, Any], filtered: list[dict[str, Any]]) -> None:
    st.subheader("Evidence for one record")
    if not filtered:
        st.caption("Adjust the filters to choose a record.")
        return
    settings = state.page("intake")
    by_id = {r["record_id"]: r for r in filtered}
    ids = list(by_id)
    first_failed = next((r["record_id"] for r in filtered if r["result"] == "Fail"), ids[0])
    if settings.get("record_id") not in by_id:
        settings["record_id"] = first_failed
    record_id = ui.selectbox(
        "Record to inspect",
        ids,
        settings,
        "record_id",
        key="intake_record",
        format_func=lambda rid: _record_label(by_id, rid),
        dynamic=True,
    )
    evidence = intake.evidence_for(dataset, record_id)
    if evidence is None:
        st.error("That record is no longer in the dataset.")
        return
    row = evidence["row"]
    failed = row["result"] == "Fail"

    requirement_column, observed_column = st.columns(2)
    with requirement_column:
        with st.container(border=True):
            st.markdown(f"**QA requirement** {ui.FACT_LABEL}")
            st.markdown(f"#### {ui.esc(row['requirement'])}")
            ui.facts(
                [
                    ("Evaluation category", row["section"]),
                    ("What this category measures", evidence["section_description"] or "Not described"),
                    ("Issue type", row["issue_type"]),
                ]
            )
            st.markdown("**Scoring guidance supplied with the dataset**")
            ui.quote(evidence["requirement_text"], "No scoring guidance was supplied with this dataset.")
    with observed_column:
        with st.container(border=True):
            st.markdown(f"**What the evaluator recorded** {ui.FACT_LABEL}")
            badges = [ui.result_badge(row["result"])]
            if failed:
                badges.append(ui.risk_badge(row["risk_level"]))
            st.markdown(" ".join(badges))
            ui.facts(
                [
                    ("Record", row["record_id"]),
                    ("Employee", row["employee"]),
                    ("Team lead", row["team_lead"] or "Not recorded"),
                    ("Call date", row["call_date"]),
                    ("Score", f"{row['score']} of {row['max_score']}"),
                ]
            )
            st.markdown("**Evaluator comment**")
            ui.quote(row["comment"], "The evaluator left no comment on this item.")

    if failed:
        with st.container(border=True):
            st.markdown(f"**Why this risk level** {ui.RULE_LABEL}")
            ui.bullets(row["risk_reasons"])
            st.caption(compliance.RISK_DEFINITIONS[row["risk_level"]] + " " + compliance.RISK_DISCLAIMER)
            flag = state.store()["flags"].get(record_id)
            if flag:
                left, right = st.columns([3, 1], vertical_alignment="center")
                left.markdown("Compliance flag: " + ui.flag_status_badge(flag["status"]))
                right.button(
                    "Open in Compliance Review",
                    key="intake_open_flag",
                    on_click=_open_flag,
                    args=(record_id,),
                )

    st.markdown("**Supporting evidence supplied with this record**")
    st.markdown(
        ui.md_table(
            ["Evidence", "Content"],
            [
                [label, ui.esc(evidence["supporting"][field]) or "Not supplied with this dataset"]
                for field, label in _SUPPORTING_LABELS.items()
            ],
        )
    )
    st.caption(
        "This application reads scored QA items and evaluator comments. It has no call recordings or "
        "transcripts unless a dataset supplies them, and it never generates any."
    )

    with st.expander(f"History for {row['employee']} on this requirement ({len(evidence['employee_history'])})"):
        st.markdown(_history_table(evidence["employee_history"]))
    with st.expander(f"Other employees on this requirement ({len(evidence['other_employees'])})"):
        if evidence["other_employees"]:
            st.markdown(_history_table(evidence["other_employees"]))
        else:
            st.caption("No other employee was evaluated on this requirement.")

    behavior = next(
        (
            b
            for b in intake.failed_requirements(dataset)
            if b["source"] == row["source"] and b["behavior"] == row["requirement"]
        ),
        None,
    )
    if behavior:
        st.button(
            "Investigate only this requirement",
            key="intake_scope_one",
            on_click=_scope_to,
            args=(behavior["id"],),
            help="Limits the analysis below to this QA requirement.",
        )


# -- analysis ----------------------------------------------------------------------------
_RUN_LABELS = {
    state.DEMO: "Load the offline demonstration analysis",
    state.LIVE: "Run the AI diagnosis with Amazon Bedrock AgentCore",
    state.REPLAY: "Load the saved run",
}
_RUN_NOTES = {
    state.DEMO: "Loads handwritten findings for the built-in dataset. No AWS call is made.",
    state.LIVE: "Sends the masked metrics and evaluator comments for the selected requirements to the "
    "configured harness in one call.",
    state.REPLAY: "Loads the saved diagnosis and coaching packages as they were. Nothing new is generated.",
}


def _analysis_section(dataset: dict[str, Any]) -> None:
    st.subheader("Start the analysis")
    settings = state.page("intake")
    failed = intake.failed_requirements(dataset)
    by_id = {b["id"]: b for b in failed}
    if not failed:
        st.info("This dataset has no failed items, so there is nothing to investigate.")
        return
    settings.setdefault("scope", list(by_id))
    scope = ui.multiselect(
        "QA requirements to investigate",
        list(by_id),
        settings,
        "scope",
        key=f"intake_scope_{settings.setdefault('scope_nonce', 0)}",
        format_func=lambda bid: (
            f"{bid} · {by_id[bid]['behavior']} "
            f"({by_id[bid]['defect_rate_pct']}%, {by_id[bid]['failed']} of {by_id[bid]['evaluated']})"
        ),
        dynamic=True,
    )
    mode = state.mode()
    max_findings = 5
    if mode == state.LIVE:
        max_findings = int(
            ui.number_input(
                "Maximum findings to request", settings, "max_findings", key="intake_max_findings",
                default=5, min_value=1, max_value=6, step=1,
            )
        )
    st.caption(_RUN_NOTES[mode])

    blocking = [c for c in actions.analysis_checks(scope) if not c["ok"]]
    if blocking:
        ui.show_problems(
            [f"{c['name']}: {c['detail']}" for c in blocking], "The analysis cannot start yet:"
        )
    allowed = ui.role_allows("run_analysis", "start the analysis") if mode != state.REPLAY else True
    diagnosis = st.session_state["diagnosis"]
    confirmed = True
    if diagnosis:
        st.success(
            f"An analysis is loaded: {len(diagnosis['findings'])} finding(s) from "
            f"{ui.esc(state.MODE_LABELS[mode].lower())}."
        )
        ui.goto_button("Open Diagnosis & Risk", state.DIAGNOSIS, key="intake_to_diagnosis")
        if state.store()["decisions"]:
            confirmed = ui.checkbox(
                "Replace the current findings and clear the decisions recorded on them",
                settings,
                "confirm_rerun",
                key="intake_confirm_rerun",
            )
    label = _RUN_LABELS[mode] if not diagnosis else "Run the analysis again"
    if st.button(
        label,
        key="intake_run",
        type="secondary" if diagnosis else "primary",
        disabled=bool(blocking) or not allowed or not confirmed,
    ):
        _run(scope, max_findings)


def _run(scope: list[str], max_findings: int) -> None:
    try:
        if state.mode() == state.LIVE:
            with st.status("Asking Amazon Bedrock AgentCore for the diagnosis...", expanded=True) as status:
                progress = st.empty()
                received = [0]

                def on_text(chunk: str) -> None:
                    received[0] += len(chunk)
                    if received[0] % 400 < len(chunk):  # refresh about every 400 characters
                        progress.caption(f"Receiving the diagnosis: {received[0]:,} characters")

                diagnosis = actions.run_analysis(scope, max_findings, on_text)
                status.update(label="Diagnosis received and validated.", state="complete", expanded=False)
        else:
            diagnosis = actions.run_analysis(scope, max_findings)
    except actions.ACTION_ERRORS as exc:
        st.error("The analysis did not complete. " + ui.esc(str(exc), keep_newlines=True))
        return
    state.page("intake")["confirm_rerun"] = False
    state.flash(
        "success",
        f"Analysis loaded: {len(diagnosis['findings'])} finding(s). Review each one below, then record "
        "a supervisor decision.",
    )
    state.request_nav(state.DIAGNOSIS)
    st.rerun()
