"""Streamlit UI for QA diagnosis, supervisor review, and coaching practice.

The public default is a handwritten synthetic walkthrough. Live AWS calls
require approved configuration; replay uses locally saved output.
"""

import json
from datetime import datetime

import pandas as pd
import streamlit as st

import get_data
import pipeline
import sample_walkthrough
import security_controls
from business_impact import calculate_business_impact
from harness_client import HarnessError, HarnessSession, get_config

# ======================================================================================
# Page setup and state
# ======================================================================================
st.set_page_config(
    page_title="ResultsCX Coaching Intelligence", page_icon="🎧", layout="wide"
)

STAGES = [
    "1 · QA data",
    "2 · AI diagnosis",
    "3 · Supervisor review",
    "4 · Training & practice",
]
DEFAULTS = {
    "data": None,  # Stage 1 result (dict from get_data.get_all_data)
    "diagnosis": None,  # Stage 2 result
    "approval": None,  # Stage 3 record: who approved what, and what they changed
    "training": None,  # Stage 4 result
    "session": None,  # HarnessSession (one runtimeSessionId for the whole pipeline)
    "error": None,  # last error text (shown at the top)
    "run_started": None,
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_run():
    for key, value in DEFAULTS.items():
        st.session_state[key] = value


def current_stage():
    if st.session_state.training:
        return 3
    if st.session_state.diagnosis:
        return 2
    if st.session_state.data:
        return 1
    return 0


def show_error(title, err):
    """Store a pipeline error for display without losing the current stage."""
    st.session_state.error = f"{title}\n\n{err}"


# ======================================================================================
# Sidebar: mode, configuration status, controls
# ======================================================================================
with st.sidebar:
    st.header("Run settings")
    mode = st.radio(
        "Model access",
        [
            "Synthetic walkthrough — no AWS",
            "Live — call Bedrock",
            "Replay — last saved run (no AWS)",
        ],
        index=0,
        help="Synthetic walkthrough is handwritten example content. Replay loads an earlier saved run.",
    )
    live = mode.startswith("Live")
    sample_mode = mode.startswith("Synthetic")
    if st.session_state.get("_last_mode") != mode:
        reset_run()
        st.session_state["_last_mode"] = mode
    max_findings = st.slider("Max findings to diagnose", 3, 6, 5)

    st.subheader("Configuration")
    cfg = None
    if live:
        try:
            cfg = get_config()
            st.success(f"AWS runtime configured (region: {cfg['region']})")
            if cfg["max_tokens"]:
                st.caption(f"HARNESS_MAX_TOKENS = {cfg['max_tokens']}")
        except HarnessError as e:
            st.error(str(e).splitlines()[0])
            st.caption(
                "Configure an approved AWS profile and runtime, or use the synthetic walkthrough."
            )

    saved = pipeline.load_json("diagnosis")
    if saved:
        st.caption(
            f"Saved run available for replay: {saved.get('_meta', {}).get('generated_at', '?')}"
        )
    else:
        st.caption("No saved run yet (run once in Live mode to create one).")

    sess = st.session_state.session
    if sess:
        st.subheader("This session")
        st.caption(f"runtimeSessionId: {sess.session_id[:8]}…")
        st.caption(
            f"Bedrock calls: {sess.calls}  ·  tokens in/out: {sess.total_input_tokens:,} / {sess.total_output_tokens:,}"
        )

    if st.button("Start over", width="stretch"):
        reset_run()
        st.rerun()


# ======================================================================================
# Header + stage tracker
# ======================================================================================
st.title("Agent QA Review")
st.caption(
    "ResultsCX × AWS student competition prototype | QA evidence → diagnosis → review → coaching"
)
if sample_mode:
    st.info(
        "SYNTHETIC WALKTHROUGH: All cases and responses here are handwritten examples. No AWS call, real QA records, or measured outcomes."
    )

stage_now = current_stage()
cols = st.columns(len(STAGES))
for i, (col, name) in enumerate(zip(cols, STAGES, strict=True)):
    if i < stage_now:
        col.success(f"✅ {name}")
    elif i == stage_now:
        col.info(f"▶ {name}")
    else:
        col.markdown(
            f"<div style='opacity:.5;padding:.6rem;border:1px dashed #999;border-radius:6px'>○ {name}</div>",
            unsafe_allow_html=True,
        )

if st.session_state.error:
    st.error(st.session_state.error)
    if st.button("Dismiss error"):
        st.session_state.error = None
        st.rerun()

st.divider()

# ======================================================================================
# STAGE 1 — QA data (always shown)
# ======================================================================================
st.header("1 · QA data (Data Intake)")

if st.session_state.data is None:
    try:
        # A public walkthrough must remain synthetic even when a developer's
        # environment is configured for an authorized private-data run.
        st.session_state.data = get_data.get_all_data(
            source_override="synthetic" if sample_mode else None
        )
    except get_data.DataError as e:
        st.error(f"Could not load the QA data.\n\n{e}")
        st.stop()

data = st.session_state.data
t = data["totals"]
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Agent-date groups (proxy)", t["calls_evaluated"])
m2.metric("Agents", t["agents"])
m3.metric("Scored line items", t["line_items"])
m4.metric("Failed line items", t["failed_items"])
m5.metric("Overall defect rate", f"{round(100 * t['failed_items'] / t['line_items'])}%")
st.caption(
    f"Source: {data['source_kind'].upper()} · dates {t['date_range'][0]} to {t['date_range'][1]} · sections: "
    + ", ".join(f"{v['name']} ({k})" for k, v in data["sections"].items())
)
st.caption(
    "An agent-date group is not necessarily one unique call. Defect rates are per scored line item."
)

with st.expander("Privacy and data-quality checks"):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Known fields handled by the input pipeline**")
        st.table(
            pd.DataFrame(
                [{"what": k, "replaced": v} for k, v in data["scrub_log"].items()]
            )
        )
        st.caption(
            "The public walkthrough uses invented records. Private-data masking is incomplete and needs human review."
        )
    with c2:
        st.markdown("**Integrity checks**")
        for name, ok, detail in data["checks"]:
            (st.success if ok else st.error)(
                f"{'PASS' if ok else 'FAIL'} — {name}  ({detail})"
            )

    # This second pass checks the entire AI payload, not just Excel comments.
    # It runs locally and sends nothing to AWS when the screen opens.
    _, extra_masks = security_controls.sanitize_payload(
        get_data.build_model_payload(data), pipeline.known_staff_names(data)
    )
    st.caption(
        f"Pre-model privacy checkpoint: {extra_masks} additional identifier patterns "
        "masked in a copy of this intake payload. The final prompts and tool replies "
        "are checked again before AWS calls. Regex masking is not complete de-identification."
    )

# Show the potential business value from the FIRST screen of a short demo.
# Every number is editable; nothing is presented as a real measured saving.
with st.expander("📊 Interactive business impact · supervisor preparation time", expanded=True):
    st.markdown(
        "**Business problem:** A QA score identifies a missed behavior, but a supervisor "
        "still has to turn it into a useful lesson, quiz, and practice scenario. "
        "This estimates the preparation time that the coaching workflow *might* free up."
    )
    left, right = st.columns(2)
    cases_per_week = left.number_input(
        "Coaching packages prepared per week", min_value=0, value=40, step=5
    )
    manual_minutes = left.number_input(
        "Manual preparation minutes per package", min_value=0, value=30, step=5
    )
    assisted_minutes = right.number_input(
        "AI-assisted minutes per package (including human review)",
        min_value=0,
        value=18,
        step=5,
    )
    hourly_rate = right.number_input(
        "Fully loaded supervisor hourly cost ($)",
        min_value=0.0,
        value=45.0,
        step=5.0,
    )
    weeks_per_year = st.number_input(
        "Working weeks per year", min_value=0, max_value=52, value=50, step=1
    )
    impact = calculate_business_impact(
        cases_per_week, manual_minutes, assisted_minutes, hourly_rate, weeks_per_year
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Supervisor hours freed / week", f"{impact['saved_weekly_hours']:,.1f}")
    c2.metric("Supervisor hours freed / year", f"{impact['saved_annual_hours']:,.0f}")
    c3.metric(
        "Annual labor-capacity value", f"${impact['annual_capacity_value']:,.0f}"
    )
    st.table(
        pd.DataFrame(
            [
                {
                    "Preparation scenario": "Manual process",
                    "Hours / week": round(impact["manual_weekly_hours"], 1),
                    "Hours / year": round(impact["manual_weekly_hours"] * weeks_per_year, 1),
                },
                {
                    "Preparation scenario": "AI-assisted + supervisor review",
                    "Hours / week": round(impact["assisted_weekly_hours"], 1),
                    "Hours / year": round(impact["assisted_weekly_hours"] * weeks_per_year, 1),
                },
                {
                    "Preparation scenario": "Potential time freed",
                    "Hours / week": round(impact["saved_weekly_hours"], 1),
                    "Hours / year": round(impact["saved_annual_hours"], 1),
                },
            ]
        )
    )
    st.caption(
        f"Scenario only · {impact['time_reduction_pct']:.0f}% change in preparation time. "
        "These starting inputs are hypothetical, not observed ResultsCX results. "
        "Time freed is potential capacity, not guaranteed payroll savings. "
        "A pilot must measure actual preparation and review time, QA defect rates, "
        "and subsequent employee performance. Negative values mean the assisted process "
        "would take longer under the chosen assumptions."
    )

st.subheader("Defect rates by QA behavior (computed, not generated)")
behavior_rows = [
    {
        "id": b["id"],
        "section": b["section"],
        "behavior": b["behavior"],
        "failed / evaluated": f"{b['failed']} / {b['evaluated']}",
        "defect rate %": b["defect_rate_pct"],
        "pattern": b["pattern"],
        "agents failed": b["n_agents_failed"],
    }
    for b in data["behaviors"]
]
st.dataframe(
    pd.DataFrame(behavior_rows),
    width="stretch",
    hide_index=True,
    column_config={
        "defect rate %": st.column_config.ProgressColumn(
            "defect rate %", min_value=0, max_value=100, format="%d%%"
        )
    },
)

with st.expander("Look at the masked evaluator comments behind a behavior"):
    options = {
        f"{b['id']} · {b['behavior']} ({b['defect_rate_pct']}%)": b
        for b in data["behaviors"]
        if b["evidence"]
    }
    if options:
        pick = st.selectbox("Behavior", list(options))
        ev_rows = [
            {
                "record": e["record_id"],
                "agent": e["agent"],
                "date": e["date"],
                "result": "FAIL" if e["result"] == "No" else "PASS",
                "comment (masked)": e["comment"],
            }
            for e in options[pick]["evidence"]
        ]
        st.dataframe(pd.DataFrame(ev_rows), width="stretch", hide_index=True)
    else:
        st.caption("No evaluator comments found in the exports.")

# ---- the Stage 2 trigger ----
st.divider()
if st.session_state.diagnosis is None:
    label = (
        "▶ Run AI diagnosis (Stage 2)"
        if live
        else "▶ Load synthetic walkthrough"
        if sample_mode
        else "▶ Load saved diagnosis (replay)"
    )
    if st.button(label, type="primary", width="stretch"):
        st.session_state.error = None
        st.session_state.run_started = datetime.now()
        try:
            if live:
                if cfg is None:
                    raise HarnessError(
                        "HARNESS_ARN is not configured — see the sidebar. Fix .env and reload, or switch to Replay."
                    )
                session = HarnessSession(
                    tools={
                        "get_all_data": lambda **kw: pipeline.build_safe_model_payload(data)
                    }
                )
                st.session_state.session = session
                with st.status(
                    "Stage 2 · Needs analysis — asking Bedrock…", expanded=True
                ) as status:
                    st.write(
                        f"Session {session.session_id[:8]}… opened · sending masked QA data"
                    )
                    ph = st.empty()
                    received = [0]

                    def on_text(chunk):
                        received[0] += len(chunk)
                        if received[0] % 400 < len(
                            chunk
                        ):  # refresh the counter every ~400 characters, not every token
                            ph.caption(
                                f"receiving the diagnosis … {received[0]:,} characters"
                            )

                    diag = pipeline.run_diagnosis(
                        session, data, max_findings=max_findings, on_text=on_text
                    )
                    status.update(
                        label=f"Stage 2 complete · {len(diag['findings'])} findings, "
                        f"{sum(f['evidence_verified'] for f in diag['findings'])} citations verified",
                        state="complete",
                        expanded=False,
                    )
            elif sample_mode:
                diag = sample_walkthrough.demo_diagnosis(data)
            else:
                diag = pipeline.load_json("diagnosis")
                if not diag:
                    raise pipeline.PipelineError(
                        "No saved run to replay.\nFix: switch to Live mode and run the diagnosis once; "
                        "every successful run is saved under outputs/ automatically."
                    )
                if diag.get("_meta", {}).get(
                    "data_fingerprint"
                ) != pipeline.data_fingerprint(data):
                    raise pipeline.PipelineError(
                        "The saved diagnosis does not match the loaded QA dataset. "
                        "Use the matching source or start a new analysis."
                    )
                replay_training = pipeline.load_json("training")
                if not replay_training:
                    raise pipeline.PipelineError(
                        "The saved diagnosis has no completed training package to replay. "
                        "Run the full approved flow in Live mode to create one."
                    )
                if replay_training.get("_meta", {}).get(
                    "data_fingerprint"
                ) != pipeline.data_fingerprint(data):
                    raise pipeline.PipelineError(
                        "The saved training was created from a different QA dataset."
                    )
                # Replay historical artifacts together. Never attribute old training
                # to a new reviewer or to edits that were not used to generate it.
                st.session_state.training = replay_training
                st.session_state.approval = pipeline.load_json("approval")
                st.info(
                    f"REPLAY — showing the saved diagnosis generated {diag.get('_meta', {}).get('generated_at', '?')}"
                )
            st.session_state.diagnosis = diag
            st.rerun()
        except (HarnessError, pipeline.PipelineError, get_data.DataError) as e:
            show_error("Stage 2 failed", e)
            st.rerun()

# ======================================================================================
# STAGE 2 — AI diagnosis
# ======================================================================================
diag = st.session_state.diagnosis
if diag:
    st.header("2 · AI diagnosis (Needs Analysis)")
    meta = diag.get("_meta", {})
    if not live and not sample_mode:
        st.info(
            f"REPLAY of the run saved {meta.get('generated_at', '?')} — no model call was made."
        )
    st.markdown(
        f"**{'Synthetic example summary' if sample_mode else 'Summary from the model'}:** {diag.get('summary', '')}"
    )
    verified = sum(f["evidence_verified"] for f in diag["findings"])
    total = sum(f["evidence_total"] for f in diag["findings"])
    st.caption(
        f"{len(diag['findings'])} findings · {verified}/{total} citations matched to record, behavior, and exact comment text · "
        f"tokens in/out {meta.get('input_tokens', '?')}/{meta.get('output_tokens', '?')} · {meta.get('latency_ms', '?')} ms"
    )

    for f in diag["findings"]:
        with st.container(border=True):
            top = st.columns([4, 1, 1, 1, 1])
            top[0].markdown(f"### {f['id']} · {f['title']}")
            top[0].caption(f"{f.get('section', '?')} · {f.get('behavior', '')}")
            if f.get("metrics_verified"):
                top[1].metric(
                    "defect rate",
                    f"{f['defect_rate_pct']}%",
                    help=f"{f['failed']} of {f['evaluated']} scored line items",
                )
            else:
                top[1].warning("metrics not matched")
            top[2].metric("agents affected", len(f.get("agents_failed", [])))
            top[3].markdown(f"**Root cause**\n\n{f['root_cause_type']}")
            top[4].markdown(f"**Confidence**\n\n{f['confidence']}")

            a, b = st.columns(2)
            a.markdown("**OBSERVED** — what evaluators wrote")
            a.write(f["observed"])
            a.caption(f"Pattern: {f.get('computed_pattern') or f.get('pattern', '')}")
            b.markdown("**INFERRED** — the model's explanation of why")
            b.write(f["root_cause"])
            b.caption(
                f"Recommended: {f['recommended_intervention']} · Existing curriculum covers it: {f['curriculum_coverage']} — {f['curriculum_note']}"
            )

            st.markdown(
                "**Evidence** (record ID, behavior, and quoted text checked against loaded rows)"
            )
            ev_rows = [
                {
                    "verified": "✅ matched" if e["verified"] else "⚠️ review",
                    "record": e["record_id"],
                    "agent": e.get("agent", ""),
                    "date": e.get("date", ""),
                    "result": ("FAIL" if e.get("result") == "No" else "PASS")
                    if e["verified"]
                    else "",
                    "quote cited by the model": e["quote"],
                }
                for e in f["evidence"]
            ]
            if ev_rows:
                st.dataframe(pd.DataFrame(ev_rows), width="stretch", hide_index=True)
                with st.expander("Full masked comments for these records"):
                    for e in f["evidence"]:
                        if e["verified"]:
                            st.markdown(
                                f"**{e['record_id']}** · {e.get('agent')} · {e.get('date')} · "
                                f"{'FAIL' if e.get('result') == 'No' else 'PASS'}\n\n> {e.get('comment', '')}"
                            )
            else:
                st.warning(
                    "The model cited no evidence for this finding — treat with caution."
                )
            if f.get("confidence_note"):
                st.caption(f"Confidence note: {f['confidence_note']}")

    c1, c2 = st.columns(2)
    if diag.get("not_a_training_problem"):
        c1.markdown("**Where training is NOT the answer**")
        for x in diag["not_a_training_problem"]:
            c1.markdown(f"- {x}")
    if diag.get("open_questions"):
        c2.markdown("**Open questions for the supervisor**")
        for x in diag["open_questions"]:
            c2.markdown(f"- {x}")

    # ==================================================================================
    # STAGE 3 — Supervisor review and approval (the human checkpoint)
    # ==================================================================================
    st.divider()
    st.header("3 · Supervisor review (Human Validation)")
    urgent_items = security_controls.critical_compliance_alerts(data)
    if urgent_items:
        # Deterministic alert: visible as soon as Stage 3 loads, before approval.
        # It does not contact anyone outside the app or declare a HIPAA violation.
        st.error(
            f"🚨 IMMEDIATE REVIEW NEEDED: {len(urgent_items)} potentially critical "
            "identity/privacy compliance failure(s) in the QA scores. "
            "Review these records and follow your organization's escalation policy. "
            "AI diagnosis is not required to raise this flag."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "record ID": item["record_id"],
                        "employee code": item["agent"],
                        "failed control": security_controls.sanitize_text(item["behavior"])[0],
                        "action": "Supervisor review / compliance escalation",
                    }
                    for item in urgent_items
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Rule: failed Compliance (CO) item referencing identity verification or privacy. "
            "This is a potential-risk flag, not a verified incident, legal determination, "
            "external notification, or substitute for the supervisor's judgment."
        )
    else:
        st.success("No failed identity/privacy compliance items matched the current alert rules.")
    if st.session_state.training is None:
        st.markdown(
            "Correct anything the AI got wrong, untick findings that should not become training, then approve. "
            "**Approval starts training generation automatically — no further prompt is needed.**"
        )
        with st.form("supervisor_review"):
            supervisor = st.text_input(
                "Reviewer name (recorded with the approval)",
                value="Demo reviewer" if sample_mode else "Supervisor",
            )
            edits = {}
            for f in diag["findings"]:
                st.markdown(f"#### {f['id']} · {f['title']}")
                include = st.checkbox(
                    "Include in training",
                    value=f["training_recommended"],
                    key=f"inc_{f['id']}",
                )
                r1, r2, r3 = st.columns(3)
                root_type = r1.selectbox(
                    "Root cause type",
                    pipeline.ROOT_CAUSE_TYPES,
                    index=pipeline.ROOT_CAUSE_TYPES.index(f["root_cause_type"]),
                    key=f"rct_{f['id']}",
                )
                interv = r2.selectbox(
                    "Intervention",
                    pipeline.INTERVENTIONS,
                    index=pipeline.INTERVENTIONS.index(f["recommended_intervention"]),
                    key=f"int_{f['id']}",
                )
                cover = r3.selectbox(
                    "Curriculum coverage today",
                    pipeline.COVERAGE,
                    index=pipeline.COVERAGE.index(f["curriculum_coverage"]),
                    key=f"cov_{f['id']}",
                )
                title = st.text_input(
                    "Finding title", value=f["title"], key=f"title_{f['id']}"
                )
                root = st.text_area(
                    "Root cause (edit if the AI is wrong)",
                    value=f["root_cause"],
                    key=f"root_{f['id']}",
                    height=90,
                )
                note = st.text_area(
                    "Supervisor note for the training designer (optional)",
                    value="",
                    key=f"note_{f['id']}",
                    height=70,
                )
                edits[f["id"]] = {
                    "include": include,
                    "title": title,
                    "root_cause_type": root_type,
                    "root_cause": root,
                    "recommended_intervention": interv,
                    "curriculum_coverage": cover,
                    "supervisor_note": note,
                }
                st.markdown("---")
            approved_clicked = st.form_submit_button(
                "✅ APPROVE DIAGNOSIS — generate training automatically",
                type="primary",
                width="stretch",
            )

        if approved_clicked:
            st.session_state.error = None
            approved = pipeline.build_approved_findings(
                diag,
                edits,
                approval_mode="synthetic_demo" if sample_mode else "human_review",
            )
            if not approved:
                st.warning(
                    "No finding is ticked 'Include in training'. Tick at least one and approve again."
                )
                st.stop()
            # Keep the reviewer's edits separate from the model's original values.
            changes = []
            for f in diag["findings"]:
                e = edits[f["id"]]
                for key in (
                    "title",
                    "root_cause_type",
                    "root_cause",
                    "recommended_intervention",
                    "curriculum_coverage",
                ):
                    if str(e[key]).strip() != str(f.get(key, "")).strip():
                        changes.append(
                            {
                                "finding": f["id"],
                                "field": key,
                                "ai_value": f.get(key, ""),
                                "supervisor_value": e[key],
                            }
                        )
                if e["supervisor_note"].strip():
                    changes.append(
                        {
                            "finding": f["id"],
                            "field": "supervisor_note",
                            "ai_value": "",
                            "supervisor_value": e["supervisor_note"],
                        }
                    )
                if not e["include"]:
                    changes.append(
                        {
                            "finding": f["id"],
                            "field": "include",
                            "ai_value": f["training_recommended"],
                            "supervisor_value": False,
                        }
                    )
            approval = {
                "approved_by": supervisor,
                "approved_at": datetime.now().isoformat(timespec="seconds"),
                "approved_findings": [a["id"] for a in approved],
                "changes": changes,
            }
            pipeline.save_json(
                "approval", approval, source_kind=data.get("source_kind", "synthetic")
            )
            st.session_state.approval = approval

            try:
                if live:
                    session = st.session_state.session
                    if (
                        session is None
                    ):  # e.g. the page was reloaded - open a new session for the training calls
                        session = HarnessSession()
                        st.session_state.session = session
                    with st.status(
                        f"Stage 4 · Training design — generating {len(approved)} module(s) automatically…",
                        expanded=True,
                    ) as status:
                        ph = st.empty()
                        received = [0]

                        def on_progress(i, n, finding):
                            received[0] = 0
                            st.write(f"Module {i}/{n}: {finding['title']}")

                        def on_text(chunk):
                            received[0] += len(chunk)
                            if received[0] % 400 < len(chunk):
                                ph.caption(f"receiving … {received[0]:,} characters")

                        result = pipeline.run_training(
                            session,
                            approved,
                            data,
                            on_progress=on_progress,
                            on_text=on_text,
                        )
                        status.update(
                            label=f"Stage 4 complete · {len(result['modules'])} module(s) generated"
                            + (
                                f", {len(result['errors'])} failed"
                                if result["errors"]
                                else ""
                            ),
                            state="complete",
                            expanded=False,
                        )
                elif sample_mode:
                    result = sample_walkthrough.demo_training(approved, data)
                else:
                    result = pipeline.load_json("training")
                    if not result:
                        raise pipeline.PipelineError(
                            "No saved training to replay.\nFix: run the full flow once in Live mode."
                        )
                    if result.get("_meta", {}).get(
                        "data_fingerprint"
                    ) != pipeline.data_fingerprint(data):
                        raise pipeline.PipelineError(
                            "The saved training was created from a different QA dataset."
                        )
                    wanted = {a["id"] for a in approved}
                    result["modules"] = [
                        m for m in result["modules"] if m["finding_id"] in wanted
                    ]
                    result["approved_findings"] = approved
                    result["alignment"] = pipeline.alignment_check(
                        approved, result["modules"]
                    )
                st.session_state.training = result
                st.rerun()
            except (HarnessError, pipeline.PipelineError, get_data.DataError) as e:
                show_error("Stage 4 failed after approval", e)
                st.rerun()
    else:
        ap = st.session_state.approval or {}
        if not live and not sample_mode and not ap:
            st.info(
                "REPLAY — saved training is shown as originally generated. "
                "This run has no separately recorded supervisor approval."
            )
        else:
            mode_note = (
                "Handwritten synthetic practice was loaded"
                if sample_mode
                else "The original saved approval is shown; no new review occurred"
                if not live
                else "Training followed this approval"
            )
            st.success(
                f"Reviewed by **{ap.get('approved_by', '?')}** at {ap.get('approved_at', '?')} — "
                f"findings {', '.join(ap.get('approved_findings', []))}. {mode_note}."
            )
        if ap.get("changes"):
            with st.expander(
                f"What the supervisor changed before approving ({len(ap['changes'])} edit(s))"
            ):
                st.dataframe(
                    pd.DataFrame(ap["changes"]), width="stretch", hide_index=True
                )
        else:
            st.caption("The supervisor approved the AI findings without changes.")

# ======================================================================================
# STAGE 4 — Training, hands-on practice, role-play (+ Stage 5 alignment check)
# ======================================================================================
result = st.session_state.training
if result:
    st.divider()
    st.header("4 · Training & hands-on practice (Training Design)")
    st.caption(
        "Each module follows an approved finding: diagnosis → objective → instruction → exercise → scenario → roleplay. "
        + (
            "This content is a synthetic fixture, not a model result."
            if sample_mode
            else ""
        )
    )
    if not live and not sample_mode:
        st.info(
            f"REPLAY of the training saved {result.get('_meta', {}).get('generated_at', '?')}."
        )

    by_id = {f["id"]: f for f in result.get("approved_findings", [])}
    for mod in result["modules"]:
        f = by_id.get(mod["finding_id"], {})
        with st.container(border=True):
            st.markdown(f"### {mod['finding_id']} → {mod['module_title']}")
            st.caption(
                f"Fixes: **{f.get('title', mod['finding_id'])}** · {f.get('behavior', '')} · "
                f"{f.get('defect_rate_pct', '?')}% defect rate · root cause: {f.get('root_cause_type', '')} · "
                f"delivery: {mod['delivery']} · {mod.get('duration_minutes', '?')} min"
            )
            total = mod.get("citations_total", 0)
            if total:
                bad = [
                    c["record_id"] for c in mod.get("citations", []) if not c["exists"]
                ]
                off = mod.get("citations_off_behavior", [])
                if bad:
                    st.error(
                        f"⚠️ This module cites {len(bad)} record ID(s) that do not exist in the QA data: "
                        f"{', '.join(bad)} — do not use these claims without checking."
                    )
                elif off:
                    st.warning(
                        f"{mod['citations_verified']}/{total} cited record IDs verified, but these are about a "
                        f"different behavior than the one diagnosed: {', '.join(off)}"
                    )
                else:
                    st.success(
                        f"✅ All {total} record IDs cited in this module matched the loaded QA rows "
                        f"(and all belong to the diagnosed behavior)."
                    )
                with st.expander("Show the evidence this module cites"):
                    cite_rows = [
                        {
                            "verified": "✅"
                            if c["exists"] and c.get("same_behavior")
                            else "⚠️ review",
                            "record": c["record_id"],
                            "agent": c.get("agent", ""),
                            "date": c.get("date", ""),
                            "result": ("FAIL" if c.get("result") == "No" else "PASS")
                            if c["exists"]
                            else "",
                            "same behavior": ("yes" if c.get("same_behavior") else "no")
                            if c["exists"]
                            else "",
                            "comment (masked)": c.get("comment", ""),
                        }
                        for c in mod["citations"]
                    ]
                    st.dataframe(
                        pd.DataFrame(cite_rows), width="stretch", hide_index=True
                    )
            else:
                st.info(
                    "This module has no explicit record citations. Review its claims before use."
                )

            tabs = st.tabs(
                [
                    "Objective & instruction",
                    "Knowledge check",
                    "Hands-on exercise",
                    "Simulation scenario",
                    "Role-play script",
                ]
            )
            with tabs[0]:
                st.markdown(f"**Learning objective:** {mod['learning_objective']}")
                st.markdown(
                    f"**Why this fixes the gap:** {mod['why_this_fixes_the_gap']}"
                )
                st.markdown("**Instruction**")
                for step in mod["instruction"]:
                    st.markdown(f"- {step}")
                if mod["existing_material_to_reuse"]:
                    st.markdown("**Existing outline material to reuse**")
                    for x in mod["existing_material_to_reuse"]:
                        st.markdown(f"- {x}")
            with tabs[1]:
                kc = mod["knowledge_check"]
                st.markdown(f"**{kc.get('question', '')}**")
                for opt in kc.get("options", []):
                    st.markdown(f"- {opt}")
                st.caption(
                    f"Correct: {kc.get('correct', '?')} — {kc.get('feedback', '')}"
                )
            with tabs[2]:
                ex = mod["hands_on_exercise"]
                st.markdown(f"**{ex.get('title', '')}**")
                st.markdown(f"**Trainee:** {ex.get('trainee_instructions', '')}")
                st.markdown(
                    f"**Facilitator:** {ex.get('facilitator_instructions', '')}"
                )
                st.markdown("**Success criteria**")
                for s in ex.get("success_criteria", []):
                    st.markdown(f"- {s}")
            with tabs[3]:
                sc = mod["simulation_scenario"]
                s1, s2 = st.columns(2)
                s1.markdown(f"**Call driver:** {sc.get('call_driver', '')}")
                s1.markdown(f"**Member persona:** {sc.get('member_persona', '')}")
                s1.markdown(f"**Member emotion:** {sc.get('member_emotion', '')}")
                s2.markdown(f"**Behavioral target:** {sc.get('behavioral_target', '')}")
                s2.markdown(
                    f"**Expected agent response:** {sc.get('expected_agent_response', '')}"
                )
                st.markdown(f"**Context:** {sc.get('scenario_context', '')}")
                st.markdown("**Scoring criteria (use the QA form)**")
                for s in sc.get("scoring_criteria", []):
                    st.markdown(f"- {s}")
            with tabs[4]:
                for turn in mod["roleplay_script"]:
                    spk = str(turn.get("speaker", "")).upper()
                    if spk == "BRANCH":
                        st.warning(
                            f"**BRANCH** — if the agent demonstrates the behavior: {turn.get('if_agent_demonstrates_behavior', '')}\n\n"
                            f"If not: {turn.get('if_agent_does_not', '')}"
                        )
                    elif spk == "AGENT":
                        st.markdown(f"🎧 **AGENT (trainee):** {turn.get('line', '')}")
                        if turn.get("coach_note"):
                            st.caption(f"coach note: {turn['coach_note']}")
                    else:
                        st.markdown(f"🙍 **MEMBER:** {turn.get('line', '')}")

    for err in result.get("errors", []):
        st.error(
            f"Module for {err['finding_id']} ({err['title']}) failed:\n\n{err['error']}"
        )

    st.subheader("5 · Alignment check (does every module target its approved finding?)")
    al_rows = [
        {
            "finding": r["finding_id"],
            "finding title": r["finding_title"],
            "module": r["module_title"],
            "targets the diagnosed behavior": "✅"
            if r["target_matches_behavior"]
            else "⚠️ check",
            "shared terms": ", ".join(r["shared_terms"]),
            "knowledge check matches behavior": "✅"
            if r.get("quiz_matches_behavior")
            else "⚠️ check",
            "objective": "✅" if r["has_objective"] else "—",
            "scenario": "✅" if r["has_scenario"] else "—",
            "role-play ≥4 turns": "✅" if r["has_roleplay"] else "—",
            "scoring criteria": "✅" if r["has_scoring"] else "—",
        }
        for r in result["alignment"]
    ]
    st.dataframe(pd.DataFrame(al_rows), width="stretch", hide_index=True)

    # ---- exports for the presentation ----
    def training_markdown(result, approval):
        lines = [
            "# Targeted training package — generated from approved QA diagnosis",
            "",
        ]
        if approval:
            lines += [
                f"Approved by {approval.get('approved_by')} at {approval.get('approved_at')}; "
                f"findings {', '.join(approval.get('approved_findings', []))}.",
                "",
            ]
        for mod in result["modules"]:
            f = by_id.get(mod["finding_id"], {})
            lines += [
                f"## {mod['finding_id']} → {mod['module_title']}",
                f"*Fixes:* {f.get('title', '')} — {f.get('behavior', '')} ({f.get('defect_rate_pct', '?')}% defect rate; root cause: {f.get('root_cause_type', '')})",
                f"*Delivery:* {mod['delivery']}, {mod.get('duration_minutes', '?')} min",
                "",
                f"**Learning objective:** {mod['learning_objective']}",
                "",
                f"**Why this fixes the gap:** {mod['why_this_fixes_the_gap']}",
                "",
                "### Instruction",
            ]
            lines += [f"- {s}" for s in mod["instruction"]]
            kc = mod["knowledge_check"]
            lines += ["", "### Knowledge check", kc.get("question", "")] + [
                f"- {o}" for o in kc.get("options", [])
            ]
            lines += [
                f"Correct: {kc.get('correct', '')} — {kc.get('feedback', '')}",
                "",
            ]
            ex = mod["hands_on_exercise"]
            lines += [
                f"### Hands-on exercise: {ex.get('title', '')}",
                f"Trainee: {ex.get('trainee_instructions', '')}",
                f"Facilitator: {ex.get('facilitator_instructions', '')}",
                "Success criteria:",
            ]
            lines += [f"- {s}" for s in ex.get("success_criteria", [])]
            sc = mod["simulation_scenario"]
            lines += [
                "",
                "### Simulation scenario",
                f"Call driver: {sc.get('call_driver', '')}",
                f"Member persona: {sc.get('member_persona', '')}",
                f"Member emotion: {sc.get('member_emotion', '')}",
                f"Context: {sc.get('scenario_context', '')}",
                f"Behavioral target: {sc.get('behavioral_target', '')}",
                f"Expected agent response: {sc.get('expected_agent_response', '')}",
                "Scoring criteria:",
            ]
            lines += [f"- {s}" for s in sc.get("scoring_criteria", [])]
            lines += ["", "### Role-play script"]
            for turn in mod["roleplay_script"]:
                spk = str(turn.get("speaker", "")).upper()
                if spk == "BRANCH":
                    lines.append(
                        f"- BRANCH — if demonstrated: {turn.get('if_agent_demonstrates_behavior', '')} | if not: {turn.get('if_agent_does_not', '')}"
                    )
                else:
                    note = (
                        f"  _(coach: {turn['coach_note']})_"
                        if turn.get("coach_note")
                        else ""
                    )
                    lines.append(f"- **{spk}:** {turn.get('line', '')}{note}")
            lines.append("")
        return "\n".join(lines)

    d1, d2 = st.columns(2)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    d1.download_button(
        "Download training package (.md)",
        training_markdown(result, st.session_state.approval),
        file_name=f"training_package_{stamp}.md",
        mime="text/markdown",
        width="stretch",
    )
    d2.download_button(
        "Download full run (.json)",
        json.dumps(
            {
                "diagnosis": diag,
                "approval": st.session_state.approval,
                "training": result,
            },
            indent=2,
            default=str,
        ),
        file_name=f"coaching_run_{stamp}.json",
        mime="application/json",
        width="stretch",
    )

