"""Stage 3: supervisor review and decision.

The supervisor decides what happens to each AI-proposed finding: approve it
(with or without corrections), reject it, or ask for more evidence; confirm or
dismiss the underlying QA flag; refer a suspected privacy incident; and choose
the next action. Rules live in ``workflow.review``; this page collects the
input, explains what is still missing, and shows the recorded decision next to
the AI's original proposal.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import pipeline
from ui import actions, components as ui, state
from workflow import compliance, intake, operations, review
from workflow.common import WorkflowError


def render() -> None:
    ui.page_header(
        state.REVIEW,
        "Decide what happens to each proposed finding. The AI drafts; the supervisor decides. Every "
        "decision is recorded with the AI's original proposal beside it.",
    )
    diagnosis = st.session_state["diagnosis"]
    dataset = state.data()
    if not diagnosis or dataset is None:
        ui.empty_state(
            "There are no findings to review yet. Start the analysis in stage 1.",
            "Open intake",
            state.INTAKE,
            key="review_to_intake",
        )
        return
    store = state.store()
    legacy = st.session_state.get("legacy_approval")
    if legacy:
        st.info(
            "This saved run was recorded before per-finding decisions existed. Its approval record: "
            f"approved by {ui.esc(legacy.get('approved_by', 'unknown'))} at "
            f"{ui.esc(legacy.get('approved_at', 'unknown'))} for findings "
            f"{ui.esc(', '.join(legacy.get('approved_findings', [])))}."
        )
    progress = review.progress(diagnosis, store["decisions"])
    st.progress(
        progress["final"] / progress["findings"] if progress["findings"] else 0.0,
        text=f"{progress['final']} of {progress['findings']} findings have a final decision",
    )
    finding = _pick_finding(diagnosis, store)
    record = store["decisions"].get(finding["id"])
    rows = intake.build_queue(dataset)

    proposal_column, decision_column = st.columns(2)
    with proposal_column:
        _proposal(finding, rows, store)
    with decision_column:
        _decision_form(finding, record, store)
    _related_flags(finding, store)
    if record:
        _recorded_decision(finding, record)
    if progress["final"] == progress["findings"] and progress["coaching_ready"]:
        ui.goto_button("Proceed to coaching and practice", state.COACHING, key="review_to_coaching", primary=True)


def _status_text(record: dict[str, Any] | None) -> str:
    return record["outcome_label"] if record else "awaiting decision"


def _pick_finding(diagnosis: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    settings = state.page("review")
    by_id = {f["id"]: f for f in diagnosis["findings"]}
    if settings.get("finding_id") not in by_id:
        undecided = [fid for fid in by_id if not review.is_final(store["decisions"].get(fid))]
        settings["finding_id"] = (undecided or list(by_id))[0]
    chosen = ui.selectbox(
        "Finding under review",
        list(by_id),
        settings,
        "finding_id",
        key=f"review_finding_{settings.setdefault('finding_nonce', 0)}",
        format_func=lambda fid: f"{fid} · {by_id[fid]['title']} ({_status_text(store['decisions'].get(fid))})",
        dynamic=True,
    )
    return by_id[chosen]


def _proposal(finding: dict[str, Any], rows: list[dict[str, Any]], store: dict[str, Any]) -> None:
    label = ui.DEMO_LABEL if state.mode() == state.DEMO else ui.AI_LABEL
    risk = compliance.requirement_risk(rows, finding.get("source", ""), finding.get("behavior", ""))
    with st.container(border=True):
        st.markdown(f"#### Proposal under review\n{label}")
        st.markdown(f"**{ui.esc(finding['id'])} · {ui.esc(finding['title'])}** {ui.risk_badge(risk['level'])}")
        ui.facts(
            [
                ("QA requirement", finding.get("behavior", "")),
                (
                    "Defect rate",
                    f"{finding.get('defect_rate_pct', '?')}% ({finding.get('failed', '?')} of "
                    f"{finding.get('evaluated', '?')} scored items)",
                ),
                ("Evidence check", f"{finding['evidence_verified']} of {finding['evidence_total']} cited comments matched"),
                ("Proposed root cause", f"{finding['root_cause_type']}: {finding['root_cause']}"),
                ("Recommended intervention", finding["recommended_intervention"]),
                ("AI confidence", f"{finding['confidence']}. {finding.get('confidence_note', '')}".strip()),
                ("AI suggested next action", review.NEXT_ACTIONS[review.suggested_next_action(finding)]),
            ]
        )
        ui.goto_button("See the full diagnosis and evidence", state.DIAGNOSIS, key="review_to_diagnosis")


def _related_flags(finding: dict[str, Any], store: dict[str, Any]) -> None:
    """Full width, because the table carries badges and is wider than a half-width column."""
    flags = operations.related_flags(store, finding)
    with st.container(border=True):
        st.markdown("#### Related compliance flags")
        if not flags:
            st.caption(
                "The failed records behind this finding are not in the compliance queue. It is a "
                "coaching matter unless the supervisor decides otherwise."
            )
            return
        st.markdown(
            ui.md_table(
                ["Record", "Employee", "Risk", "Flag status"],
                [
                    [
                        ui.esc(flag["record_id"]),
                        ui.esc(flag["employee"]),
                        ui.risk_badge(compliance.effective_risk(flag)),
                        ui.flag_status_badge(flag["status"]),
                    ]
                    for flag in flags
                ],
            )
        )
        st.caption(
            "Confirming the control marks these flags 'QA failure confirmed'. Dismissing it marks them "
            "dismissed. A referral sends them to compliance review as suspected, not confirmed."
        )


def _decision_form(finding: dict[str, Any], record: dict[str, Any] | None, store: dict[str, Any]) -> None:
    finding_id = finding["id"]
    revision = record["revision"] if record else 0
    drafts = st.session_state["drafts"]
    if finding_id not in drafts:
        drafts[finding_id] = review.draft_from_record(finding, record) if record else review.new_draft(finding)
    draft = drafts[finding_id]
    # Keys carry the revision so the inputs start fresh after a decision is recorded.
    prefix = f"review_{finding_id}_{revision}"
    name, role = state.actor()

    with st.container(border=True):
        st.markdown(f"#### {'Revise the decision' if record else 'Supervisor decision'}\n{ui.HUMAN_LABEL}")
        allowed = ui.role_allows("decide_finding", "record a supervisor decision")
        decision = ui.radio(
            "Decision on the diagnosis",
            list(review.DECISION_OPTIONS),
            draft,
            "decision",
            key=f"{prefix}_decision",
            format_func=review.DECISION_OPTIONS.get,
            allow_empty=True,
            disabled=not allowed,
        )
        draft["decision"] = decision or ""
        ui.radio(
            "Was the QA control missed?",
            list(review.CONTROL_OPTIONS),
            draft,
            "control",
            key=f"{prefix}_control",
            format_func=review.CONTROL_OPTIONS.get,
            disabled=not allowed,
        )

        if decision == "approve":
            with st.expander("Correct the finding before approving", expanded=bool(review.changes(finding, draft))):
                ui.text_input("Finding title", draft, "title", key=f"{prefix}_title", max_chars=160, disabled=not allowed)
                ui.selectbox(
                    "Root cause type", pipeline.ROOT_CAUSE_TYPES, draft, "root_cause_type",
                    key=f"{prefix}_type", disabled=not allowed,
                )
                ui.text_area(
                    "Root cause explanation", draft, "root_cause", key=f"{prefix}_cause", height=120,
                    max_chars=1500, disabled=not allowed,
                )
                ui.selectbox(
                    "Recommended intervention", pipeline.INTERVENTIONS, draft, "recommended_intervention",
                    key=f"{prefix}_intervention", disabled=not allowed,
                )
                ui.selectbox(
                    "Existing curriculum covers it", pipeline.COVERAGE, draft, "curriculum_coverage",
                    key=f"{prefix}_coverage", disabled=not allowed,
                )

        if decision in ("approve", "reject"):
            choices = (
                list(review.NEXT_ACTIONS)
                if decision == "approve"
                else ["close", "follow_up", "escalate_operations"]
            )
            ui.selectbox(
                "Next action",
                choices,
                draft,
                "next_action",
                key=f"{prefix}_next",
                format_func=review.NEXT_ACTIONS.get,
                dynamic=True,
                disabled=not allowed,
            )
            st.caption("AI suggested: " + review.NEXT_ACTIONS[review.suggested_next_action(finding)] + ".")
        elif decision == "request_evidence":
            st.caption("The finding stays open until the evidence arrives and a decision is recorded.")

        if operations.related_flags(store, finding):
            referring = ui.checkbox(
                "Refer the related records as a suspected privacy incident",
                draft,
                "privacy_referral",
                key=f"{prefix}_refer",
                help="Sends the related flags to compliance review. It records a suspicion, not a finding.",
                disabled=not allowed,
            )
            if referring:
                ui.checkbox(compliance.ATTESTATION_TEXT, draft, "attested", key=f"{prefix}_attest", disabled=not allowed)
        else:
            draft["privacy_referral"] = False

        reasons = review.note_reasons(finding, draft)
        ui.text_area(
            "Supervisor note" + (" (required)" if reasons else " (optional)"),
            draft,
            "note",
            key=f"{prefix}_note",
            height=110,
            max_chars=1500,
            disabled=not allowed,
        )
        if reasons:
            st.caption("A note is required because " + ui.esc("; ".join(reasons)) + ".")

        if st.button(
            "Record revised decision" if record else "Record decision",
            key=f"{prefix}_record",
            type="primary",
            disabled=not allowed,
        ):
            problems = review.validate(finding, draft, actor=name, role=role)
            if problems:
                ui.show_problems(problems)
                return
            try:
                outcome = operations.record_decision(store, finding, draft, actor=name, role=role)
            except WorkflowError as exc:
                ui.show_problems([str(exc)])
                return
            drafts.pop(finding_id, None)
            actions.persist_review()
            _advance(store)
            updated = len({change["record_id"] for change in outcome["flags_updated"]})
            state.flash(
                "success",
                f"Decision recorded for {finding_id}: {outcome['record']['outcome_label']}. "
                f"{updated} related flag(s) updated.",
            )
            st.rerun()


def _advance(store: dict[str, Any]) -> None:
    """After a decision, show the next finding that still needs one (if any)."""
    settings = state.page("review")
    remaining = [f["id"] for f in state.findings() if not review.is_final(store["decisions"].get(f["id"]))]
    if remaining:
        settings["finding_id"] = remaining[0]
    # A new key makes the selector take its value from the stored choice again.
    settings["finding_nonce"] = settings.get("finding_nonce", 0) + 1


def _recorded_decision(finding: dict[str, Any], record: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(f"#### Recorded decision for {ui.esc(finding['id'])}\n{ui.HUMAN_LABEL}")
        ui.facts(
            [
                ("Outcome", record["outcome_label"]),
                ("QA control", review.CONTROL_OPTIONS.get(record["control"], record["control"])),
                (
                    "Next action",
                    review.NEXT_ACTIONS.get(record["next_action"], "Waiting for additional evidence"),
                ),
                ("Suspected privacy incident referred", "Yes" if record["privacy_referral"] else "No"),
                ("Recorded by", f"{record['decided_by']} ({record['role']}) at {record['decided_at']} UTC"),
                ("Revision", record["revision"]),
            ]
        )
        st.markdown("**Supervisor note**")
        ui.quote(record["note"], "No note was recorded.")
        changed = {change["field"] for change in record["changes"]}
        rows = []
        for field in review.EDITABLE_FIELDS:
            rows.append(
                [
                    review.FIELD_LABELS[field],
                    ui.esc(record["ai_original"].get(field)),
                    ui.esc(record["final"].get(field)),
                    ui.badge("Changed", "orange", ":material/edit:") if field in changed else "Unchanged",
                ]
            )
        decided = review.NEXT_ACTIONS.get(record["next_action"], "Waiting for additional evidence")
        suggested = review.NEXT_ACTIONS[record["ai_suggested_next_action"]]
        rows.append(
            [
                "Next action",
                ui.esc(suggested),
                ui.esc(decided),
                "Unchanged" if decided == suggested else ui.badge("Changed", "orange", ":material/edit:"),
            ]
        )
        st.markdown("**AI proposal and supervisor decision, side by side**")
        st.markdown(ui.md_table(["Field", "AI proposed", "Supervisor decided", "Difference"], rows))
        st.caption(
            "The AI's original values are kept with every decision and in the audit trail. Recording a "
            "new decision adds a revision; it does not overwrite the history."
        )
