"""QA intake: the record queue, its filters, evidence lookups, and upload validation.

A QA *record* is one scored line item: one requirement, one employee, one
evaluated call, answered Yes or No by an evaluator. Records are the evidence
every later stage points back to.

Uploaded files are treated as untrusted and possibly private. They are parsed
in memory only, validated field by field, pseudonymized, and masked before
anything is displayed. Nothing here writes an upload to disk.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime
from typing import Any

import get_data
from workflow import compliance

RESULT_LABELS = {"Yes": "Pass", "No": "Fail"}
SECTION_NAMES: dict[str, str] = {code: meta[1] for code, meta in get_data.QA_FILES.items()}

# --------------------------------------------------------------------------------------
# Record queue
# --------------------------------------------------------------------------------------
def build_queue(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per scored line item, with the derived category, risk, and repeat count."""
    records = data["records"]
    repeats = compliance.repeat_counts(records)
    flagged_ids = set(compliance.build_flags(records))
    rows = []
    for record in records:
        source = record.get("source", "")
        requirement = record.get("behavior", "")
        category = compliance.CATEGORIES[compliance.classify_requirement(source, requirement)]
        failed = record.get("result") == "No"
        repeat = repeats.get((record.get("agent", ""), source, requirement), 0)
        risk = (
            compliance.risk_for_failure(category.key, repeat, record.get("comment", ""))
            if failed
            else {"level": "", "reasons": []}
        )
        rows.append(
            {
                "record_id": record.get("record_id", ""),
                "employee": record.get("agent", ""),
                "team_lead": record.get("team_lead", ""),
                "call_date": record.get("call_date", ""),
                "source": source,
                "section": record.get("section", ""),
                "requirement": requirement,
                "result": RESULT_LABELS.get(record.get("result", ""), "Not scored"),
                "comment": record.get("comment", ""),
                "score": record.get("score"),
                "max_score": record.get("max_score"),
                "category": category.key,
                "issue_type": category.label,
                "risk_level": risk["level"],
                "risk_reasons": risk["reasons"],
                "repeat_count": repeat if failed else 0,
                "flagged": record.get("record_id", "") in flagged_ids,
            }
        )
    return rows


def filter_options(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinct values present in the data, for populating the filter controls."""
    dates = sorted(r["call_date"] for r in rows if _parse_date(r["call_date"]))
    return {
        "employees": sorted({r["employee"] for r in rows if r["employee"]}),
        "sections": sorted({r["section"] for r in rows if r["section"]}),
        "issue_types": sorted({r["issue_type"] for r in rows}),
        "risk_levels": [
            level for level in compliance.RISK_LEVELS if any(r["risk_level"] == level for r in rows)
        ],
        "results": [label for label in ("Fail", "Pass") if any(r["result"] == label for r in rows)],
        "date_min": _parse_date(dates[0]) if dates else None,
        "date_max": _parse_date(dates[-1]) if dates else None,
    }


def filter_queue(
    rows: list[dict[str, Any]],
    *,
    employees: tuple[str, ...] | list[str] = (),
    sections: tuple[str, ...] | list[str] = (),
    issue_types: tuple[str, ...] | list[str] = (),
    risk_levels: tuple[str, ...] | list[str] = (),
    results: tuple[str, ...] | list[str] = (),
    date_from: date | None = None,
    date_to: date | None = None,
    flagged_only: bool = False,
    requirement: str = "",
) -> list[dict[str, Any]]:
    """Apply the intake filters. An empty selection means "no restriction"."""
    selected = []
    for row in rows:
        if employees and row["employee"] not in employees:
            continue
        if sections and row["section"] not in sections:
            continue
        if issue_types and row["issue_type"] not in issue_types:
            continue
        if risk_levels and row["risk_level"] not in risk_levels:
            continue
        if results and row["result"] not in results:
            continue
        if requirement and row["requirement"] != requirement:
            continue
        if flagged_only and not row["flagged"]:
            continue
        if date_from or date_to:
            call_date = _parse_date(row["call_date"])
            if call_date is None:
                continue  # a row without a usable date cannot satisfy a date filter
            if (date_from and call_date < date_from) or (date_to and call_date > date_to):
                continue
        selected.append(row)
    return selected


def failed_requirements(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Requirements with at least one failure, highest defect rate first."""
    return [b for b in data["behaviors"] if b["failed"] > 0]


def requirement_text(data: dict[str, Any], source: str, behavior: str) -> str:
    """The scoring guidance supplied with the dataset for a requirement, or ''."""
    for entry in data.get("qa_form") or []:
        if entry.get("source") == source and entry.get("behavior") == behavior:
            return str(entry.get("requirement") or "")
    return ""


def evidence_for(data: dict[str, Any], record_id: str) -> dict[str, Any] | None:
    """Everything the evidence panel shows for one record, or None if it does not exist."""
    queue = build_queue(data)
    row = next((r for r in queue if r["record_id"] == record_id), None)
    record = next((r for r in data["records"] if r.get("record_id") == record_id), None)
    if row is None or record is None:
        return None
    same_requirement = [
        r for r in queue if r["source"] == row["source"] and r["requirement"] == row["requirement"]
    ]
    section_meta = data.get("sections", {}).get(row["source"], {})
    return {
        "row": row,
        "requirement_text": requirement_text(data, row["source"], row["requirement"]),
        "section_description": section_meta.get("description", ""),
        "employee_history": sorted(
            (r for r in same_requirement if r["employee"] == row["employee"]),
            key=lambda r: r["call_date"],
        ),
        "other_employees": sorted(
            (r for r in same_requirement if r["employee"] != row["employee"]),
            key=lambda r: (r["result"] != "Fail", r["call_date"]),
        ),
        # Only what the dataset actually carries. Missing items are reported as missing.
        "supporting": {field: str(record.get(field) or "") for field in get_data.EVIDENCE_FIELDS},
    }


def data_checks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-quality checks that must pass before the records are analyzed."""
    records = data["records"]
    ids = [r.get("record_id", "") for r in records]
    unscored = [r.get("record_id", "?") for r in records if r.get("result") not in ("Yes", "No")]
    undated = [r.get("record_id", "?") for r in records if _parse_date(r.get("call_date")) is None]
    failed = sum(1 for r in records if r.get("result") == "No")
    checks = [
        _check("QA records loaded", bool(records), f"{len(records)} scored line items"),
        _check(
            "Record IDs are unique",
            len(ids) == len(set(ids)) and all(ids),
            "every record can be cited unambiguously"
            if len(ids) == len(set(ids)) and all(ids)
            else "duplicate or empty record IDs found",
        ),
        _check(
            "Every item is scored Yes or No",
            not unscored,
            "all scored" if not unscored else "unscored: " + ", ".join(unscored[:8]),
        ),
        _check(
            "Every item has a call date",
            not undated,
            "all dated" if not undated else "no usable date: " + ", ".join(undated[:8]),
        ),
        _check(
            "At least one failed item to investigate",
            failed > 0,
            f"{failed} failed item(s)",
        ),
    ]
    checks += [_check(name, bool(ok), str(detail)) for name, ok, detail in data.get("checks", [])]
    return checks


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_ROWS = 5000
MAX_PROBLEMS_REPORTED = 12
_LIMITS = {"behavior": 300, "comment": 4000, "agent": 80, "team_lead": 80}
_REQUIRED = ("agent", "call_date", "behavior", "result")
_RECORD_ID = re.compile(r"^[A-Z]{2}-\d{3,6}$")
_PSEUDONYM = re.compile(r"^(?:Agent|TL)-\d{2,4}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_YES = {"yes", "y", "pass", "passed", "true", "1", "met"}
_NO = {"no", "n", "fail", "failed", "false", "0", "not met"}
# Column names accepted in CSV headers and JSON keys, mapped to the internal field.
_ALIASES = {
    "record_id": "record_id",
    "id": "record_id",
    "source": "source",
    "section": "section",
    "qa_section": "section",
    "team_lead": "team_lead",
    "team_leader": "team_lead",
    "agent": "agent",
    "employee": "agent",
    "agentnames": "agent",
    "call_date": "call_date",
    "date_of_call": "call_date",
    "date": "call_date",
    "behavior": "behavior",
    "requirement": "behavior",
    "questions": "behavior",
    "result": "result",
    "answers": "result",
    "comment": "comment",
    "comments": "comment",
    "feedback_answers": "comment",
    "max_score": "max_score",
    "maxscore": "max_score",
    "score": "score",
    "transcript_excerpt": "transcript_excerpt",
    "call_timestamp": "call_timestamp",
    "policy_reference": "policy_reference",
}
UPLOAD_TEMPLATE_CSV = (
    "record_id,section,agent,team_lead,call_date,behavior,result,comment\n"
    "CO-001,Compliance,Agent-01,TL-01,2026-09-01,Confirm identity before discussing account details,"
    "No,Example evaluator comment for a failed item.\n"
    "CO-002,Compliance,Agent-02,TL-01,2026-09-02,Confirm identity before discussing account details,"
    "Yes,\n"
)


class UploadError(ValueError):
    """An uploaded file was rejected. ``problems`` lists what to fix."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _clean(value: Any) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", str(value if value is not None else "")).split())


def _canonical_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _normalize_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _section_code(source: str, section: str) -> str | None:
    code = source.upper()
    if code in SECTION_NAMES:
        return code
    wanted = section.lower()
    for known_code, name in SECTION_NAMES.items():
        if wanted in (name.lower(), known_code.lower()):
            return known_code
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _read_rows(filename: str, raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decode a JSON or CSV upload into raw row dicts plus any optional extras."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError(
            [f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit."]
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadError(["The file is not UTF-8 text. Save it as UTF-8 and retry."]) from exc
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UploadError([f"The JSON could not be parsed (line {exc.lineno})."]) from exc
        extras: dict[str, Any] = {}
        if isinstance(payload, dict):
            extras = {k: payload.get(k) for k in ("outline", "qa_form")}
            payload = payload.get("records")
        if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
            raise UploadError(['JSON must be a list of records or an object with a "records" list.'])
        return payload, extras
    if suffix == "csv":
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as exc:
            raise UploadError([f"The CSV could not be parsed: {exc}."]) from exc
        return rows, {}
    raise UploadError(["Unsupported file type. Upload a .json or .csv file."])


def parse_upload(filename: str, raw: bytes) -> dict[str, Any]:
    """Validate an uploaded QA file and return a dataset dict (``source_kind="upload"``).

    The whole file is rejected if any row is invalid, so a partly loaded dataset can
    never be mistaken for a complete one.
    """
    raw_rows, extras = _read_rows(filename, raw)
    if not raw_rows:
        raise UploadError(["The file contains no records."])
    if len(raw_rows) > MAX_UPLOAD_ROWS:
        raise UploadError([f"The file has more than {MAX_UPLOAD_ROWS} records."])

    problems: list[str] = []
    ignored: set[str] = set()
    staged: list[dict[str, Any]] = []
    for number, raw_row in enumerate(raw_rows, start=1):
        row: dict[str, Any] = {}
        for key, value in raw_row.items():
            field = _ALIASES.get(_canonical_key(key))
            if field is None:
                ignored.add(str(key))
            elif field not in row or not row[field]:
                row[field] = value
        text = {f: _clean(row.get(f)) for f in _ALIASES.values()}
        where = f"Row {number}"
        missing = [f for f in _REQUIRED if not text[f]]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}.")
            continue
        code = _section_code(text["source"], text["section"])
        if code is None:
            problems.append(
                f"{where}: section must be one of {', '.join(SECTION_NAMES.values())} "
                f"(or source {', '.join(SECTION_NAMES)})."
            )
            continue
        answer = text["result"].lower()
        if answer not in _YES | _NO:
            problems.append(f"{where}: result must be Yes or No (found '{text['result'][:20]}').")
            continue
        call_date = _normalize_date(text["call_date"])
        if call_date is None:
            problems.append(f"{where}: call_date must be YYYY-MM-DD or MM/DD/YYYY.")
            continue
        too_long = [f for f, limit in _LIMITS.items() if len(text[f]) > limit]
        if too_long:
            problems.append(f"{where}: {', '.join(too_long)} is longer than allowed.")
            continue
        result = "Yes" if answer in _YES else "No"
        max_score = _number(row.get("max_score")) if text["max_score"] else 1.0
        score = _number(row.get("score")) if text["score"] else (1.0 if result == "Yes" else 0.0)
        if max_score is None or score is None or not 0 <= score <= max_score:
            problems.append(f"{where}: score must be a number between 0 and max_score.")
            continue
        record_id = text["record_id"].upper()
        if record_id and (not _RECORD_ID.match(record_id) or not record_id.startswith(code + "-")):
            problems.append(f"{where}: record_id must look like {code}-001.")
            continue
        staged.append(
            {
                "record_id": record_id,
                "source": code,
                "section": SECTION_NAMES[code],
                "team_lead": text["team_lead"],
                "agent": text["agent"],
                "call_date": call_date,
                "behavior": text["behavior"],
                "result": result,
                "comment": text["comment"],
                "max_score": max_score,
                "score": score,
                **{f: text[f] for f in get_data.EVIDENCE_FIELDS if text[f]},
            }
        )

    with_id = sum(1 for r in staged if r["record_id"])
    if with_id and with_id != len(staged):
        problems.append("Give every record a record_id, or leave the column out entirely.")
    ids = [r["record_id"] for r in staged if r["record_id"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append("Duplicate record_id values: " + ", ".join(duplicates[:8]) + ".")
    if problems:
        extra = len(problems) - MAX_PROBLEMS_REPORTED
        shown = problems[:MAX_PROBLEMS_REPORTED]
        if extra > 0:
            shown.append(f"...and {extra} more problem(s).")
        raise UploadError(shown)

    records, agent_map, lead_map, scrub_log = _pseudonymize_and_mask(staged)
    warnings = []
    if ignored:
        warnings.append("Ignored unrecognized columns: " + ", ".join(sorted(ignored)[:10]) + ".")
    checks = [
        ("Uploaded file passed field validation", True, f"{len(records)} records, strict mode"),
        (
            "Employee and team-lead names replaced with codes",
            True,
            f"{len(agent_map)} employee(s), {len(lead_map)} team lead(s)",
        ),
    ]
    dataset = get_data.dataset_from_records(
        records,
        _normalize_outline(extras.get("outline"), filename),
        source_kind="upload",
        checks=checks,
        scrub_log=scrub_log,
        agent_map=agent_map,
        team_lead_map=lead_map,
        qa_form=_normalize_qa_form(extras.get("qa_form")),
        source_label=f"Uploaded file: {_clean(filename)[:60]}",
    )
    dataset["upload_warnings"] = warnings
    return dataset


def _codes(values: list[str], prefix: str) -> dict[str, str]:
    """Map each distinct real value to a stable code, keeping values that are already codes."""
    mapping: dict[str, str] = {}
    taken = {v for v in values if _PSEUDONYM.match(v)}
    counter = 0
    for value in sorted(set(values)):
        if not value:
            continue
        if _PSEUDONYM.match(value):
            mapping[value] = value
            continue
        while True:
            counter += 1
            code = f"{prefix}-{counter:02d}"
            if code not in taken:
                break
        taken.add(code)
        mapping[value] = code
    return mapping


def _pseudonymize_and_mask(
    staged: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str], dict[str, int]]:
    agents = _codes([r["agent"] for r in staged], "Agent")
    leads = _codes([r["team_lead"] for r in staged], "TL")
    real_names = {real: code for real, code in {**agents, **leads}.items() if real != code}
    scrubber = get_data.Scrubber([], real_names)
    counters: dict[str, int] = {}
    generate_ids = not any(r["record_id"] for r in staged)
    records = []
    for row in staged:
        record = dict(row)
        if generate_ids:
            counters[row["source"]] = counters.get(row["source"], 0) + 1
            record["record_id"] = f"{row['source']}-{counters[row['source']]:03d}"
        record["agent"] = agents[row["agent"]]
        record["team_lead"] = leads.get(row["team_lead"], "TL-??")
        record["comment"] = scrubber.scrub(row["comment"])
        for field in get_data.EVIDENCE_FIELDS:
            if field in record:
                record[field] = scrubber.scrub(record[field])
        records.append(record)
    agent_map = {code: real for real, code in agents.items() if real != code}
    lead_map = {code: real for real, code in leads.items() if real != code}
    return records, agent_map, lead_map, {k: v for k, v in scrubber.log.items() if v}


def _normalize_outline(outline: Any, filename: str) -> dict[str, Any]:
    entries = []
    if isinstance(outline, dict) and isinstance(outline.get("entries"), list):
        for entry in outline["entries"][:500]:
            if not isinstance(entry, dict):
                continue
            cleaned = {
                key: _clean(entry.get(key))[:300]
                for key in ("week", "day", "topic", "detail", "resource", "important")
                if _clean(entry.get(key))
            }
            if cleaned.get("topic") or cleaned.get("detail"):
                cleaned.setdefault("week", "Outline")
                entries.append(cleaned)
    return {"source_file": _clean(filename)[:60], "mission_statement": [], "entries": entries}


def _normalize_qa_form(qa_form: Any) -> list[dict[str, str]]:
    cleaned = []
    if isinstance(qa_form, list):
        for entry in qa_form[:200]:
            if isinstance(entry, dict) and entry.get("behavior") and entry.get("requirement"):
                cleaned.append(
                    {
                        "source": _clean(entry.get("source")).upper()[:2],
                        "behavior": _clean(entry.get("behavior"))[:300],
                        "requirement": _clean(entry.get("requirement"))[:1000],
                    }
                )
    return cleaned
