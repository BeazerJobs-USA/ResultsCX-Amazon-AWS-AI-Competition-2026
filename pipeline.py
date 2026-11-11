"""
pipeline.py - the model-facing steps of the four-stage workflow, as plain Python.

    Stage 1  Intake and evidence     get_data / workflow.intake        (Python, offline)
    Stage 2  Diagnosis and risk      run_diagnosis() -> validate_diagnosis()   (one model call)
    Stage 3  Supervisor review       workflow.review / workflow.operations     (people, no AI)
    Stage 4  Coaching and practice   run_training()                    (one model call per package)

Everything a model returns is validated here before it is shown: numbers are
replaced with the values computed in Python, cited record IDs and quotes are
checked against the loaded records, and structurally incomplete output is
rejected. ``alignment_check()`` is a deterministic keyword check that a package
targets its finding. Measuring whether coaching changed later QA results is
outside this prototype.

Results from synthetic data can be saved under outputs/ for offline replay. Any
other data source needs an explicit opt-in because those JSON files are not
encrypted.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import get_data
import prompts
import security_controls
from harness_client import HarnessError

log = logging.getLogger("pipeline")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
HISTORY_DIR = OUTPUT_DIR / "history"
# Timestamped copies kept per artifact name. Older copies are deleted on save so the
# folder cannot grow without limit.
HISTORY_LIMIT = 20

ROOT_CAUSE_TYPES = ["Knowledge Gap", "Skill Gap", "Process Gap", "Will Gap"]
INTERVENTIONS = ["simulation", "eLearning/ILT", "coaching", "escalate to operations"]
COVERAGE = ["Yes", "Partial", "No"]
CONFIDENCE = ["high", "medium", "low"]


class PipelineError(Exception):
    """Raised when a pipeline stage cannot produce a valid result."""


# ======================================================================================
# Small utilities
# ======================================================================================
def index_records(data):
    return {r["record_id"]: r for r in data["records"]}


def index_behaviors(data):
    return {b["id"]: b for b in data["behaviors"]}


def data_fingerprint(data):
    """Identify a loaded dataset without saving its underlying records."""
    encoded = json.dumps(data["records"], sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def extract_json(text):
    """Decode the first complete JSON object without assuming the last brace closes it."""
    if not text or not text.strip():
        raise PipelineError("The model returned an empty reply.\nFix: retry the stage.")
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    for i, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            result, _ = decoder.raw_decode(cleaned, i)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise PipelineError(
        "The model did not return a complete JSON object. Check the response format or retry."
    )


def _pick(value, allowed, default):
    """Normalize a supported label, allowing a short parenthetical explanation."""
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    for a in allowed:
        if v == a.lower() or v.startswith(a.lower() + " ("):
            return a
    return default


def is_synthetic(source_kind: str | None) -> bool:
    """Only the checked-in invented fixture is treated as safe to store or send.

    Uploaded files and local exports are both handled as private, whatever they contain.
    """
    return (source_kind or "synthetic") == "synthetic"


def storage_allowed(source_kind: str | None) -> bool:
    """Whether derived output for this data source may be written to disk."""
    return is_synthetic(source_kind) or os.environ.get("ALLOW_PRIVATE_OUTPUT_STORAGE") == "1"


def cloud_send_allowed(source_kind: str | None) -> bool:
    """Whether this data source may be sent to AWS."""
    return is_synthetic(source_kind) or os.environ.get("ALLOW_PRIVATE_CLOUD_SEND") == "1"


def save_json(name, obj, source_kind="synthetic", keep_history=True):
    """Write ``outputs/latest_<name>.json`` (plus a timestamped copy) if storage is allowed."""
    if not storage_allowed(source_kind):
        return None
    OUTPUT_DIR.mkdir(exist_ok=True)
    latest = OUTPUT_DIR / f"latest_{name}.json"
    text = json.dumps(obj, indent=2, default=str)
    latest.write_text(text, encoding="utf-8")
    if keep_history:
        HISTORY_DIR.mkdir(exist_ok=True)
        stamped = HISTORY_DIR / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{name}.json"
        stamped.write_text(text, encoding="utf-8")
        for stale in sorted(HISTORY_DIR.glob(f"*_{name}.json"))[:-HISTORY_LIMIT]:
            stale.unlink(missing_ok=True)
    return latest


def load_json(name):
    """Read ``outputs/latest_<name>.json``. A missing or unreadable file returns None."""
    p = OUTPUT_DIR / f"latest_{name}.json"
    if not p.exists():
        return None
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A damaged file must not stop the application from starting.
        log.warning("saved %s could not be read and was ignored", name)
        return None
    return loaded if isinstance(loaded, dict) else None


def ensure_cloud_upload_authorized(data):
    """Prevent accidental external processing of incomplete or private data."""
    failed = [name for name, passed, _ in data.get("checks", []) if not passed]
    if failed:
        raise PipelineError("Data validation failed: " + ", ".join(failed))
    if not cloud_send_allowed(data.get("source_kind")):
        raise PipelineError(
            "Private QA data cannot be sent to AWS by default. Obtain written authorization, "
            "review redaction, and explicitly set ALLOW_PRIVATE_CLOUD_SEND=1."
        )


def known_staff_names(data):
    """Read local identities only for masking; never return them to the model."""
    return list(data.get("agent_map", {}).values()) + list(
        data.get("team_lead_map", {}).values()
    )


def build_safe_model_payload(data):
    """Second privacy checkpoint, after Excel parsing but BEFORE an AWS call.

    The same helper protects both the diagnosis prompt and the Bedrock tool
    callback (so the model cannot request the unfiltered payload instead).
    """
    payload = get_data.build_model_payload(data)
    safe_payload, _count = security_controls.sanitize_payload(
        payload, known_staff_names(data)
    )
    return safe_payload


def sanitize_model_prompt(prompt, data):
    """Check final text too: supervisor edits and quotes bypass Stage 1 summaries."""
    safe_prompt, _count = security_controls.sanitize_text(
        prompt, known_staff_names(data)
    )
    get_data.assert_no_real_names(safe_prompt, data)
    return safe_prompt


# ======================================================================================
# STAGE 2 - Needs analysis
# ======================================================================================
_SAFE_FINDING_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,11}")


def validate_diagnosis(diagnosis, data):
    """Make the AI's findings safe to display:
       * attach the software-computed metrics for each finding's behavior (numbers never come from the model)
       * verify every cited record ID exists and belongs to that behavior; keep the real comment next to the quote
       * normalise labels so the UI's select boxes always have a valid value
    Mutates and returns ``diagnosis``."""
    if not isinstance(diagnosis, dict):
        raise PipelineError("Diagnosis must be a JSON object")
    records = index_records(data)
    behaviors = index_behaviors(data)
    if not isinstance(diagnosis.get("findings"), list) or not diagnosis["findings"]:
        raise PipelineError(
            "The diagnosis JSON has no 'findings' list.\nType: model output problem. Fix: retry the analysis."
        )
    seen_ids: set[str] = set()
    for i, f in enumerate(diagnosis["findings"], start=1):
        if not isinstance(f, dict):
            raise PipelineError(
                f"Diagnosis finding {i} is not a JSON object. Retry the AI analysis."
            )
        # Finding ids become widget keys, file names, and audit subjects, so they must be
        # short, plain, and unique. A model can repeat an id or return something odd.
        finding_id = str(f.get("id") or "").strip()
        if not _SAFE_FINDING_ID.fullmatch(finding_id) or finding_id in seen_ids:
            finding_id, suffix = f"F{i}", 1
            while finding_id in seen_ids:
                suffix += 1
                finding_id = f"F{i}-{suffix}"
        f["id"] = finding_id
        seen_ids.add(finding_id)
        b = behaviors.get(str(f.get("behavior_id", "")).strip().upper())
        if b is None:
            # Only use a sufficiently specific title as a fallback; never match empty text.
            name = str(f.get("title", "")).lower()
            if len(name.strip()) >= 10:
                b = next(
                    (
                        x
                        for x in data["behaviors"]
                        if x["behavior"].lower() in name
                        or name in x["behavior"].lower()
                    ),
                    None,
                )
        f["metrics_verified"] = b is not None
        if b:
            f["behavior_id"] = b["id"]
            f["behavior"] = b["behavior"]
            f["section"] = b["section"]
            f["source"] = b["source"]
            f["defect_rate_pct"] = b["defect_rate_pct"]
            f["failed"] = b["failed"]
            f["evaluated"] = b["evaluated"]
            f["agents_failed"] = b["agents_failed"]
            f["computed_pattern"] = b["pattern"]
        else:
            f.setdefault("behavior", f.get("title", "unknown behavior"))
            f.setdefault("section", "?")
        f["root_cause_type"] = _pick(
            f.get("root_cause_type"), ROOT_CAUSE_TYPES, "Skill Gap"
        )
        f["recommended_intervention"] = _pick(
            f.get("recommended_intervention"), INTERVENTIONS, "coaching"
        )
        f["curriculum_coverage"] = _pick(
            f.get("curriculum_coverage"), COVERAGE, "Partial"
        )
        f["confidence"] = _pick(f.get("confidence"), CONFIDENCE, "medium")
        recommended = f.get("training_recommended", False)
        f["training_recommended"] = (
            recommended
            if isinstance(recommended, bool)
            else str(recommended).strip().lower() in {"true", "yes", "1"}
        )
        for key in (
            "title",
            "observed",
            "root_cause",
            "pattern",
            "curriculum_note",
            "confidence_note",
        ):
            f[key] = str(f.get(key) or "")
        actions = f.get("corrective_actions")
        f["corrective_actions"] = (
            [str(a).strip() for a in actions if str(a).strip()][:6] if isinstance(actions, list) else []
        )

        verified = 0
        evidence = f.get("evidence") if isinstance(f.get("evidence"), list) else []
        cleaned = []
        for e in evidence:
            if not isinstance(e, dict):
                continue
            rid = str(e.get("record_id", "")).strip().upper()
            rec = records.get(rid)
            quote = str(e.get("quote") or "").strip()
            same_behavior = bool(
                rec
                and b
                and rec["behavior"] == b["behavior"]
                and rec.get("source") == b.get("source")
            )
            comment = str(rec.get("comment") or "") if rec else ""
            # Compare to what Bedrock actually saw. The second privacy pass
            # may have replaced an identifier with [EMAIL] or [IDENTIFIER].
            safe_comment, _ = security_controls.sanitize_text(
                comment, known_staff_names(data)
            )
            quote_matches = bool(
                quote
                and " ".join(quote.split()).casefold()
                in " ".join(safe_comment.split()).casefold()
            )
            is_verified = same_behavior and quote_matches
            item = {
                "record_id": rid,
                "quote": quote,
                "verified": is_verified,
                "same_behavior": same_behavior,
                "quote_matches": quote_matches,
            }
            if rec:
                item.update(
                    {
                        "agent": rec["agent"],
                        "date": rec["call_date"],
                        "result": rec["result"],
                        "record_behavior": rec["behavior"],
                        "comment": rec["comment"],
                        "same_behavior": same_behavior,
                    }
                )
            if is_verified:
                verified += 1
            cleaned.append(item)
        f["evidence"] = cleaned
        f["evidence_verified"] = verified
        f["evidence_total"] = len(cleaned)
    diagnosis.setdefault("summary", "")
    for key in ("not_a_training_problem", "open_questions"):
        items = diagnosis.get(key)
        diagnosis[key] = [str(x) for x in items] if isinstance(items, list) else []
    return diagnosis


def run_diagnosis(
    session,
    data: dict[str, Any],
    max_findings: int = 5,
    on_text: Callable[[str], None] | None = None,
    focus_behavior_ids: Iterable[str] | None = None,
):
    """Bedrock call #1. Returns the validated diagnosis dict (also saved to outputs/).

    ``focus_behavior_ids`` limits the analysis to the QA requirements the supervisor
    selected. Findings the model returns for any other behavior are dropped.
    """
    ensure_cloud_upload_authorized(data)
    payload = build_safe_model_payload(data)
    focus = sorted(set(focus_behavior_ids)) if focus_behavior_ids else None
    prompt = sanitize_model_prompt(
        prompts.build_diagnosis_prompt(
            payload, max_findings=max_findings, focus_behavior_ids=focus
        ),
        data,
    )
    text, turn = session.ask(
        prompt, system_prompt=prompts.SYSTEM_PROMPT, on_text=on_text
    )
    diagnosis = validate_diagnosis(extract_json(text), data)
    if focus:
        in_scope = [f for f in diagnosis["findings"] if f.get("behavior_id") in focus]
        diagnosis["out_of_scope_dropped"] = len(diagnosis["findings"]) - len(in_scope)
        if not in_scope:
            raise PipelineError(
                "The model returned no findings for the selected QA requirements. "
                "Retry the analysis or widen the scope."
            )
        diagnosis["findings"] = in_scope
    diagnosis["_meta"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "focus_behavior_ids": focus,
        "session_id": session.session_id,
        "input_tokens": turn.input_tokens,
        "output_tokens": turn.output_tokens,
        "latency_ms": turn.latency_ms,
        "stop_reason": turn.stop_reason,
        "prompt_chars": len(prompt),
        "raw_text": text,
        "source_kind": data.get("source_kind", "synthetic"),
        "data_fingerprint": data_fingerprint(data),
    }
    save_json("diagnosis", diagnosis, source_kind=data.get("source_kind", "synthetic"))
    return diagnosis


# ======================================================================================
# STAGE 4 - Training design (one call per approved finding)
# ======================================================================================
_STOPWORDS = set(
    "the a an and or of to in for on with as by at is are was were be this that it its from "
    "did agent member all any not no yes value skills use offer".split()
)


def _keywords(text):
    return {
        w for w in re.findall(r"[a-z]{4,}", str(text).lower()) if w not in _STOPWORDS
    }


def related_outline_entries(finding, outline, limit=12):
    """Cheap relevance: outline entries sharing the most keywords with the finding."""
    kw = (
        _keywords(finding.get("behavior", ""))
        | _keywords(finding.get("title", ""))
        | _keywords(finding.get("root_cause", ""))
    )
    scored = []
    for e in outline["entries"]:
        text = " ".join(str(e.get(k) or "") for k in ("topic", "detail", "important"))
        score = len(kw & _keywords(text))
        if score:
            scored.append((score, e))
    scored.sort(key=lambda s: -s[0])
    lines = []
    for _, e in scored[:limit]:
        parts = [
            e["week"],
            e.get("day") or "",
            e.get("topic") or "",
            e.get("detail") or "",
        ]
        if e.get("resource"):
            parts.append(f"(resource: {e['resource']})")
        lines.append(" / ".join(p for p in parts if p))
    return "\n".join(lines)


def evidence_lines_for(finding, data, max_items=8):
    """Only verified, same-behavior evidence, plus other FAIL comments on that behavior."""
    records = index_records(data)
    lines, seen = [], set()
    for e in finding.get("evidence", []):
        if not e.get("verified"):
            continue
        rec = records.get(e.get("record_id"))
        if (
            rec
            and rec["record_id"] not in seen
            and rec["behavior"] == finding.get("behavior")
            and rec.get("source") == finding.get("source")
        ):
            seen.add(rec["record_id"])
            lines.append(
                f"{rec['record_id']} | {'FAIL' if rec['result'] == 'No' else 'PASS'} | {rec['comment']}"
            )
    for rec in data["records"]:
        if len(lines) >= max_items:
            break
        if (
            rec["behavior"] == finding.get("behavior")
            and rec.get("source") == finding.get("source")
            and rec["result"] == "No"
            and rec["comment"].strip()
            and rec["record_id"] not in seen
        ):
            seen.add(rec["record_id"])
            lines.append(f"{rec['record_id']} | FAIL | {rec['comment']}")
    return "\n".join(lines[:max_items])


# Components of the original module. ``None`` means "check these".
CORE_COMPONENTS = (
    "targeted_coaching",
    "knowledge_check",
    "guided_practice",
    "customer_scenario",
    "roleplay",
)


def ensure_module_complete(module, components: Iterable[str] | None = None):
    """Reject structurally incomplete model output before presenting it as usable training.

    Only the components the supervisor asked for are checked. ``None`` checks the
    original five-part module.
    """
    wanted = set(CORE_COMPONENTS if components is None else components) | {"targeted_coaching"}
    missing = []
    for key in ("module_title", "learning_objective", "why_this_fixes_the_gap", "delivery"):
        if not str(module.get(key) or "").strip():
            missing.append(key)
    if not module.get("instruction"):
        missing.append("instruction")
    if "knowledge_check" in wanted:
        quiz = module.get("knowledge_check") or {}
        options = quiz.get("options")
        if not (quiz.get("question") and isinstance(options, list) and len(options) >= 2
                and all(isinstance(option, str) and option.strip() for option in options)
                and quiz.get("correct") and quiz.get("feedback")):
            missing.append("knowledge_check")
    if "guided_practice" in wanted:
        exercise = module.get("hands_on_exercise") or {}
        if not (exercise.get("trainee_instructions") and exercise.get("facilitator_instructions")
                and isinstance(exercise.get("success_criteria"), list)
                and exercise["success_criteria"]):
            missing.append("hands_on_exercise")
    if "customer_scenario" in wanted:
        scenario = module.get("simulation_scenario") or {}
        if not (scenario.get("behavioral_target") and scenario.get("scenario_context")
                and isinstance(scenario.get("scoring_criteria"), list)
                and scenario["scoring_criteria"]):
            missing.append("simulation_scenario")
    if "roleplay" in wanted:
        roleplay = module.get("roleplay_script") or []
        if len(roleplay) < 4 or not any(t.get("speaker", "").upper() == "BRANCH" for t in roleplay):
            missing.append("roleplay_script")
    if "policy_refresher" in wanted:
        refresher = module.get("policy_refresher") or {}
        if not (refresher.get("summary") and refresher.get("key_points")):
            missing.append("policy_refresher")
    if "coaching_notes" in wanted:
        if not (module.get("coaching_notes") or {}).get("questions_to_ask"):
            missing.append("coaching_notes")
    if "remediation_plan" in wanted:
        plan = module.get("remediation_plan") or {}
        if not (plan.get("goal") and plan.get("steps")):
            missing.append("remediation_plan")
    if "follow_up_evaluation" in wanted:
        follow_up = module.get("follow_up_evaluation") or {}
        if not (follow_up.get("what_to_evaluate") and follow_up.get("success_measure")):
            missing.append("follow_up_evaluation")
    if missing:
        raise PipelineError("Incomplete training module: " + ", ".join(missing))
    return module


# Three or more digits: record numbering continues past 999 in larger exports.
RECORD_ID_PATTERN = re.compile(r"\b([A-Z]{2}-\d{3,})\b")


def verify_module_citations(module, finding, data):
    """Check record IDs referenced anywhere in a training module.

    This is a structural cross-check, not a verification of each generated claim.
    Each citation is checked twice:
        exists        - the record ID is a real row in the QA exports
        same_behavior - that row is about the behavior this module is supposed to fix
    Adds ``citations`` / ``citations_verified`` / ``citations_total`` to the module and returns it.
    """
    records = {r["record_id"]: r for r in data["records"]}
    # Scan the whole module (minus _meta) so a citation anywhere - objective, instruction,
    # scoring criteria, coach notes - is caught, not just the ones in a known field.
    scanned = {k: v for k, v in module.items() if k != "_meta"}
    found = sorted(set(RECORD_ID_PATTERN.findall(json.dumps(scanned, default=str))))

    citations = []
    for rid in found:
        rec = records.get(rid)
        item = {"record_id": rid, "exists": rec is not None}
        if rec:
            item.update(
                {
                    "agent": rec["agent"],
                    "date": rec["call_date"],
                    "result": rec["result"],
                    "record_behavior": rec["behavior"],
                    "comment": rec["comment"],
                    "same_behavior": (
                        rec["behavior"] == finding.get("behavior", "")
                        and (
                            not finding.get("source")
                            or rec.get("source") == finding.get("source")
                        )
                    ),
                }
            )
        citations.append(item)
    module["citations"] = citations
    module["citations_verified"] = sum(
        1 for c in citations if c["exists"] and c.get("same_behavior")
    )
    module["citations_total"] = len(citations)
    module["citations_off_behavior"] = [
        c["record_id"] for c in citations if c["exists"] and not c.get("same_behavior")
    ]
    return module


def validate_module(module, finding, data=None):
    if not isinstance(module, dict):
        raise PipelineError("Training module must be a JSON object")
    module["finding_id"] = finding["id"]
    module["behavior"] = finding.get("behavior", "")
    for key in (
        "module_title",
        "learning_objective",
        "why_this_fixes_the_gap",
        "delivery",
    ):
        module[key] = str(module.get(key) or "")
    for key in ("instruction", "existing_material_to_reuse", "roleplay_script"):
        if not isinstance(module.get(key), list):
            module[key] = []
    module["instruction"] = [str(item) for item in module["instruction"]]
    module["existing_material_to_reuse"] = [
        str(item) for item in module["existing_material_to_reuse"]
    ]
    module["roleplay_script"] = [
        item for item in module["roleplay_script"] if isinstance(item, dict)
    ]
    for turn in module["roleplay_script"]:
        # An LLM can return a number/null where a speaker label is expected.
        # Normalize it so validation shows an error instead of crashing the UI.
        turn["speaker"] = str(turn.get("speaker") or "").upper()
    for key in ("knowledge_check", "hands_on_exercise", "simulation_scenario"):
        if not isinstance(module.get(key), dict):
            module[key] = {}
    sc = module["simulation_scenario"]
    if not isinstance(sc.get("scoring_criteria"), list):
        sc["scoring_criteria"] = (
            [str(sc.get("scoring_criteria") or "")]
            if sc.get("scoring_criteria")
            else []
        )
    _normalize_optional_sections(module)
    if data is not None:
        verify_module_citations(module, finding, data)
    return module


def _text_list(value: Any, limit: int = 12) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()][:limit] if isinstance(value, list) else []


def _normalize_optional_sections(module: dict[str, Any]) -> None:
    """Give the optional package sections a predictable shape, whatever a model returned."""
    for key in ("additional_knowledge_checks", "roleplay_decisions"):
        value = module.get(key)
        module[key] = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    for key, list_fields in (
        ("policy_refresher", ("key_points", "confirm_with_policy_owner")),
        ("coaching_notes", ("questions_to_ask", "what_good_looks_like", "commitments_to_agree")),
        ("follow_up_evaluation", ()),
    ):
        section = module.get(key) if isinstance(module.get(key), dict) else {}
        for field in list_fields:
            section[field] = _text_list(section.get(field))
        module[key] = {k: (v if isinstance(v, list) else str(v or "")) for k, v in section.items()}
    plan = module.get("remediation_plan") if isinstance(module.get("remediation_plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    module["remediation_plan"] = {
        "goal": str(plan.get("goal") or ""),
        "steps": [
            {key: str(step.get(key) or "") for key in ("step", "owner", "timing")}
            for step in steps
            if isinstance(step, dict) and str(step.get("step") or "").strip()
        ][:12],
        "escalation_trigger": str(plan.get("escalation_trigger") or ""),
    } if plan else {}


def run_training(
    session,
    approved_findings: list[dict[str, Any]],
    data: dict[str, Any],
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    components: dict[str, list[str]] | list[str] | None = None,
    merge_into: dict[str, Any] | None = None,
):
    """Bedrock call per approved finding. Returns {"modules": [...], "errors": [...]} and saves it.

    ``components`` is the package content the supervisor selected, either one list for
    every finding or a mapping of finding id to list. ``None`` requests the original
    five-part module. ``merge_into`` is an earlier result to add these packages to, so
    packages can be generated (or retried) one finding at a time.

    A failed module is recorded without discarding other completed modules.
    """
    ensure_cloud_upload_authorized(data)
    modules, errors = [], []
    for i, finding in enumerate(approved_findings, start=1):
        if on_progress:
            on_progress(i, len(approved_findings), finding)
        chosen = components.get(finding["id"]) if isinstance(components, dict) else components
        prompt = sanitize_model_prompt(
            prompts.build_training_prompt(
                finding,
                evidence_lines_for(finding, data),
                related_outline_entries(finding, data["outline"]),
                components=chosen,
            ),
            data,
        )
        try:
            text, turn = session.ask(
                prompt, system_prompt=prompts.SYSTEM_PROMPT, on_text=on_text
            )
            module = validate_module(extract_json(text), finding, data)
            ensure_module_complete(module, chosen)
            module["components"] = list(chosen) if chosen is not None else list(CORE_COMPONENTS)
            module["_meta"] = {
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "latency_ms": turn.latency_ms,
                "raw_text": text,
            }
            modules.append(module)
        except (HarnessError, PipelineError) as e:
            errors.append(
                {
                    "finding_id": finding["id"],
                    "title": finding.get("title", ""),
                    "error": str(e),
                }
            )
    result = {
        "modules": modules,
        "errors": errors,
        "approved_findings": approved_findings,
        "alignment": alignment_check(approved_findings, modules),
        "_meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "session_id": session.session_id,
            "source_kind": data.get("source_kind", "synthetic"),
            "data_fingerprint": data_fingerprint(data),
        },
    }
    result = merge_training(merge_into, result)
    save_json("training", result, source_kind=data.get("source_kind", "synthetic"))
    return result


def merge_training(existing: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Add newly generated packages to an earlier result, replacing same-finding entries."""
    if not existing:
        return new
    regenerated = {m["finding_id"] for m in new["modules"]}
    attempted = regenerated | {e["finding_id"] for e in new["errors"]}
    findings = {f["id"]: f for f in existing.get("approved_findings", [])}
    findings.update({f["id"]: f for f in new.get("approved_findings", [])})
    # A failed regeneration keeps the earlier package; only a new package replaces it.
    modules = [m for m in existing.get("modules", []) if m["finding_id"] not in regenerated] + new["modules"]
    errors = [e for e in existing.get("errors", []) if e["finding_id"] not in attempted] + new["errors"]
    approved = list(findings.values())
    return {
        "modules": modules,
        "errors": errors,
        "approved_findings": approved,
        "alignment": alignment_check(approved, modules),
        "_meta": new.get("_meta", {}),
    }


def load_saved_run(data: dict[str, Any]) -> dict[str, Any]:
    """Load a saved diagnosis and its coaching packages for read-only replay.

    The saved files are checked against the loaded dataset and passed through the same
    validators as fresh model output, so a stale or hand-edited file produces a clear
    error instead of a broken screen.
    """
    fingerprint = data_fingerprint(data)
    diagnosis = load_json("diagnosis")
    if not diagnosis:
        raise PipelineError(
            "No saved run to replay.\nFix: switch to Live mode and run the diagnosis once; "
            "every successful run is saved under outputs/ automatically."
        )
    if diagnosis.get("_meta", {}).get("data_fingerprint") != fingerprint:
        raise PipelineError(
            "The saved diagnosis does not match the loaded QA dataset. "
            "Use the matching source or start a new analysis."
        )
    training = load_json("training")
    if not training:
        raise PipelineError(
            "The saved diagnosis has no completed training package to replay. "
            "Run the full approved flow in Live mode to create one."
        )
    if training.get("_meta", {}).get("data_fingerprint") != fingerprint:
        raise PipelineError("The saved training was created from a different QA dataset.")

    meta = diagnosis.get("_meta", {})
    diagnosis = validate_diagnosis(diagnosis, data)
    diagnosis["_meta"] = meta
    approved = {
        f["id"]: f for f in training.get("approved_findings", []) if isinstance(f, dict) and f.get("id")
    }
    modules = []
    for module in training.get("modules", []):
        finding = approved.get(module.get("finding_id")) if isinstance(module, dict) else None
        if finding:
            components = module.get("components")
            module = validate_module(module, finding, data)
            module["components"] = components if isinstance(components, list) else list(CORE_COMPONENTS)
            modules.append(module)
    training = {
        "modules": modules,
        "errors": [e for e in training.get("errors", []) if isinstance(e, dict) and e.get("finding_id")],
        "approved_findings": list(approved.values()),
        "alignment": alignment_check(list(approved.values()), modules),
        "_meta": training.get("_meta", {}),
    }
    review_state = load_json("review")
    if review_state and review_state.get("data_fingerprint") != fingerprint:
        review_state = None
    return {
        "diagnosis": diagnosis,
        "training": training,
        "review": review_state,
        "legacy_approval": load_json("approval"),
    }


# ======================================================================================
# Alignment check (deterministic keyword heuristic, not a measure of training quality)
# ======================================================================================
def alignment_check(approved_findings, modules):
    """One row per module: does it point at an approved finding, and does its behavioral target
    actually name the diagnosed behavior? Also lists approved findings that got no module."""
    by_id = {f["id"]: f for f in approved_findings}
    rows = []
    covered = set()
    for m in modules:
        fid = m.get("finding_id")
        f = by_id.get(fid)
        target = str(
            (m.get("simulation_scenario") or {}).get("behavioral_target") or ""
        )
        objective = str(m.get("learning_objective") or "")
        quiz = m.get("knowledge_check") or {}
        quiz_text = str(quiz.get("question") or "")
        behavior_kw = _keywords(f.get("behavior", "")) if f else set()
        overlap = behavior_kw & (_keywords(target) | _keywords(objective))
        quiz_overlap = behavior_kw & _keywords(quiz_text)
        rows.append(
            {
                "finding_id": fid,
                "finding_title": f.get("title", "") if f else "(unknown finding)",
                "module_title": m.get("module_title", ""),
                "components": m.get("components"),
                "finding_exists": f is not None,
                "target_matches_behavior": bool(overlap) if f else False,
                "shared_terms": sorted(overlap),
                "quiz_matches_behavior": bool(quiz_overlap) if f else False,
                "quiz_shared_terms": sorted(quiz_overlap),
                "has_objective": bool(objective.strip()),
                "has_scenario": bool(target.strip()),
                "has_roleplay": len(m.get("roleplay_script") or []) >= 4,
                "has_scoring": len(
                    (m.get("simulation_scenario") or {}).get("scoring_criteria") or []
                )
                >= 1,
            }
        )
        if f:
            covered.add(fid)
    for fid, f in by_id.items():
        if fid not in covered:
            rows.append(
                {
                    "finding_id": fid,
                    "finding_title": f.get("title", ""),
                    "module_title": "(no module generated)",
                    "components": None,
                    "finding_exists": True,
                    "target_matches_behavior": False,
                    "shared_terms": [],
                    "quiz_matches_behavior": False,
                    "quiz_shared_terms": [],
                    "has_objective": False,
                    "has_scenario": False,
                    "has_roleplay": False,
                    "has_scoring": False,
                }
            )
    return rows


# ======================================================================================
# Convenience: build the "approved" finding objects from the supervisor's edits
# ======================================================================================
def build_approved_findings(diagnosis, edits, approval_mode="human_review"):
    """edits: {finding_id: {field: value, ..., "include": bool, "supervisor_note": str}}"""
    approved = []
    for f in diagnosis["findings"]:
        e = edits.get(f["id"], {})
        if not e.get("include", f.get("training_recommended", True)):
            continue
        merged = dict(f)
        for key in (
            "title",
            "root_cause_type",
            "root_cause",
            "observed",
            "recommended_intervention",
            "curriculum_coverage",
            "curriculum_note",
            "supervisor_note",
        ):
            if key in e:
                merged[key] = e[key]
        merged["approved"] = True
        merged["approval_mode"] = approval_mode
        merged["approved_at"] = datetime.now().isoformat(timespec="seconds")
        approved.append(merged)
    return approved

