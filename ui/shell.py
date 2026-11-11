"""The frame around every workspace: sidebar, header, and the four-stage progress strip."""

from __future__ import annotations

import streamlit as st

import pipeline
from ui import actions, components as ui, state
from workflow import common

APP_NAME = "Agent QA Review"
APP_PURPOSE = (
    "Connects scored QA evidence to an AI-assisted diagnosis, a supervisor's decision, "
    "and targeted coaching and practice."
)


def render_sidebar() -> str:
    """Draw the sidebar and return the workspace to show."""
    with st.sidebar:
        st.markdown(f"### {APP_NAME}")
        st.caption("Student competition prototype. Not a ResultsCX or AWS product.")
        # No index argument: buttons elsewhere change this selection through Session State.
        section = st.radio("Workspace", state.SECTIONS, key=state.NAV_KEY)

        st.divider()
        st.markdown("**Acting as**")
        identity = st.session_state["identity"]
        ui.text_input("Name recorded with decisions", identity, "name", key="identity_name", max_chars=60)
        role = ui.selectbox("Role", common.ROLES, identity, "role", key="identity_role")
        st.caption(
            f"{common.ROLE_SUMMARY[role]} Roles simulate who may do what. "
            "This is not a login and nothing here verifies identity."
        )

        st.divider()
        _analysis_source()

        st.divider()
        _session_controls()
    return section


def _analysis_source() -> None:
    settings = state.page("shell")
    settings.setdefault("mode_choice", state.mode())
    nonce = settings.setdefault("mode_nonce", 0)
    choice = ui.radio(
        "Analysis source",
        list(state.MODE_LABELS),
        settings,
        "mode_choice",
        key=f"mode_choice_{nonce}",
        format_func=state.MODE_LABELS.get,
    )
    st.caption(state.MODE_HELP[state.mode()])
    if choice != state.mode():
        if state.has_work():
            st.warning(
                f"Switching to '{state.MODE_LABELS[choice]}' clears the current analysis, decisions, "
                "and audit trail."
            )
            if st.button("Switch and clear this run", key="mode_confirm", type="primary"):
                actions.switch_mode(choice)
                state.flash("info", f"Analysis source changed to {state.MODE_LABELS[choice]}.")
                st.rerun()
            st.button("Keep the current source", key="mode_cancel", on_click=_keep_mode)
        else:
            actions.switch_mode(choice)
            st.rerun()
    if state.mode() == state.LIVE:
        config, reason = actions.live_config()
        if config:
            st.caption(f"Harness configured for region {config['region']}.")
        else:
            st.caption("Harness not configured: " + ui.esc(reason))
        session = st.session_state.get("session")
        if session is not None:
            st.caption(
                f"Session {session.session_id[:8]}: {session.calls} call(s), "
                f"{session.total_input_tokens:,} tokens in, {session.total_output_tokens:,} out."
            )
    elif state.mode() == state.REPLAY:
        st.caption(ui.esc(actions.saved_run_note()))


def _keep_mode() -> None:
    """Callback: put the source selector back on the active source."""
    settings = state.page("shell")
    settings["mode_choice"] = state.mode()
    settings["mode_nonce"] = settings.get("mode_nonce", 0) + 1


def _session_controls() -> None:
    settings = state.page("shell")
    dataset = state.data()
    if dataset:
        st.caption("Dataset: " + ui.esc(dataset.get("source_label", dataset["source_kind"])))
    if not settings.get("confirm_reset"):
        st.button("Reset session", key="reset_ask", on_click=_ask_reset, width="stretch")
        return
    st.warning("This clears the analysis, decisions, coaching packages, referrals, and the audit trail.")
    if st.button("Confirm reset", key="reset_confirm", type="primary", width="stretch"):
        state.reset_run()
        actions.ensure_data()
        state.flash("info", "The session was reset.")
        st.rerun()
    st.button("Cancel", key="reset_cancel", on_click=_cancel_reset, width="stretch")


def _ask_reset() -> None:
    state.page("shell")["confirm_reset"] = True


def _cancel_reset() -> None:
    state.page("shell")["confirm_reset"] = False


def render_header() -> None:
    """Application title, what kind of data and analysis is on screen, and stage progress."""
    st.title(APP_NAME)
    st.caption(APP_PURPOSE)
    dataset = state.data()
    _name, role = state.actor()
    badges = []
    if dataset is None:
        badges.append(ui.badge("No dataset loaded", "gray", ":material/database:"))
    elif pipeline.is_synthetic(dataset["source_kind"]):
        badges.append(ui.badge("Synthetic data", "orange", ":material/science:"))
    else:
        badges.append(ui.badge("User-supplied data, handled as private", "red", ":material/lock:"))
    mode_style = {
        state.DEMO: ("blue", ":material/cloud_off:", "Offline demonstration, no AWS call"),
        state.LIVE: ("violet", ":material/cloud:", "Live analysis with Amazon Bedrock AgentCore"),
        state.REPLAY: ("gray", ":material/history:", "Replay of a saved run, read-only"),
    }[state.mode()]
    badges.append(ui.badge(mode_style[2], mode_style[0], mode_style[1]))
    badges.append(ui.badge(f"Role: {role} (simulated)", "gray", ":material/person:"))
    st.markdown(" ".join(badges))

    columns = st.columns(len(state.STAGES))
    for index, (column, stage) in enumerate(zip(columns, state.stage_status(), strict=True), start=1):
        with column:
            with st.container(border=True):
                st.markdown(f"**{stage['name']}**  \n{ui.stage_badge(stage['status'])}")
                st.caption(stage["detail"])
                st.button(
                    f"Open stage {index}",
                    key=f"open_stage_{index}",
                    on_click=state.goto,
                    args=(stage["name"],),
                    type="tertiary",
                )
    ui.show_flash()
