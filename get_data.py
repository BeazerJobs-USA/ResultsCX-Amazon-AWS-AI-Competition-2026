"""Load public synthetic examples or authorized private QA exports.

The included masking rules are limited safeguards, not a de-identification
service. Private inputs must be reviewed before any external processing.
No AWS calls are made in this module.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# --------------------------------------------------------------------------------------
# Paths are anchored to THIS file, so the app works no matter which folder you launch from.
# --------------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
SAMPLE_FILE = BASE_DIR / "sample_data" / "synthetic_qa.json"
NAMES_FILE = BASE_DIR / "names_to_mask.txt"

# short code -> (file name, section name, what the case study says the section measures)
QA_FILES = {
    "BP": (
        "process_qa.xlsx",
        "Business Process",
        "Did the agent follow the correct workflow? Documentation, care plan creation, "
        "demographic updates, SOP adherence.",
    ),
    "CO": (
        "compliance_qa.xlsx",
        "Compliance",
        "High-risk requirements: HIPAA verification, accurate information, escalation, "
        "required follow-up, call avoidance. A single failure here is serious even if the rate is low.",
    ),
    "ME": (
        "experience_qa.xlsx",
        "Member Experience",
        "Communication behaviors: active listening, call control, rapport, confidence, responsiveness.",
    ),
}
TRAINING_FILE = "training_outline.xlsx"

# Fields a QA record may carry. Anything else in a public fixture or an upload is rejected
# or dropped so stray identity columns cannot ride along into the review screens.
RECORD_FIELDS = (
    "record_id",
    "source",
    "section",
    "team_lead",
    "agent",
    "call_date",
    "behavior",
    "result",
    "comment",
    "max_score",
    "score",
)
# Optional supporting evidence. Shown only when a dataset actually supplies it.
EVIDENCE_FIELDS = ("transcript_excerpt", "call_timestamp", "policy_reference")

# Columns we expect in each QA export (checked explicitly so a renamed column gives a clear error).
REQUIRED_COLUMNS = [
    "QA Name",
    "Team Leader",
    "AgentNames",
    "Date of Call",
    "Questions",
    "Answers",
    "Feedback answers",
    "MaxScore",
    "Score",
]


class DataError(Exception):
    """Raised when a QA dataset is missing or malformed."""


# ======================================================================================
# PII masking
# ======================================================================================
def load_names_to_mask():
    """Read names_to_mask.txt. Each line is a name; an optional '=> [TAG]' picks the replacement.

    Example lines:
        Jane Doe
        Some Vendor Inc => [ORG]
    Lines starting with '#' are comments.
    """
    pairs = []
    if not NAMES_FILE.exists():
        return pairs
    for raw in NAMES_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            name, tag = [p.strip() for p in line.split("=>", 1)]
        else:
            name, tag = line, "[MEMBER]"
        if name:
            pairs.append((name, tag))
    # Longest names first so "Jane Doe" is replaced before "Jane".
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


# Regular expressions for identifiers that are not names. Order matters (phones before IDs).
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RE_PHONE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
RE_DOCTOR = re.compile(r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")
RE_HONORIFIC = re.compile(r"\b(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")
RE_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")  # 07/27/2026, 07-24-26
RE_LONG_ID = re.compile(
    r"\b\d{5,}\b"
)  # ticket IDs (TID: 368790), zip codes, member IDs


class Scrubber:
    """Masks personal identifiers in free-text comments and counts what it replaced."""

    def __init__(self, names_to_mask, person_codes):
        self.names_to_mask = names_to_mask
        # person_codes: real agent / team-leader name -> code such as "Agent-03"
        self.person_patterns = []
        for real_name, code in person_codes.items():
            full = re.escape(real_name.strip())
            self.person_patterns.append(
                (re.compile(r"\b" + full + r"\b", re.IGNORECASE), code)
            )
            first = real_name.strip().split()[0]
            if (
                len(first) >= 3
            ):  # avoid masking 2-letter tokens that are also ordinary words
                self.person_patterns.append(
                    (re.compile(r"\b" + re.escape(first) + r"\b"), code)
                )
        self.log = defaultdict(int)

    def scrub(self, text):
        if not text:
            return ""
        text = str(text)
        for pattern, code in self.person_patterns:  # employee names -> Agent-03 / TL-01
            text, n = pattern.subn(code, text)
            self.log["employee names"] += n
        for name, tag in self.names_to_mask:  # known member / provider / vendor names
            n = text.count(name)
            if n:
                self.log["listed names"] += n
                text = text.replace(name, tag)
        for label, regex, tag in (
            ("emails", RE_EMAIL, "[EMAIL]"),
            ("phones", RE_PHONE, "[PHONE]"),
            ("providers", RE_DOCTOR, "[PROVIDER]"),
            ("honorific names", RE_HONORIFIC, "[MEMBER]"),
            ("dates", RE_DATE, "[DATE]"),
            ("ids/zips", RE_LONG_ID, "[ID]"),
        ):
            text, n = regex.subn(tag, text)
            self.log[label] += n
        return text


# ======================================================================================
# QA exports -> records
# ======================================================================================
def _open_workbook(path):
    """Open an Excel export read-only. openpyxl is imported here because only the
    opt-in private-data path needs it; the synthetic walkthrough does not."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depends on the local install
        raise DataError(
            "openpyxl is required to read private Excel exports. "
            "Install the project requirements and retry."
        ) from exc
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def _read_export(path):
    """Return (header, rows) for the first worksheet of a QA export, with a clear error if broken."""
    if not path.exists():
        raise DataError(
            f"Missing QA file: {path.name}\n"
            f"Expected location: {path}\n"
            f"Fix: copy the ResultsCX Excel files into the folder  {DATA_DIR}"
        )
    wb = _open_workbook(path)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise DataError(f"{path.name} is empty (no rows found on the first sheet).")
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise DataError(
            f"{path.name}: expected columns {missing} were not found in the header row.\n"
            f"Header found: {header}\n"
            f"Fix: check the export was not modified; get_data.py expects the original column names."
        )
    return header, rows[1:]


def _fmt_date(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value) if value is not None else ""


def prepare_qa_data():
    """Load the three QA exports and return masked, structured records.

    Returns a dict:
        records      list of line items (comments already masked, agents already coded)
        agent_map    {"Agent-01": "Real Name", ...}   LOCAL ONLY - never send to the model
        team_lead_map{"TL-01": "Real Name", ...}      LOCAL ONLY
        scrub_log    counts of what was masked
        checks       list of (name, passed, detail) data-integrity checks
    """
    raw_rows = []  # (code, header, row)
    for code, (filename, _section, _desc) in QA_FILES.items():
        header, rows = _read_export(DATA_DIR / filename)
        col = {name: i for i, name in enumerate(header)}
        for row in rows:
            qa = row[col["QA Name"]]
            # Skip the "Total" row and the "Applied filters:" footer that Power BI adds to exports.
            if (
                qa in (None, "")
                or str(qa).strip() == "Total"
                or str(qa).startswith("Applied filters")
            ):
                continue
            raw_rows.append((code, col, row))

    # ---- pseudonymise employees: stable codes based on sorted real names ----
    agents = sorted(
        {
            str(row[col["AgentNames"]]).strip()
            for _, col, row in raw_rows
            if row[col["AgentNames"]]
        }
    )
    leads = sorted(
        {
            str(row[col["Team Leader"]]).strip()
            for _, col, row in raw_rows
            if row[col["Team Leader"]]
        }
    )
    agent_map = {f"Agent-{i:02d}": name for i, name in enumerate(agents, start=1)}
    team_lead_map = {f"TL-{i:02d}": name for i, name in enumerate(leads, start=1)}
    real_to_code = {v: k for k, v in agent_map.items()}
    real_to_code.update({v: k for k, v in team_lead_map.items()})

    scrubber = Scrubber(load_names_to_mask(), real_to_code)

    records = []
    counters = defaultdict(int)
    for code, col, row in raw_rows:
        counters[code] += 1
        agent_real = (
            str(row[col["AgentNames"]]).strip() if row[col["AgentNames"]] else ""
        )
        lead_real = (
            str(row[col["Team Leader"]]).strip() if row[col["Team Leader"]] else ""
        )
        records.append(
            {
                "record_id": f"{code}-{counters[code]:03d}",
                "source": code,
                "section": QA_FILES[code][1],
                "team_lead": real_to_code.get(lead_real, "TL-??"),
                "agent": real_to_code.get(agent_real, "Agent-??"),
                "call_date": _fmt_date(row[col["Date of Call"]]),
                "behavior": re.sub(
                    r"\s+", " ", str(row[col["Questions"]] or "")
                ).strip(),
                "result": str(row[col["Answers"]]).strip()
                if row[col["Answers"]] is not None
                else "",
                "comment": scrubber.scrub(row[col["Feedback answers"]]),
                "max_score": row[col["MaxScore"]],
                "score": row[col["Score"]],
            }
        )

    # Basic integrity checks for the records used in the analysis.
    checks = []
    leaks = [
        r["record_id"]
        for r in records
        if RE_EMAIL.search(r["comment"]) or RE_PHONE.search(r["comment"])
    ]
    checks.append(
        (
            "No emails or phone numbers left in comments",
            not leaks,
            ", ".join(leaks) or "clean",
        )
    )
    real_name_leaks = [
        r["record_id"]
        for r in records
        if any(
            re.search(r"\b" + re.escape(n.split()[0]) + r"\b", r["comment"])
            for n in agents
            if len(n.split()[0]) >= 3
        )
    ]
    checks.append(
        (
            "No employee first names left in comments",
            not real_name_leaks,
            ", ".join(real_name_leaks) or "clean",
        )
    )
    bad_results = [r["record_id"] for r in records if r["result"] not in ("Yes", "No")]
    checks.append(
        (
            "Every line item is answered Yes/No",
            not bad_results,
            ", ".join(bad_results) or "all Yes/No",
        )
    )
    checks.append(
        (
            "Three QA sections loaded",
            set(counters) == set(QA_FILES),
            f"{dict(counters)}",
        )
    )

    return {
        "records": records,
        "agent_map": agent_map,
        "team_lead_map": team_lead_map,
        "scrub_log": dict(scrubber.log),
        "checks": checks,
    }


# ======================================================================================
# Metrics (deterministic - no AI involved)
# ======================================================================================
def _pattern_label(failed, n_failed, n_evaluated, repeat_agents):
    """Describe how widespread a missed behavior is.

    "Recurring" means three or more employees missed it, or at least two who make up
    half or more of the employees evaluated on it. An absolute count alone mislabels
    small teams: two of three employees is not an isolated problem.
    """
    if failed == 0:
        return "no failures"
    widespread = n_failed >= 3 or (n_failed >= 2 and 2 * n_failed >= n_evaluated)
    label = f"{'recurring' if widespread else 'isolated'} - {n_failed} of {n_evaluated} agents evaluated"
    if repeat_agents:
        label += f"; repeated by {', '.join(repeat_agents)}"
    return label


def analyze_defects(records):
    """Compute defect rate per behavior and per agent.

    The export does not have a unique call identifier. Agent/date counts
    are a proxy, not a verified count of distinct calls.
    """
    if not records:
        raise DataError("No QA records were loaded. Check the selected data source.")
    n_agents = len({r["agent"] for r in records})
    calls = {(r["agent"], r["call_date"]) for r in records}

    by_behavior = {}
    for r in records:
        key = (r["source"], r["behavior"])
        s = by_behavior.setdefault(
            key,
            {
                "source": r["source"],
                "section": r["section"],
                "behavior": r["behavior"],
                "evaluated": 0,
                "failed": 0,
                "agents_failed": set(),
                "agents_evaluated": set(),
                "fails_by_agent": defaultdict(int),
                "evidence": [],
            },
        )
        s["evaluated"] += 1
        s["agents_evaluated"].add(r["agent"])
        if r["result"] == "No":
            s["failed"] += 1
            s["agents_failed"].add(r["agent"])
            s["fails_by_agent"][r["agent"]] += 1
        if r[
            "comment"
        ].strip():  # evaluators also comment on some PASSING items - keep both
            s["evidence"].append(
                {
                    "record_id": r["record_id"],
                    "agent": r["agent"],
                    "date": r["call_date"],
                    "result": r["result"],
                    "comment": r["comment"],
                }
            )

    behaviors = []
    for s in by_behavior.values():
        s["agents_failed"] = sorted(s["agents_failed"])
        s["n_agents_failed"] = len(s["agents_failed"])
        s["n_agents_evaluated"] = len(s.pop("agents_evaluated"))
        # Employees who missed this behavior on more than one evaluation.
        s["repeat_agents"] = sorted(a for a, n in s.pop("fails_by_agent").items() if n >= 2)
        s["defect_rate_pct"] = (
            round(100 * s["failed"] / s["evaluated"]) if s["evaluated"] else 0
        )
        s["pattern"] = _pattern_label(
            s["failed"], s["n_agents_failed"], s["n_agents_evaluated"], s["repeat_agents"]
        )
        behaviors.append(s)
    behaviors.sort(key=lambda s: (-s["defect_rate_pct"], -s["failed"], s["behavior"]))
    for i, s in enumerate(behaviors, start=1):
        s["id"] = f"B{i:02d}"

    # per-agent view: separates "everyone fails one thing" (knowledge/process) from
    # "one person fails everything" (coaching / will gap)
    per_agent = {}
    for r in records:
        a = per_agent.setdefault(
            r["agent"],
            {
                "agent": r["agent"],
                "team_lead": r["team_lead"],
                "calls": set(),
                "evaluated": 0,
                "failed": 0,
                "failed_behaviors": defaultdict(int),
            },
        )
        a["calls"].add(r["call_date"])
        a["evaluated"] += 1
        if r["result"] == "No":
            a["failed"] += 1
            a["failed_behaviors"][r["behavior"]] += 1
    agents = []
    for a in per_agent.values():
        a["calls"] = len(a["calls"])
        a["fail_rate_pct"] = (
            round(100 * a["failed"] / a["evaluated"]) if a["evaluated"] else 0
        )
        a["failed_behaviors"] = dict(
            sorted(a["failed_behaviors"].items(), key=lambda kv: -kv[1])
        )
        agents.append(a)
    agents.sort(key=lambda a: (-a["fail_rate_pct"], a["agent"]))

    # Reconcile aggregate counts before they are displayed or sent to the model.
    total_fail = sum(1 for r in records if r["result"] == "No")
    if sum(s["failed"] for s in behaviors) != total_fail:
        raise DataError("Behavior-level failure counts do not reconcile")
    if sum(a["failed"] for a in agents) != total_fail:
        raise DataError("Agent-level failure counts do not reconcile")

    return {
        "behaviors": behaviors,
        "agents": agents,
        "totals": {
            "line_items": len(records),
            "failed_items": total_fail,
            # An agent/date pair is only a proxy; it is not a unique call ID.
            "calls_evaluated": len(calls),
            "agents": n_agents,
            "date_range": [
                min(r["call_date"] for r in records),
                max(r["call_date"] for r in records),
            ],
        },
    }


# ======================================================================================
# Training outline (slim, fast)
# ======================================================================================
def load_training_outline():
    """Return a compact outline: mission statement + one entry per training row.

    Each Week sheet has columns A=Topic, B=Resources, C=Completion Date, D=Comments, E=Important.
    Sub-topics frequently live in the Comments column with an empty Topic, so both are kept.
    """
    path = DATA_DIR / TRAINING_FILE
    if not path.exists():
        raise DataError(
            f"Missing training workbook: {path.name}\nExpected location: {path}\n"
            f"Fix: copy the ResultsCX Excel files into the folder  {DATA_DIR}"
        )
    wb = _open_workbook(path)

    def clean(v, limit=220):
        if v is None:
            return None
        s = re.sub(r"\s+", " ", str(v)).strip()
        return (s[:limit] + "...") if len(s) > limit else (s or None)

    mission = []
    if "Mission Statement" in wb.sheetnames:
        for row in wb["Mission Statement"].iter_rows(max_col=1, values_only=True):
            v = clean(row[0], 400)
            if v:
                mission.append(v)

    entries = []
    for name in wb.sheetnames:
        if not name.startswith("Week"):
            continue
        current_day = None
        for row in wb[name].iter_rows(max_col=5, values_only=True):
            topic, resource, _date, comments, important = (list(row) + [None] * 5)[:5]
            topic, resource, comments, important = (
                clean(topic),
                clean(resource),
                clean(comments),
                clean(important),
            )
            if topic and re.fullmatch(r"Days?\s+.*", topic, flags=re.IGNORECASE):
                current_day = topic
                continue
            if topic == "Topic" and resource == "Resources":
                continue  # repeated header row
            if not any((topic, resource, comments, important)):
                continue
            entry = {"week": name.strip(), "day": current_day, "topic": topic}
            if comments:
                entry["detail"] = comments
            if resource:
                entry["resource"] = resource
            if important:
                entry["important"] = important
            entries.append(entry)
    wb.close()
    return {"source_file": path.name, "mission_statement": mission, "entries": entries}


# ======================================================================================
# Public entry points
# ======================================================================================
def dataset_from_records(
    records: list[dict[str, Any]],
    outline: dict[str, Any],
    *,
    source_kind: str,
    checks: list[tuple[str, bool, str]],
    scrub_log: dict[str, int] | None = None,
    agent_map: dict[str, str] | None = None,
    team_lead_map: dict[str, str] | None = None,
    qa_form: list[dict[str, str]] | None = None,
    source_label: str = "",
) -> dict[str, Any]:
    """Assemble the dataset dict every stage reads, whatever the records came from.

    ``agent_map`` and ``team_lead_map`` hold real names for local masking only and are
    never included in anything sent to a model.
    """
    metrics = analyze_defects(records)
    return {
        "records": records,
        "behaviors": metrics["behaviors"],
        "agents": metrics["agents"],
        "totals": metrics["totals"],
        "outline": outline,
        "qa_form": qa_form or [],
        "sections": {
            code: {"name": v[1], "description": v[2]} for code, v in QA_FILES.items()
        },
        "checks": checks,
        "scrub_log": scrub_log or {},
        "agent_map": agent_map or {},  # LOCAL ONLY
        "team_lead_map": team_lead_map or {},  # LOCAL ONLY
        "source_kind": source_kind,
        "source_label": source_label or source_kind,
    }


def load_synthetic_fixture() -> dict[str, Any]:
    """Read the checked-in invented QA records."""
    try:
        fixture = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(
            "The public synthetic QA fixture is unavailable or invalid"
        ) from exc
    allowed = set(RECORD_FIELDS) | set(EVIDENCE_FIELDS)
    checks = [
        ("Synthetic example records only", True, "No original QA exports loaded"),
        (
            "No unexpected identity fields",
            all(set(r) <= allowed for r in fixture["records"]),
            "Public fixture field allowlist",
        ),
    ]
    return dataset_from_records(
        fixture["records"],
        fixture["outline"],
        source_kind="synthetic",
        checks=checks,
        qa_form=fixture.get("qa_form"),
        source_label="Built-in synthetic dataset",
    )


def get_all_data(source_override: str | None = None) -> dict[str, Any]:
    """Return synthetic examples by default; private workbooks require opt-in.

    Set QA_DATA_SOURCE=private and ALLOW_PRIVATE_QA_READ=1 to read the local
    authorized workbooks. Cloud upload has a separate explicit approval gate.
    """
    source = (
        source_override
        if source_override is not None
        else os.environ.get("QA_DATA_SOURCE", "synthetic")
    ).strip().lower()
    if source == "synthetic":
        return load_synthetic_fixture()
    if source == "private":
        if os.environ.get("ALLOW_PRIVATE_QA_READ") != "1":
            raise DataError(
                "Private workbooks require ALLOW_PRIVATE_QA_READ=1 and permission to use them"
            )
        qa = prepare_qa_data()
        return dataset_from_records(
            qa["records"],
            load_training_outline(),
            source_kind="private",
            checks=qa["checks"],
            scrub_log=qa["scrub_log"],
            agent_map=qa["agent_map"],
            team_lead_map=qa["team_lead_map"],
            source_label="Authorized local exports",
        )
    raise DataError("QA_DATA_SOURCE must be either synthetic or private")


def private_source_enabled() -> bool:
    """True when the environment opts in to reading the local private workbooks."""
    return (
        os.environ.get("QA_DATA_SOURCE", "").strip().lower() == "private"
        and os.environ.get("ALLOW_PRIVATE_QA_READ") == "1"
    )


def build_model_payload(data):
    """The subset of the data that is sent to Bedrock. Contains NO real employee names."""
    behaviors = []
    for b in data["behaviors"]:
        behaviors.append(
            {
                "id": b["id"],
                "section": b["section"],
                "behavior": b["behavior"],
                "evaluated": b["evaluated"],
                "failed": b["failed"],
                "defect_rate_pct": b["defect_rate_pct"],
                "pattern": b["pattern"],
                "agents_failed": b["agents_failed"],
                "evidence": b["evidence"],
            }
        )
    agents = [
        {
            "agent": a["agent"],
            "calls_evaluated": a["calls"],
            "items_evaluated": a["evaluated"],
            "items_failed": a["failed"],
            "fail_rate_pct": a["fail_rate_pct"],
            "failed_behaviors": a["failed_behaviors"],
        }
        for a in data["agents"]
    ]
    return {
        "totals": data["totals"],
        "sections": data["sections"],
        "behaviors": behaviors,
        "agents": agents,
        "training_outline": data["outline"],
    }


def assert_no_real_names(payload_text, data):
    """Check known employee names; not a guarantee of full PII removal."""
    for real in list(data["agent_map"].values()) + list(data["team_lead_map"].values()):
        if real and re.search(
            r"\b" + re.escape(real) + r"\b", payload_text, flags=re.IGNORECASE
        ):
            raise DataError(
                "Privacy check failed: a known employee name appears in the model payload. "
                "Review input masking before running the model."
            )


if __name__ == "__main__":
    # Offline self-test:  python get_data.py
    try:
        data = get_all_data()
    except DataError as e:
        sys.exit(f"DATA ERROR\n{e}")
    t = data["totals"]
    print(
        f"{t['line_items']} line items, {t['failed_items']} failures, {t['calls_evaluated']} agent-date groups, "
        f"{t['agents']} agents, dates {t['date_range'][0]} to {t['date_range'][1]}"
    )
    print("Masking:", data["scrub_log"])
    for name, ok, detail in data["checks"]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} - {detail}")
    print(f"\n{'rate':>5} {'fail':>6}  pattern                          behavior")
    for b in data["behaviors"]:
        if b["failed"]:
            print(
                f"{b['defect_rate_pct']:>4}% {b['failed']:>2}/{b['evaluated']:<3} {b['pattern']:<32} "
                f"[{b['source']}] {b['behavior'][:55]}"
            )
    payload = build_model_payload(data)
    text = json.dumps(payload)
    assert_no_real_names(text, data)
    print(
        f"\nModel payload: {len(text):,} characters (~{len(text) // 4:,} tokens); "
        f"outline entries: {len(data['outline']['entries'])}"
    )
    print("PASS: known employee names were not found in the model payload")
    if data["source_kind"] == "private":
        print(
            "WARNING: automatic masking is incomplete; an authorized human must review private data."
        )

