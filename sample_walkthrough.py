"""Handwritten offline demonstration, for running the application without AWS.

The findings and coaching packages returned here were written by hand for the
built-in synthetic dataset and are stored in
``sample_data/offline_demo_content.json``. They are not captured Bedrock
responses and say nothing about ResultsCX data. They pass through the same
validators as model output, so the offline demonstration exercises the same
checks a live run does.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

import pipeline

CONTENT_FILE = Path(__file__).resolve().parent / "sample_data" / "offline_demo_content.json"
SOURCE_LABEL = "handwritten synthetic walkthrough"
GENERATED_LABEL = "Offline demonstration content (not a model run)"


def _content() -> dict[str, Any]:
    try:
        return json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise pipeline.PipelineError(
            "The offline demonstration content is missing or invalid."
        ) from exc


def _cases_for(data: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each handwritten case with the matching computed behavior in ``data``."""
    behaviors = {(b["source"], b["behavior"]): b for b in data["behaviors"]}
    pairs = []
    for case in _content()["cases"]:
        behavior = behaviors.get((case["source"], case["behavior"]))
        if behavior is not None and behavior["failed"] > 0:
            pairs.append((case, behavior))
    return pairs


def supports(data: dict[str, Any]) -> bool:
    """The demonstration only has content for the built-in synthetic dataset."""
    return data.get("source_kind") == "synthetic" and bool(_cases_for(data))


def demo_diagnosis(
    data: dict[str, Any], focus_behavior_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Return the handwritten findings for the requirements in scope."""
    if not supports(data):
        raise pipeline.PipelineError(
            "The offline demonstration only has handwritten content for the built-in "
            "synthetic dataset. Analyzing other data needs Live mode with an authorized harness."
        )
    focus = set(focus_behavior_ids) if focus_behavior_ids else None
    comments = {r["record_id"]: r["comment"] for r in data["records"]}
    findings, not_training = [], []
    for case, behavior in _cases_for(data):
        if focus is not None and behavior["id"] not in focus:
            continue
        finding = {k: v for k, v in case["finding"].items() if k != "evidence_record_ids"}
        finding["behavior_id"] = behavior["id"]
        # Quote each cited record's own comment, so the citation check has real text to match.
        finding["evidence"] = [
            {"record_id": record_id, "quote": comments.get(record_id, "")}
            for record_id in case["finding"]["evidence_record_ids"]
        ]
        findings.append(finding)
        if case.get("not_a_training_problem"):
            not_training.append(case["not_a_training_problem"])
    if not findings:
        raise pipeline.PipelineError(
            "None of the selected QA requirements has demonstration content. Widen the scope."
        )
    content = _content()
    diagnosis = pipeline.validate_diagnosis(
        {
            "summary": content["summary"],
            "findings": findings,
            "not_a_training_problem": not_training,
            "open_questions": content["open_questions"],
        },
        data,
    )
    diagnosis["_meta"] = {
        "generated_at": GENERATED_LABEL,
        "source": SOURCE_LABEL,
        "focus_behavior_ids": sorted(focus) if focus else None,
        "data_fingerprint": pipeline.data_fingerprint(data),
    }
    return diagnosis


def demo_training(
    approved_findings: list[dict[str, Any]],
    data: dict[str, Any],
    components: dict[str, list[str]] | list[str] | None = None,
    merge_into: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the handwritten coaching package for each approved finding."""
    modules_by_key = {(c["source"], c["behavior"]): c["module"] for c in _content()["cases"]}
    modules, errors = [], []
    for finding in approved_findings:
        chosen = components.get(finding["id"]) if isinstance(components, dict) else components
        template = modules_by_key.get((finding.get("source"), finding.get("behavior")))
        try:
            if template is None:
                raise pipeline.PipelineError(
                    "The offline demonstration has no handwritten package for this requirement."
                )
            module = pipeline.validate_module(copy.deepcopy(template), finding, data)
            pipeline.ensure_module_complete(module, chosen)
            module["components"] = list(chosen) if chosen is not None else list(pipeline.CORE_COMPONENTS)
            modules.append(module)
        except pipeline.PipelineError as exc:
            errors.append({"finding_id": finding["id"], "title": finding.get("title", ""), "error": str(exc)})
    result = {
        "modules": modules,
        "errors": errors,
        "approved_findings": approved_findings,
        "alignment": pipeline.alignment_check(approved_findings, modules),
        "_meta": {
            "generated_at": GENERATED_LABEL,
            "source": SOURCE_LABEL,
            "data_fingerprint": pipeline.data_fingerprint(data),
        },
    }
    return pipeline.merge_training(merge_into, result)
