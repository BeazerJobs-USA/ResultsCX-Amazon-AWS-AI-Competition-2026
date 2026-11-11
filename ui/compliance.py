"""Compliance review: flags raised on failed QA items, and what a person decided about each.

Flags are opened by fixed rules when the data is loaded, never by the AI. A
supervisor can confirm the QA failure, dismiss the flag, ask for evidence, or
refer it as a suspected privacy incident. Only the compliance-reviewer role can
record the outcome of a referral. Every action needs the right role, most need
a written reason, and all of them are kept in the flag's history.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import streamlit as st

from ui import actions, components as ui, state
from workflow import audit, compliance, operations
from workflow.common import NOTE_HINT, WorkflowError, can

DISTINCTION = (
    "**Three different things.** A *QA failure* is what the evaluator scored. A *suspected privacy "
    "incident* is a supervisor's referral for closer review. A *compliance finding* can only be "
    "recorded by the compliance-reviewer role after a referral. A missed verification step is a QA "
    "failure; it is not, by itself, a privacy incident or a HIPAA violation."
)


def render() -> None:
    ui.page_header(
        state.COMPLIANCE,
        "Failed QA items that need a person's judgment, with the reason each was flagged, its triage "
        "risk level, and the review history.",
    )
    st.info(DISTINCTION + " " + compliance.RISK_DISCLAIMER)
    if state.data() is None:
        st.error("No QA dataset is loaded.")
        return
    store = state.store()
    flags = store["flags"]
    if not flags:
        st.success("No failed item in this dataset met the rules for compliance review.")
        return
    summary = compliance.summarize_flags(flags)
    metrics = st.columns(4)
    metrics[0].metric("Flags", summary["total"], border=True)
    metrics[1].metric("Needing review", summary["needs_attention"], help="Open, waiting for evidence, or referred.", border=True)
    metrics[2].metric("Referred as suspected privacy incidents", summary["referred"], border=True)
    metrics[3].metric(
        "High or critical risk",
        summary["by_risk"][compliance.HIGH] + summary["by_risk"][compliance.CRITICAL],
        border=True,
    )
    visible = _queue(flags)
    if not visible:
        st.info("No flags match these filters.")
        return
    flag = _pick(visible)
    detail, action = st.columns(2)
    with detail:
        _detail(flag)
    with action:
        _action_panel(flag, store)
    _history(flag, store)


def _queue(flags: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    st.subheader("Review queue")
    filters = state.page("compliance").setdefault("filters", {})
    statuses = [s for s in compliance.STATUS_LABELS if any(f["status"] == s for f in flags.values())]
    categories = sorted({f["category_label"] for f in flags.values()})
    columns = st.columns(3)
    with columns[0]:
        chosen_status = ui.multiselect(
            "Status", statuses, filters, "statuses", key="comp_f_status",
            format_func=compliance.STATUS_LABELS.get, dynamic=True,
        )
    with columns[1]:
        chosen_risk = ui.multiselect("Risk level", list(compliance.RISK_LEVELS), filters, "risks", key="comp_f_risk")
    with columns[2]:
        chosen_category = ui.multiselect(
            "Issue category", categories, filters, "categories", key="comp_f_category", dynamic=True
        )
    visible = [
        flag
        for flag in flags.values()
        if (not chosen_status or flag["status"] in chosen_status)
        and (not chosen_risk or compliance.effective_risk(flag) in chosen_risk)
        and (not chosen_category or flag["category_label"] in chosen_category)
    ]
    visible.sort(key=lambda f: (-compliance.risk_rank(compliance.effective_risk(f)), f["record_id"]))
    if visible:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Record": f["record_id"],
                        "Employee": f["employee"],
                        "Call date": f["call_date"],
                        "Issue category": f["category_label"],
                        "Risk": compliance.effective_risk(f) + (" (reviewer)" if f["risk_override"] else ""),
                        "Status": compliance.STATUS_LABELS[f["status"]],
                        "Repeat": f"{f['repeat_count']} misses" if f["repeat_count"] >= compliance.REPEAT_THRESHOLD else "",
                        "Follow-up": (
                            f"{f['follow_up_owner']} by {f['follow_up_due']}" if f["follow_up_owner"] else ""
                        ),
                    }
                    for f in visible
                ]
            ),
            hide_index=True,
        )
    return visible


def _pick(visible: list[dict[str, Any]]) -> dict[str, Any]:
    settings = state.page("compliance")
    by_id = {f["record_id"]: f for f in visible}
    if settings.get("record_id") not in by_id:
        settings["record_id"] = visible[0]["record_id"]
    chosen = ui.selectbox(
        "Flag to review",
        list(by_id),
        settings,
        "record_id",
        key="comp_record",
        format_func=lambda rid: (
            f"{rid} · {by_id[rid]['employee']} · {compliance.effective_risk(by_id[rid])} · "
            f"{compliance.STATUS_LABELS[by_id[rid]['status']]}"
        ),
        dynamic=True,
    )
    return by_id[chosen]


def _detail(flag: dict[str, Any]) -> None:
    category = compliance.CATEGORIES[flag["category"]]
    with st.container(border=True):
        st.markdown(f"#### {ui.esc(flag['record_id'])} {ui.FACT_LABEL}")
        st.markdown(
            ui.result_badge("Fail") + " " + ui.risk_badge(compliance.effective_risk(flag)) + " " + ui.flag_status_badge(flag["status"])
        )
        ui.facts(
            [
                ("QA requirement", flag["requirement"]),
                ("Evaluation category", flag["section"]),
                ("Issue category", flag["category_label"]),
                ("Employee", flag["employee"]),
                ("Call date", flag["call_date"]),
                (
                    "Misses by this employee on this requirement",
                    f"{flag['repeat_count']} in this dataset",
                ),
            ]
        )
        st.markdown("**Evaluator comment**")
        ui.quote(flag["comment"], "The evaluator left no comment on this item.")
    with st.container(border=True):
        st.markdown(f"#### Why it was flagged {ui.RULE_LABEL}")
        ui.bullets(flag["risk_reasons"])
        if flag["risk_override"]:
            st.markdown(
                f"Rule-based level: {ui.esc(flag['rule_risk'])}. A reviewer set it to "
                f"**{ui.esc(flag['risk_override'])}**; the reason is in the history below."
            )
        st.markdown("**Possible implications**")
        st.markdown(ui.esc(category.implication))
        if flag["follow_up_owner"]:
            ui.facts([("Follow-up owner", flag["follow_up_owner"]), ("Follow-up due", flag["follow_up_due"])])


def _action_panel(flag: dict[str, Any], store: dict[str, Any]) -> None:
    record_id = flag["record_id"]
    name, role = state.actor()
    drafts = state.page("compliance").setdefault("drafts", {})
    draft = drafts.setdefault(record_id, {})
    # Keys carry the history length so the inputs start fresh after each recorded action.
    prefix = f"comp_{record_id}_{len(flag['history'])}"
    read_only = state.read_only()

    with st.container(border=True):
        st.markdown(f"#### Review action {ui.HUMAN_LABEL}")
        available = compliance.available_actions(flag, role) if not read_only else []
        if read_only:
            st.info("This is a replayed run, shown as it was saved. No action can be recorded.")
        elif not available:
            st.info(
                f"No action is available to the {ui.esc(role)} role while this flag is "
                f"'{ui.esc(compliance.STATUS_LABELS[flag['status']])}'. "
                + (
                    "A referred flag waits for the compliance-reviewer role."
                    if flag["status"] == compliance.REFERRED
                    else "Change the role in the sidebar if a different role should act."
                )
            )
        else:
            actions_by_key = {a.key: a for a in available}
            chosen = ui.selectbox(
                "Action",
                list(actions_by_key),
                draft,
                "action",
                key=f"{prefix}_action",
                format_func=lambda key: actions_by_key[key].label,
                dynamic=True,
            )
            action = actions_by_key[chosen]
            st.caption(action.help)
            ui.text_area(
                "Rationale" + (" (required)" if action.note_required else " (optional)"),
                draft,
                "note",
                key=f"{prefix}_note",
                height=100,
                max_chars=1500,
            )
            attested = True
            if action.attestation_required:
                attested = ui.checkbox(compliance.ATTESTATION_TEXT, draft, "attested", key=f"{prefix}_attest")
            if st.button("Record this action", key=f"{prefix}_record", type="primary"):
                try:
                    updated = operations.flag_action(
                        store, record_id, action.key, actor=name, role=role,
                        note=draft.get("note", ""), attested=bool(attested),
                    )
                except WorkflowError as exc:
                    ui.show_problems([str(exc)])
                    return
                drafts.pop(record_id, None)
                actions.persist_review()
                state.flash(
                    "success", f"{record_id}: {compliance.STATUS_LABELS[updated['status']]}."
                )
                st.rerun()

    if read_only:
        return
    with st.expander("Change the risk level"):
        if can(role, "override_risk"):
            levels = list(compliance.RISK_LEVELS)
            draft.setdefault("override_level", compliance.effective_risk(flag))
            level = ui.selectbox("Risk level", levels, draft, "override_level", key=f"{prefix}_level")
            ui.text_area(
                "Reason for the change (required)", draft, "override_note", key=f"{prefix}_level_note",
                height=80, max_chars=800,
            )
            if st.button("Record risk level", key=f"{prefix}_level_record"):
                try:
                    operations.flag_override_risk(
                        store, record_id, level, actor=name, role=role, note=draft.get("override_note", "")
                    )
                except WorkflowError as exc:
                    ui.show_problems([str(exc)])
                    return
                drafts.pop(record_id, None)
                actions.persist_review()
                state.flash("success", f"{record_id}: risk level recorded as {level}.")
                st.rerun()
        else:
            st.caption("Changing the risk level needs the Supervisor or Compliance reviewer role. " + NOTE_HINT)
    with st.expander("Assign follow-up"):
        if can(role, "assign_follow_up"):
            today = dt.date.today()
            draft.setdefault("owner", flag["follow_up_owner"])
            ui.text_input("Person or team responsible", draft, "owner", key=f"{prefix}_owner", max_chars=80)
            due = ui.date_input(
                "Due date", draft, "due", key=f"{prefix}_due", default=today + dt.timedelta(days=7), min_value=today
            )
            if st.button("Record follow-up", key=f"{prefix}_follow_record"):
                try:
                    operations.flag_follow_up(
                        store, record_id, owner=draft.get("owner", ""), due=due.isoformat(), actor=name, role=role
                    )
                except WorkflowError as exc:
                    ui.show_problems([str(exc)])
                    return
                drafts.pop(record_id, None)
                actions.persist_review()
                state.flash("success", f"{record_id}: follow-up assigned.")
                st.rerun()
        else:
            st.caption("Assigning follow-up needs the Supervisor or Compliance reviewer role.")


def _history(flag: dict[str, Any], store: dict[str, Any]) -> None:
    st.subheader("Review history for this flag")
    if not flag["history"]:
        st.caption("No action has been recorded on this flag yet.")
        return
    labels = {key: action.label for key, action in compliance.FLAG_ACTIONS.items()}
    labels.update({"override_risk": "Risk level changed", "assign_follow_up": "Follow-up assigned"})
    st.markdown(
        ui.md_table(
            ["When (UTC)", "Who", "Role", "Action", "Change", "Rationale"],
            [
                [
                    ui.esc(entry["at"]),
                    ui.esc(entry["actor"]),
                    ui.esc(entry["role"]),
                    ui.esc(labels.get(entry["action"], entry["action"])),
                    ui.esc(f"{_label(entry.get('from'))} to {_label(entry.get('to'))}") if entry.get("to") else " ",
                    ui.esc(entry["note"]) or "None given",
                ]
                for entry in flag["history"]
            ],
        )
    )
    entries = audit.entries_for(store["audit"], "flag", flag["record_id"])
    st.caption(f"{len(entries)} matching entries in the audit trail.")


def _label(value: Any) -> str:
    return compliance.STATUS_LABELS.get(str(value), str(value))
