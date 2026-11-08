"""
pipeline.py — the six-step flow from the case study, as plain Python functions.

    1 Data Intake         get_data.get_all_data()               (Python, offline)
    2 Needs Analysis      run_diagnosis()                       (Bedrock call #1)  -> validate_diagnosis()
    3 Human Validation    the supervisor edits/approves in app.py (no AI)
    4 Training Design     run_training()                        (Bedrock call per approved finding)
    5 Alignment Check     alignment_check()                     (Python, deterministic)
    6 Outcome Evaluation  not in scope for the prototype (re-run stage 1 on post-training exports)

Synthetic AI results can be saved under outputs/ for offline replay. Private
results require explicit opt-in because those JSON files are not encrypted.
"""

import json
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

import get_data
import prompts
import security_controls
from harness_client import HarnessError

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
HISTORY_DIR = OUTPUT_DIR / "history"

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


def save_json(name, obj, source_kind="synthetic"):
    if (
        source_kind == "private"
        and os.environ.get("ALLOW_PRIVATE_OUTPUT_STORAGE") != "1"
    ):
        return None
    OUTPUT_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)
    latest = OUTPUT_DIR / f"latest_{name}.json"
    stamped = HISTORY_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.json"
    for p in (latest, stamped):
        p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    return latest


def load_json(name):
    p = OUTPUT_DIR / f"latest_{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_cloud_upload_authorized(data):
    """Prevent accidental external processing of incomplete or private data."""
    failed = [name for name, passed, _ in data.get("checks", []) if not passed]
    if failed:
        raise PipelineError("Data validation failed: " + ", ".join(failed))
    if (
        data.get("source_kind") == "private"
        and os.environ.get("ALLOW_PRIVATE_CLOUD_SEND") != "1"
    ):
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
    for i, f in enumerate(diagnosis["findings"], start=1):
        f.setdefault("id", f"F{i}")
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
            quote_matches = bool(
                quote
                and " ".join(quote.split()).casefold()
                in " ".join(comment.split()).casefold()
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
    diagnosis["not_a_training_problem"] = [
        str(x) for x in diagnosis.get("not_a_training_problem") or []
    ]
    diagnosis["open_questions"] = [
        str(x) for x in diagnosis.get("open_questions") or []
    ]
    return diagnosis


def run_diagnosis(session, data, max_findings=5, on_text=None):
    """Bedrock call #1. Returns the validated diagnosis dict (also saved to outputs/)."""
    ensure_cloud_upload_authorized(data)
    payload = build_safe_model_payload(data)
    prompt = sanitize_model_prompt(
        prompts.build_diagnosis_prompt(payload, max_findings=max_findings), data
    )
    text, turn = session.ask(
        prompt, system_prompt=prompts.SYSTEM_PROMPT, on_text=on_text
    )
    diagnosis = validate_diagnosis(extract_json(text), data)
    diagnosis["_meta"] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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


def ensure_module_complete(module):
    """Reject structurally incomplete model output before presenting it as usable training."""
    missing = []
    for key in ("module_title", "learning_objective", "why_this_fixes_the_gap", "delivery"):
        if not str(module.get(key) or "").strip():
            missing.append(key)
    if not module.get("instruction"):
        missing.append("instruction")
    quiz = module.get("knowledge_check") or {}
    options = quiz.get("options")
    if not (quiz.get("question") and isinstance(options, list) and len(options) >= 2
            and all(isinstance(option, str) and option.strip() for option in options)
            and quiz.get("correct") and quiz.get("feedback")):
        missing.append("knowledge_check")
    exercise = module.get("hands_on_exercise") or {}
    if not (exercise.get("trainee_instructions") and exercise.get("facilitator_instructions")
            and isinstance(exercise.get("success_criteria"), list)
            and exercise["success_criteria"]):
        missing.append("hands_on_exercise")
    scenario = module.get("simulation_scenario") or {}
    if not (scenario.get("behavioral_target") and scenario.get("scenario_context")
            and isinstance(scenario.get("scoring_criteria"), list)
            and scenario["scoring_criteria"]):
        missing.append("simulation_scenario")
    roleplay = module.get("roleplay_script") or []
    if len(roleplay) < 4 or not any(t.get("speaker", "").upper() == "BRANCH" for t in roleplay):
        missing.append("roleplay_script")
    if missing:
        raise PipelineError("Incomplete training module: " + ", ".join(missing))
    return module


RECORD_ID_PATTERN = re.compile(r"\b([A-Z]{2}-\d{3})\b")


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
    if data is not None:
        verify_module_citations(module, finding, data)
    return module


def run_training(session, approved_findings, data, on_progress=None, on_text=None):
    """Bedrock call per approved finding. Returns {"modules": [...], "errors": [...]} and saves it.

    A failed module is recorded without discarding other completed modules.
    """
    ensure_cloud_upload_authorized(data)
    modules, errors = [], []
    for i, finding in enumerate(approved_findings, start=1):
        if on_progress:
            on_progress(i, len(approved_findings), finding)
        prompt = sanitize_model_prompt(
            prompts.build_training_prompt(
                finding,
                evidence_lines_for(finding, data),
                related_outline_entries(finding, data["outline"]),
            ),
            data,
        )
        try:
            text, turn = session.ask(
                prompt, system_prompt=prompts.SYSTEM_PROMPT, on_text=on_text
            )
            module = validate_module(extract_json(text), finding, data)
            ensure_module_complete(module)
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
    save_json("training", result, source_kind=data.get("source_kind", "synthetic"))
    return result


# ======================================================================================
# STAGE 5 - Alignment check (deterministic)
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

