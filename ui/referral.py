"""Administrative referral: a supervisor-written record for authorized HR review.

This workspace is a simulation and says so on every screen. No HR system is
connected, nothing is sent anywhere, referrals are never written to disk, and
they are left out of every export except the one on this page. The application
shows recorded QA facts about the employee; it does not suggest an action.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import streamlit as st

from ui import components as ui, state
from workflow import analytics, intake, operations, referrals
from workflow.common import WorkflowError, can


def render() -> None:
    ui.page_header(
        state.REFERRAL,
        "For serious or repeated failures a supervisor can write a referral for authorized HR review. "
        "Coaching comes first; this is the exception, and it is written by a person.",
    )
    st.warning(referrals.SIMULATION_NOTICE)
    dataset = state.data()
    if dataset is None:
        st.error("No QA dataset is loaded.")
        return
    store = state.store()
    rows = intake.build_queue(dataset)
    form_column, list_column = st.columns(2)
    with form_column:
        _form(dataset, rows, store)
    with list_column:
        _existing(store)


def _case_label(case_id: str, store: dict[str, Any]) -> str:
    finding = next((f for f in state.findings() if f["id"] == case_id), None)
    if finding:
        return f"{case_id} · finding · {finding['title']}"
    flag = store["flags"].get(case_id, {})
    return f"{case_id} · QA record · {flag.get('employee', '')} · {flag.get('requirement', '')}"


def _form(dataset: dict[str, Any], rows: list[dict[str, Any]], store: dict[str, Any]) -> None:
    settings = state.page("referral")
    nonce = settings.setdefault("nonce", 0)
    form = settings.setdefault("form", {})
    name, role = state.actor()
    employees = sorted({r["agent"] for r in dataset["records"]})
    cases = operations.case_ids(st.session_state["diagnosis"], store)
    prefix = f"ref_{nonce}"
    with st.container(border=True):
        st.markdown(f"#### New referral {ui.HUMAN_LABEL}")
        allowed = ui.role_allows("create_referral", "write a referral")
        employee = ui.selectbox(
            "Employee (synthetic identifier)", employees, form, "employee", key=f"{prefix}_employee",
            dynamic=True, disabled=not allowed,
        )
        context = analytics.employee_context(rows, employee) if employee else None
        if context:
            repeats = "; ".join(f"{r['requirement']} ({r['times']} misses)" for r in context["repeat_findings"])
            st.caption(
                f"Recorded QA facts for {ui.esc(employee)}: {context['failed']} of {context['evaluated']} "
                f"scored items failed ({ui.esc(', '.join(context['failed_records']) or 'none')}). "
                f"Repeat findings: {ui.esc(repeats or 'none')}. These are facts from the data. The "
                "application does not recommend an action."
            )
        case_labels = {case_id: _case_label(case_id, store) for case_id in cases}
        ui.selectbox(
            "QA case this referral is based on", cases, form, "case_id", key=f"{prefix}_case",
            format_func=case_labels.get, dynamic=True, disabled=not allowed,
        )
        action_key = ui.selectbox(
            "Proposed action", list(referrals.ACTIONS), form, "action", key=f"{prefix}_action",
            format_func=lambda key: referrals.ACTIONS[key].label, disabled=not allowed,
        )
        action = referrals.ACTIONS[action_key]
        st.caption(action.description)
        ui.text_area(
            "Supervisor justification (required)", form, "justification", key=f"{prefix}_justification",
            height=120, max_chars=2000, disabled=not allowed,
        )
        today = dt.date.today()
        reviewed = ui.date_input(
            "Date of the supervisor's review", form, "review_date_value", key=f"{prefix}_date",
            default=today, max_value=today, disabled=not allowed,
        )
        form["review_date"] = reviewed.isoformat()
        form.setdefault("approvals", [])
        ui.multiselect(
            "Additional approvals required", list(referrals.APPROVERS), form, "approvals",
            key=f"{prefix}_approvals", disabled=not allowed,
        )
        minimum = ", ".join(action.minimum_approvals) or "none"
        st.caption(f"Minimum for '{ui.esc(action.label)}': {ui.esc(minimum)}.")
        ui.text_input(
            "Witness to the conversation, if there was one", form, "witness_name", key=f"{prefix}_witness",
            max_chars=120, disabled=not allowed,
        )
        ui.checkbox(
            "The witness has acknowledged this record", form, "witness_acknowledged",
            key=f"{prefix}_witness_ack", disabled=not allowed,
        )
        ui.checkbox(referrals.ATTESTATION_TEXT, form, "attested", key=f"{prefix}_attest", disabled=not allowed)
        draft_column, submit_column = st.columns(2)
        save_draft = draft_column.button("Save as draft", key=f"{prefix}_draft", disabled=not allowed, width="stretch")
        submit = submit_column.button(
            "Mark for HR review (simulated)", key=f"{prefix}_submit", type="primary",
            disabled=not allowed, width="stretch",
        )
        if save_draft or submit:
            try:
                referral = operations.create_referral(
                    store, form, actor=name, role=role, employees=employees, case_ids=cases, submitting=submit
                )
            except WorkflowError as exc:
                ui.show_problems([str(exc)], "The referral was not saved:")
                return
            settings["form"] = {}
            settings["nonce"] = nonce + 1
            settings["selected"] = referral["id"]
            state.flash(
                "success",
                f"{referral['id']} saved: {referrals.STATUS_LABELS[referral['status']]}. Nothing was sent.",
            )
            st.rerun()


def _existing(store: dict[str, Any]) -> None:
    settings = state.page("referral")
    name, role = state.actor()
    with st.container(border=True):
        st.markdown("#### Referrals in this session")
        if not store["referrals"]:
            st.caption("No referral has been written.")
            return
        st.markdown(
            ui.md_table(
                ["Referral", "Employee", "Case", "Proposed action", "Status"],
                [
                    [
                        ui.esc(r["id"]),
                        ui.esc(r["employee"]),
                        ui.esc(r["case_id"]),
                        ui.esc(r["action_label"]),
                        ui.esc(referrals.STATUS_LABELS[r["status"]]),
                    ]
                    for r in store["referrals"]
                ],
            )
        )
        by_id = {r["id"]: r for r in store["referrals"]}
        if settings.get("selected") not in by_id:
            settings["selected"] = store["referrals"][-1]["id"]
        chosen = ui.selectbox("Referral to open", list(by_id), settings, "selected", key="ref_selected", dynamic=True)
        referral = by_id[chosen]
        ui.facts(
            [
                ("Status", referrals.STATUS_LABELS[referral["status"]]),
                ("Written by", f"{referral['created_by']} at {referral['created_at']} UTC"),
                ("Date of review", referral["review_date"]),
                ("Approvals required", ", ".join(referral["approvals_required"]) or "None"),
                (
                    "Witness",
                    (
                        f"{referral['witness_name']} "
                        f"({'acknowledged' if referral['witness_acknowledged'] else 'not yet acknowledged'})"
                    )
                    if referral["witness_name"]
                    else "None recorded",
                ),
            ]
        )
        st.markdown("**Supervisor justification**")
        ui.quote(referral["justification"])
        if state.read_only() or not can(role, "create_referral"):
            st.caption("Only the Supervisor role can change a referral, and not in a replayed run.")
        else:
            drafts = settings.setdefault("status_drafts", {})
            draft = drafts.setdefault(chosen, {})
            prefix = f"ref_status_{chosen}_{len(referral['history'])}"
            if referral["status"] == referrals.DRAFT:
                attested = ui.checkbox(referrals.ATTESTATION_TEXT, draft, "attested", key=f"{prefix}_attest")
                if st.button("Mark for HR review (simulated)", key=f"{prefix}_submit"):
                    _change(store, chosen, referrals.SUBMITTED, name, role, "", attested)
            if referral["status"] in (referrals.DRAFT, referrals.SUBMITTED):
                ui.text_input("Reason for withdrawing", draft, "note", key=f"{prefix}_note", max_chars=300)
                if st.button("Withdraw this referral", key=f"{prefix}_withdraw"):
                    _change(store, chosen, referrals.WITHDRAWN, name, role, draft.get("note", ""), False)
        st.download_button(
            "Download referrals (.json, simulated)",
            json.dumps({"notice": referrals.SIMULATION_NOTICE, "referrals": store["referrals"]}, indent=2, default=str),
            file_name="simulated_referrals.json",
            mime="application/json",
            key="ref_download",
            icon=":material/download:",
        )
        st.caption(
            "Referrals exist only in this browser session. They are not saved to disk, not included in "
            "the run export, and are lost when the session is reset."
        )


def _change(
    store: dict[str, Any], referral_id: str, status: str, name: str, role: str, note: str, attested: bool
) -> None:
    try:
        updated = operations.change_referral_status(
            store, referral_id, status, actor=name, role=role, note=note, attested=attested
        )
    except WorkflowError as exc:
        ui.show_problems([str(exc)], "The referral was not changed:")
        return
    state.flash("success", f"{referral_id}: {referrals.STATUS_LABELS[updated['status']]}.")
    st.rerun()
