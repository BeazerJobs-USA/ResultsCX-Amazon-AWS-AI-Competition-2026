"""Loading data, running the analysis, and generating coaching packages.

Nothing in this module draws to the page. Each function changes session state
through ``workflow.operations`` or ``pipeline`` and either returns or raises one
of the exceptions in :data:`ACTION_ERRORS`, which the calling page shows next
to the control that was used.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

import get_data
import pipeline
import sample_walkthrough
from harness_client import HarnessError, HarnessSession, get_config
from ui import state
from workflow import coaching, common, intake, operations, review
from workflow.common import WorkflowError

# Everything a page should catch and display instead of letting it reach Streamlit.
ACTION_ERRORS = (HarnessError, pipeline.PipelineError, get_data.DataError, WorkflowError, intake.UploadError)
_PERSISTED = ("flags", "decisions", "packages", "trackers", "audit")


# -- datasets ----------------------------------------------------------------------------
def _install(dataset: dict[str, Any], event: str) -> None:
    state.reset_run()
    st.session_state["data"] = dataset
    st.session_state["store"] = operations.new_store(dataset["records"])
    name, role = state.actor()
    operations.log_event(
        st.session_state["store"],
        event,
        actor=name,
        role=role,
        subject_type="dataset",
        subject_id=dataset["source_kind"],
        details={
            "source": dataset.get("source_label", ""),
            "records": dataset["totals"]["line_items"],
            "fingerprint": pipeline.data_fingerprint(dataset),
        },
    )


def ensure_data() -> None:
    """Load the built-in synthetic dataset the first time the app runs."""
    if st.session_state["data"] is not None or st.session_state["data_error"]:
        return
    try:
        load_builtin()
    except get_data.DataError as exc:
        st.session_state["data_error"] = str(exc)


def load_builtin() -> None:
    _install(get_data.get_all_data(source_override=state.SYNTHETIC), "dataset.loaded")


def load_private() -> None:
    """Read the opt-in local exports. Refused in the offline demonstration."""
    if state.mode() == state.DEMO:
        raise WorkflowError("The offline demonstration always uses the built-in synthetic dataset.")
    if not get_data.private_source_enabled():
        raise get_data.DataError(
            "Local exports are disabled. See docs/SECURITY.md for the two settings that enable them."
        )
    _install(get_data.get_all_data(source_override=state.PRIVATE), "dataset.loaded")


def load_upload(filename: str, raw: bytes) -> list[str]:
    """Validate and load an uploaded file. Returns any non-blocking warnings."""
    dataset = intake.parse_upload(filename, raw)
    _install(dataset, "dataset.uploaded")
    return dataset.get("upload_warnings", [])


def switch_mode(new_mode: str) -> None:
    """Change the analysis source. The offline demonstration returns to synthetic data."""
    if new_mode not in state.MODE_LABELS:
        raise WorkflowError("Unknown analysis source.")
    current = state.data()
    keep_data = current is not None and not (new_mode == state.DEMO and current["source_kind"] != state.SYNTHETIC)
    st.session_state["mode"] = new_mode
    if keep_data and current is not None:
        _install(current, "analysis_source.changed")
    else:
        state.reset_run()
        ensure_data()


# -- readiness ---------------------------------------------------------------------------
def live_config() -> tuple[dict[str, Any] | None, str]:
    """(configuration, '') when the harness settings are valid, else (None, reason)."""
    try:
        return get_config(), ""
    except HarnessError as exc:
        return None, str(exc).splitlines()[0]


def saved_run_note() -> str:
    """One line describing the saved run available for replay, or why there is none."""
    saved = pipeline.load_json("diagnosis")
    if not saved:
        return "No saved run. A live run creates one."
    return f"Saved run from {saved.get('_meta', {}).get('generated_at', 'an unknown time')}."


def analysis_checks(scope: list[str] | None) -> list[dict[str, Any]]:
    """Everything that must hold before the analysis can start, for the current source."""
    dataset = state.data()
    if dataset is None:
        return [{"name": "QA dataset loaded", "ok": False, "detail": "no dataset"}]
    checks = intake.data_checks(dataset)
    checks.append(
        {
            "name": "At least one QA requirement is in scope",
            "ok": bool(scope),
            "detail": f"{len(scope or [])} selected",
        }
    )
    mode = state.mode()
    if mode == state.DEMO:
        supported = sample_walkthrough.supports(dataset)
        checks.append(
            {
                "name": "Offline demonstration content exists for this dataset",
                "ok": supported,
                "detail": "built-in synthetic dataset"
                if supported
                else "only the built-in dataset has handwritten content; other data needs Live mode",
            }
        )
    elif mode == state.LIVE:
        config, reason = live_config()
        checks.append(
            {
                "name": "AgentCore harness is configured",
                "ok": config is not None,
                "detail": f"region {config['region']}" if config else reason,
            }
        )
        allowed = pipeline.cloud_send_allowed(dataset.get("source_kind"))
        checks.append(
            {
                "name": "Sending this data source to AWS is authorized",
                "ok": allowed,
                "detail": "synthetic data" if pipeline.is_synthetic(dataset.get("source_kind")) else (
                    "ALLOW_PRIVATE_CLOUD_SEND=1 is set" if allowed else "uploads and local exports need ALLOW_PRIVATE_CLOUD_SEND=1"
                ),
            }
        )
    else:
        saved = pipeline.load_json("diagnosis")
        matches = bool(saved) and saved.get("_meta", {}).get("data_fingerprint") == pipeline.data_fingerprint(dataset)
        checks.append(
            {
                "name": "A saved run exists for this dataset",
                "ok": matches,
                "detail": saved_run_note() if matches else (
                    "the saved run was made from a different dataset" if saved else "no saved run found"
                ),
            }
        )
    return checks


def default_scope() -> list[str]:
    dataset = state.data()
    return [b["id"] for b in intake.failed_requirements(dataset)] if dataset else []


# -- analysis ----------------------------------------------------------------------------
def run_analysis(
    scope: list[str],
    max_findings: int = 5,
    on_text: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Produce findings for the requirements in scope, from the current analysis source."""
    name, role = state.actor()
    common.require(role, "run_analysis")
    dataset = state.data()
    failing = [c for c in analysis_checks(scope) if not c["ok"]]
    if dataset is None or failing:
        raise pipeline.PipelineError(
            "The analysis cannot start: " + "; ".join(f"{c['name']} ({c['detail']})" for c in failing)
        )
    everything = default_scope()
    focus = None if set(scope) >= set(everything) else [b for b in everything if b in scope]
    mode = state.mode()
    restored: dict[str, Any] | None = None
    training = None
    session = None
    if mode == state.DEMO:
        diagnosis = sample_walkthrough.demo_diagnosis(dataset, focus)
    elif mode == state.LIVE:
        # One harness session carries the diagnosis and the later coaching prompts.
        session = HarnessSession(
            tools={"get_all_data": lambda **_kwargs: pipeline.build_safe_model_payload(dataset)}
        )
        diagnosis = pipeline.run_diagnosis(
            session, dataset, max_findings=max_findings, on_text=on_text, focus_behavior_ids=focus
        )
    else:
        restored = pipeline.load_saved_run(dataset)
        diagnosis, training = restored["diagnosis"], restored["training"]

    state.clear_analysis()
    st.session_state["diagnosis"] = diagnosis
    st.session_state["training"] = training
    st.session_state["session"] = session
    store = state.store()
    if restored is not None:
        _restore_review(store, restored, diagnosis, training)
    operations.log_event(
        store,
        "analysis.loaded",
        actor=name,
        role=role,
        subject_type="analysis",
        subject_id=mode,
        details={
            "findings": [f["id"] for f in diagnosis["findings"]],
            "scope": focus or "all failed requirements",
            "source": state.MODE_LABELS[mode],
        },
    )
    persist_review()
    return diagnosis


def _restore_review(
    store: dict[str, Any],
    restored: dict[str, Any],
    diagnosis: dict[str, Any],
    training: dict[str, Any] | None,
) -> None:
    """Rebuild the case store for a replayed run from what was saved with it."""
    saved_store = (restored.get("review") or {}).get("store")
    if isinstance(saved_store, dict):
        for key in _PERSISTED:
            if key in saved_store:
                store[key] = saved_store[key]
    st.session_state["legacy_approval"] = None if saved_store else restored.get("legacy_approval")
    findings = {f["id"]: f for f in (training or {}).get("approved_findings", [])}
    for module in (training or {}).get("modules", []):
        finding_id = module["finding_id"]
        if finding_id in store["packages"]:
            continue
        # A run saved before packages were tracked: describe it from the module itself.
        components = coaching.normalize_components(module.get("components") or pipeline.CORE_COMPONENTS)
        employees = list(findings.get(finding_id, {}).get("agents_failed", []))
        store["packages"][finding_id] = {
            "finding_id": finding_id,
            "components": components,
            "employees": employees,
            "source": state.REPLAY,
            "decision_revision": 0,
            "generated_by": "",
            "stale": False,
        }
        store["trackers"][finding_id] = coaching.new_tracker(employees, components)


def persist_review() -> None:
    """Save decisions with a live run so it can be replayed. Referrals are never written."""
    dataset = state.data()
    if state.mode() != state.LIVE or dataset is None:
        return
    store = state.store()
    pipeline.save_json(
        "review",
        {
            "data_fingerprint": pipeline.data_fingerprint(dataset),
            "saved_at": common.now_iso(),
            "store": {key: store[key] for key in _PERSISTED},
        },
        source_kind=dataset.get("source_kind", "synthetic"),
        keep_history=False,
    )


# -- coaching packages -------------------------------------------------------------------
def approved_finding(finding_id: str) -> dict[str, Any] | None:
    """The finding as the supervisor approved it, or None if it was not sent to coaching."""
    approval_mode = "synthetic_demo" if state.mode() == state.DEMO else "human_review"
    approved = review.approved_findings(
        st.session_state["diagnosis"] or {"findings": []}, state.store()["decisions"], approval_mode
    )
    return next((f for f in approved if f["id"] == finding_id), None)


def generate_package(
    finding_id: str,
    components: list[str],
    employees: list[str],
    on_text: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create (or recreate) the coaching package for one approved finding."""
    name, role = state.actor()
    common.require(role, "generate_coaching")
    if state.read_only():
        raise WorkflowError("A replayed run cannot generate new packages.")
    dataset = state.data()
    finding = approved_finding(finding_id)
    if dataset is None or finding is None:
        raise WorkflowError(
            "A coaching package needs a supervisor-approved finding whose next action is "
            "coaching or remediation."
        )
    assigned = [e for e in employees if e in finding.get("agents_failed", [])]
    if not assigned:
        raise WorkflowError("Assign at least one employee who missed this requirement.")
    chosen = coaching.normalize_components(components)
    existing = st.session_state["training"]
    if state.mode() == state.DEMO:
        result = sample_walkthrough.demo_training(
            [finding], dataset, components={finding_id: chosen}, merge_into=existing
        )
    else:
        session = st.session_state.get("session")
        if session is None:  # for example after a page reload: open a new harness session
            session = HarnessSession()
            st.session_state["session"] = session
        result = pipeline.run_training(
            session, [finding], dataset, on_text=on_text, components={finding_id: chosen}, merge_into=existing
        )
    st.session_state["training"] = result
    failure = next((e for e in result["errors"] if e["finding_id"] == finding_id), None)
    if failure:
        raise pipeline.PipelineError(failure["error"])
    operations.register_package(
        state.store(),
        finding,
        components=chosen,
        employees=assigned,
        source=state.mode(),
        actor=name,
        role=role,
    )
    persist_review()
    return next(m for m in result["modules"] if m["finding_id"] == finding_id)
