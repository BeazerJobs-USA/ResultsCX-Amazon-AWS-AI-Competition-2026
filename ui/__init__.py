"""Streamlit interface for Agent QA Review.

Modules in this package draw screens and collect input. They hold no business
rules: every validation and state change is delegated to ``workflow/`` and
``pipeline.py``, which run and are tested without Streamlit.

    state       what is kept in the session and how it is reset
    components  shared display helpers and input widgets that keep their values
    actions     loading data, running the analysis, generating packages
    shell       sidebar, header, and the four-stage progress strip
    dashboard, intake, diagnosis, review, coaching, compliance, referral, audit
                one module per workspace
"""
