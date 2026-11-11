"""Agent QA Review: Streamlit entry point.

    python -m streamlit run app.py

This file only wires things together: page configuration, session state, the
sidebar and header, and which workspace to draw. The screens are in ``ui/``,
the review rules in ``workflow/``, and the model-facing steps in ``pipeline.py``.

The default analysis source is an offline demonstration with handwritten
content for an invented dataset. A live run needs an authorized Amazon Bedrock
AgentCore harness; a replay shows a run saved by an earlier live session.
"""

import streamlit as st

from ui import actions, audit, coaching, compliance, dashboard, diagnosis, intake, referral, review, shell, state

st.set_page_config(page_title="Agent QA Review", page_icon="📋", layout="wide")

WORKSPACES = {
    state.DASHBOARD: dashboard.render,
    state.INTAKE: intake.render,
    state.DIAGNOSIS: diagnosis.render,
    state.REVIEW: review.render,
    state.COACHING: coaching.render,
    state.COMPLIANCE: compliance.render,
    state.REFERRAL: referral.render,
    state.AUDIT: audit.render,
}

state.init()
state.apply_pending_nav()  # must run before the sidebar creates the workspace selector
actions.ensure_data()
section = shell.render_sidebar()
shell.render_header()
WORKSPACES.get(section, dashboard.render)()
