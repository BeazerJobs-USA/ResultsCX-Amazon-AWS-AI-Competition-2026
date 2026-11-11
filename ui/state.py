"""What the interface keeps in ``st.session_state`` and how it is reset.

Decisions, drafts, and selections live in plain dictionaries under the keys
below, never only in a widget. Streamlit discards a widget's state when the
widget is not drawn, so anything a user would mind losing when they change
workspace has to be stored separately.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from workflow import coaching, common, review

# -- analysis sources -------------------------------------------------------------------
DEMO, LIVE, REPLAY = "demo", "live", "replay"
MODE_LABELS: dict[str, str] = {
    DEMO: "Offline demonstration",
    LIVE: "Live - Amazon Bedrock AgentCore",
    REPLAY: "Replay a saved run",
}
MODE_HELP: dict[str, str] = {
    DEMO: "Handwritten findings and coaching content for the built-in synthetic dataset. No AWS call.",
    LIVE: "Sends masked QA data to the configured AgentCore harness. Needs approved AWS access.",
    REPLAY: "Shows a diagnosis and coaching packages saved by an earlier live run. Read-only.",
}

# -- workspaces -------------------------------------------------------------------------
DASHBOARD = "Dashboard"
INTAKE = "1 · Intake & Evidence"
DIAGNOSIS = "2 · Diagnosis & Risk"
REVIEW = "3 · Supervisor Review"
COACHING = "4 · Coaching & Practice"
COMPLIANCE = "Compliance Review"
REFERRAL = "Administrative Referral"
AUDIT = "Audit Trail"
SECTIONS: tuple[str, ...] = (DASHBOARD, INTAKE, DIAGNOSIS, REVIEW, COACHING, COMPLIANCE, REFERRAL, AUDIT)
STAGES: tuple[str, ...] = (INTAKE, DIAGNOSIS, REVIEW, COACHING)
NAV_KEY = "nav"
PENDING_NAV_KEY = "pending_nav"

SYNTHETIC, UPLOAD, PRIVATE = "synthetic", "upload", "private"

# Cleared whenever the dataset or the analysis source changes.
_RUN_DEFAULTS: dict[str, Any] = {
    "data": None,  # dataset dict (see get_data.dataset_from_records)
    "data_error": "",
    "diagnosis": None,  # validated AI findings
    "training": None,  # generated coaching packages
    "store": None,  # case store (see workflow.operations)
    "session": None,  # HarnessSession for live calls
    "legacy_approval": None,  # approval record from a run saved by an earlier version
}
# Kept across resets.
_SESSION_DEFAULTS: dict[str, Any] = {
    "mode": DEMO,
    "identity": None,
    "flash": None,
    PENDING_NAV_KEY: None,
}


def init() -> None:
    """Create every session key the interface reads. Safe to call on each run."""
    for key, value in {**_RUN_DEFAULTS, **_SESSION_DEFAULTS}.items():
        st.session_state.setdefault(key, value)
    if st.session_state["identity"] is None:
        st.session_state["identity"] = {"name": "Demo supervisor", "role": common.SUPERVISOR}
    for key in ("drafts", "ui", "practice"):
        st.session_state.setdefault(key, {})


def reset_run() -> None:
    """Clear the dataset, the analysis, and everything decided from it."""
    for key, value in _RUN_DEFAULTS.items():
        st.session_state[key] = value
    for key in ("drafts", "ui", "practice"):
        st.session_state[key] = {}


def clear_analysis() -> None:
    """Drop the findings and what was decided from them, keeping the data and its flags."""
    st.session_state["diagnosis"] = None
    st.session_state["training"] = None
    st.session_state["legacy_approval"] = None
    st.session_state["drafts"] = {}
    st.session_state["practice"] = {}
    store = st.session_state.get("store")
    if store:
        store["decisions"], store["packages"], store["trackers"] = {}, {}, {}


def goto(section: str) -> None:
    """Button callback: open another workspace on the next run."""
    st.session_state[NAV_KEY] = section


def request_nav(section: str) -> None:
    """Ask for another workspace from the middle of a run.

    The workspace selector already exists by then, and Streamlit does not allow a
    widget's key to be changed after the widget is created. The request is applied by
    :func:`apply_pending_nav` at the top of the next run instead.
    """
    st.session_state[PENDING_NAV_KEY] = section


def apply_pending_nav() -> None:
    """Honor a request made by :func:`request_nav`. Call before the sidebar is drawn."""
    pending = st.session_state.get(PENDING_NAV_KEY)
    if pending is None:
        return
    st.session_state[PENDING_NAV_KEY] = None
    if pending in SECTIONS:
        st.session_state[NAV_KEY] = pending


def flash(level: str, text: str) -> None:
    """Show a message once, at the top of the next run."""
    st.session_state["flash"] = {"level": level, "text": text}


def page(name: str) -> dict[str, Any]:
    """Selections and filters remembered for one workspace."""
    return st.session_state["ui"].setdefault(name, {})


def actor() -> tuple[str, str]:
    """(name, role) of the simulated signed-in person."""
    identity = st.session_state["identity"]
    return str(identity.get("name") or "").strip(), identity.get("role") or common.SUPERVISOR


def mode() -> str:
    return st.session_state["mode"]


def read_only() -> bool:
    """A replayed run is shown as it was saved; nothing new is decided in it."""
    return mode() == REPLAY


def data() -> dict[str, Any] | None:
    return st.session_state.get("data")


def store() -> dict[str, Any]:
    return st.session_state["store"]


def has_work() -> bool:
    """True when switching dataset or source would discard something a person did."""
    current = st.session_state.get("store") or {}
    return bool(
        st.session_state.get("diagnosis")
        or current.get("decisions")
        or current.get("referrals")
        or any(flag.get("history") for flag in current.get("flags", {}).values())
    )


def findings() -> list[dict[str, Any]]:
    return (st.session_state.get("diagnosis") or {}).get("findings", [])


def modules_by_finding() -> dict[str, dict[str, Any]]:
    training = st.session_state.get("training") or {}
    return {m["finding_id"]: m for m in training.get("modules", [])}


def stage_status() -> list[dict[str, str]]:
    """Status of the four stages for the progress strip: complete, active, or waiting."""
    current_data = data()
    diagnosis = st.session_state.get("diagnosis")
    decisions = (st.session_state.get("store") or {}).get("decisions", {})
    progress = review.progress(diagnosis, decisions)
    modules = modules_by_finding()
    ready = [f["id"] for f in findings() if review.coaching_ready(decisions.get(f["id"]))]
    generated = sum(1 for fid in ready if fid in modules)

    if current_data is None:
        intake = ("waiting", "No dataset loaded")
    elif diagnosis is None:
        intake = ("active", f"{current_data['totals']['line_items']} records loaded; analysis not started")
    else:
        intake = ("complete", f"{current_data['totals']['line_items']} records analyzed")
    if diagnosis is None:
        diagnose = ("waiting", "Run the analysis in stage 1")
    else:
        diagnose = ("complete", f"{progress['findings']} finding(s) proposed")
    if diagnosis is None:
        decide = ("waiting", "Needs findings to review")
    elif progress["final"] < progress["findings"]:
        decide = ("active", f"{progress['final']} of {progress['findings']} findings decided")
    else:
        decide = ("complete", f"All {progress['findings']} findings decided")
    if not ready:
        coach = ("waiting", "No finding has been sent to coaching")
    elif generated < len(ready):
        coach = ("active", f"{generated} of {len(ready)} packages generated")
    else:
        done = sum(
            coaching.tracker_summary(tracker)["done"]
            for fid, tracker in (st.session_state["store"].get("trackers") or {}).items()
            if fid in modules
        )
        coach = ("complete", f"{generated} package(s) ready; {done} practice item(s) recorded")
    return [
        {"name": name, "status": status, "detail": detail}
        for name, (status, detail) in zip(STAGES, (intake, diagnose, decide, coach), strict=True)
    ]
