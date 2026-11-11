"""Review workflow rules for Agent QA Review.

Everything in this package is plain Python: no Streamlit and no AWS calls.
The interface in ``ui/`` renders these rules; it does not re-implement them.

    common       roles, permissions, note quality
    intake       QA record queue, filters, evidence lookups, upload validation
    compliance   issue categories, risk rules, review-flag state machine
    review       supervisor decisions on AI-proposed findings
    coaching     package components, quiz grading, roleplay practice, completion
    referrals    simulated administrative referral records
    audit        hash-chained record of every decision
    analytics    dashboard figures computed from the data and the case store
    operations   the validated, audited state changes the interface may request
"""
