"""Audit trail: every recorded decision, in order, with an integrity check."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from ui import components as ui, state
from workflow import audit

_ALL = "All subjects"


def render() -> None:
    ui.page_header(
        state.AUDIT,
        "Every decision and event recorded in this session, in order. Each entry is chained to the "
        "one before it so a later change to the record can be detected.",
    )
    if st.session_state.get("store") is None:
        st.error("No QA dataset is loaded.")
        return
    log: list[dict[str, Any]] = state.store()["audit"]
    result = audit.verify(log)
    left, right = st.columns([3, 1], vertical_alignment="center")
    with left:
        if result["ok"]:
            st.markdown(
                ui.badge(f"Chain intact: {result['checked']} entries verified", "green", ":material/verified:")
            )
        else:
            st.markdown(
                ui.badge(
                    f"Chain broken at entry {result['first_invalid']}: the record was altered after it was written",
                    "red",
                    ":material/gpp_bad:",
                )
            )
    right.download_button(
        "Download (.json)",
        json.dumps({"verification": result, "entries": log}, indent=2, default=str),
        file_name="agent_qa_review_audit.json",
        mime="application/json",
        key="audit_download",
        icon=":material/download:",
        disabled=not log,
    )
    st.caption(
        "What this does and does not provide: each entry stores the SHA-256 hash of the previous "
        "entry, so editing, removing, or reordering an entry is detectable. The trail lives in this "
        "session's memory and is cleared by a reset. The name and role on each entry are whatever was "
        "selected in the sidebar; nothing verifies identity. It is not a secure enterprise audit store."
    )
    if not log:
        st.info("Nothing has been recorded yet.")
        return
    settings = state.page("audit")
    subjects = [_ALL] + sorted({entry["subject_type"] for entry in log})
    subject = ui.selectbox("Show", subjects, settings, "subject", key="audit_subject", dynamic=True)
    shown = [entry for entry in log if subject == _ALL or entry["subject_type"] == subject]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Entry": entry["seq"],
                    "Time (UTC)": entry["timestamp"],
                    "Who": entry["actor"],
                    "Role": entry["role"],
                    "Action": entry["action"],
                    "Subject": f"{entry['subject_type']} {entry['subject_id']}".strip(),
                    "Details": json.dumps(entry["details"], default=str),
                    "Hash": entry["hash"][:12],
                }
                for entry in reversed(shown)
            ]
        ),
        hide_index=True,
        column_config={"Details": st.column_config.TextColumn("Details", width="large")},
    )
