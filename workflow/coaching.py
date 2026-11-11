"""Coaching packages: components, graded knowledge checks, roleplay practice, completion.

A coaching *package* is built for one supervisor-approved finding. The
supervisor chooses which components it contains before anything is generated.

The knowledge check is graded against the answer key in the package. The
roleplay practice is a decision tree: the trainee picks what the agent says at
each decision point and the scripted member reacts to that choice. It is
branching practice with fixed content, not a free-text conversation with a model.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from workflow.common import WorkflowError, now_iso, require

# --------------------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Component:
    key: str
    label: str
    description: str


COMPONENTS: dict[str, Component] = {
    c.key: c
    for c in (
        Component(
            "targeted_coaching",
            "Targeted coaching",
            "Learning objective and teaching points for this specific behavior. Always included.",
        ),
        Component(
            "policy_refresher",
            "Policy refresher",
            "Plain-language recap of the requirement, with points to confirm with the policy owner.",
        ),
        Component(
            "knowledge_check",
            "Knowledge check",
            "Scenario questions graded against an answer key.",
        ),
        Component(
            "guided_practice",
            "Guided practice",
            "A coached exercise with observable success criteria.",
        ),
        Component(
            "customer_scenario",
            "Customer scenario",
            "A fictional member situation with scoring criteria.",
        ),
        Component(
            "roleplay",
            "Branching roleplay",
            "Decision-point practice plus a script to read through with a coach.",
        ),
        Component(
            "coaching_notes",
            "Supervisor coaching notes",
            "Talking points and questions for the coaching conversation.",
        ),
        Component(
            "remediation_plan",
            "Remediation plan",
            "Ordered steps, owners, and the point at which to escalate.",
        ),
        Component(
            "follow_up_evaluation",
            "Follow-up evaluation",
            "What to re-evaluate after coaching and how to judge the result.",
        ),
    )
}
ALWAYS_INCLUDED = "targeted_coaching"
# The component set the original prototype always generated.
CORE_COMPONENTS: tuple[str, ...] = (
    "targeted_coaching",
    "knowledge_check",
    "guided_practice",
    "customer_scenario",
    "roleplay",
)
_DEFAULTS: dict[str, tuple[str, ...]] = {
    "coaching": CORE_COMPONENTS + ("coaching_notes", "follow_up_evaluation"),
    "remediation": (
        "targeted_coaching",
        "policy_refresher",
        "knowledge_check",
        "guided_practice",
        "coaching_notes",
        "remediation_plan",
        "follow_up_evaluation",
    ),
}


def normalize_components(selected: Any) -> list[str]:
    """Known components in display order, always including targeted coaching."""
    chosen = set(selected or ()) | {ALWAYS_INCLUDED}
    return [key for key in COMPONENTS if key in chosen]


def default_components(next_action: str) -> list[str]:
    """Starting selection for the next action the supervisor chose."""
    return normalize_components(_DEFAULTS.get(next_action, _DEFAULTS["coaching"]))


# --------------------------------------------------------------------------------------
# Knowledge check
# --------------------------------------------------------------------------------------
PASS_MARK = 0.8
_OPTION_PREFIX = re.compile(r"^\s*\(?([A-Za-z])[).:\-]\s+")


def _correct_index(options: list[str], correct: Any) -> int | None:
    """Resolve an answer key ("B", "B)", "Option B", or the option text) to an index."""
    key = str(correct or "").strip()
    if not key or not options:
        return None
    letters = []
    for option in options:
        match = _OPTION_PREFIX.match(option)
        letters.append(match.group(1).upper() if match else None)
    letter_match = re.fullmatch(r"(?:option\s+)?\(?([A-Za-z])[).:]?", key, flags=re.I)
    if letter_match:
        letter = letter_match.group(1).upper()
        if letter in letters:
            return letters.index(letter)
        position = ord(letter) - ord("A")
        # Options without letter prefixes: "B" means the second option.
        if all(found is None for found in letters) and 0 <= position < len(options):
            return position
        return None
    wanted = " ".join(key.split()).casefold()
    for index, option in enumerate(options):
        full = " ".join(option.split()).casefold()
        bare = " ".join(_OPTION_PREFIX.sub("", option).split()).casefold()
        if wanted in (full, bare):
            return index
    return None


def quiz_questions(module: dict[str, Any]) -> list[dict[str, Any]]:
    """The package's questions in a uniform shape, each with a resolved answer key.

    ``correct_index`` is None when the answer key does not identify one of the options;
    such a question is shown but never graded.
    """
    raw = [module.get("knowledge_check")] + list(module.get("additional_knowledge_checks") or [])
    questions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
        if not text or len(options) < 2:
            continue
        questions.append(
            {
                "id": f"q{len(questions) + 1}",
                "question": text,
                "options": options,
                "correct_index": _correct_index(options, item.get("correct")),
                "feedback": str(item.get("feedback") or "").strip(),
            }
        )
    return questions


def grade_quiz(questions: list[dict[str, Any]], answers: dict[str, int | None]) -> dict[str, Any]:
    """Score submitted answers. Every gradable question must be answered first."""
    gradable = [q for q in questions if q["correct_index"] is not None]
    if not gradable:
        raise WorkflowError("This knowledge check has no usable answer key, so it cannot be graded.")
    unanswered = [q["id"] for q in gradable if answers.get(q["id"]) is None]
    if unanswered:
        raise WorkflowError(f"Answer every question before submitting ({len(unanswered)} left).")
    results = []
    for question in questions:
        selected = answers.get(question["id"])
        if selected is not None and not 0 <= selected < len(question["options"]):
            raise WorkflowError("A submitted answer does not match the available options.")
        is_gradable = question["correct_index"] is not None
        results.append(
            {
                "id": question["id"],
                "selected": selected,
                "correct_index": question["correct_index"],
                "gradable": is_gradable,
                "is_correct": is_gradable and selected == question["correct_index"],
                "feedback": question["feedback"],
            }
        )
    score = sum(1 for r in results if r["is_correct"])
    total = len(gradable)
    return {
        "score": score,
        "total": total,
        "percent": round(100 * score / total),
        "passed": score / total >= PASS_MARK,
        "pass_mark_percent": round(100 * PASS_MARK),
        "ungraded": len(questions) - total,
        "results": results,
    }


# --------------------------------------------------------------------------------------
# Roleplay practice (decision tree)
# --------------------------------------------------------------------------------------
END = "end"


def roleplay_nodes(module: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate ``roleplay_decisions``. Returns (nodes, problems); nodes is [] if unusable."""
    raw = module.get("roleplay_decisions")
    if not isinstance(raw, list) or not raw:
        return [], ["This package has no decision-point roleplay."]
    problems: list[str] = []
    nodes: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            problems.append(f"Decision {position} is not an object.")
            continue
        node_id = str(item.get("id") or f"d{position}").strip()
        options = []
        for index, option in enumerate(item.get("options") or [], start=1):
            if not isinstance(option, dict) or not str(option.get("agent_line") or "").strip():
                continue
            options.append(
                {
                    "id": str(option.get("id") or chr(ord("a") + index - 1)).strip(),
                    "agent_line": str(option["agent_line"]).strip(),
                    "demonstrates_behavior": option.get("demonstrates_behavior") is True,
                    "member_reaction": str(option.get("member_reaction") or "").strip(),
                    "coach_feedback": str(option.get("coach_feedback") or "").strip(),
                    "next": str(option.get("next")).strip() if option.get("next") else "",
                }
            )
        if not str(item.get("member_line") or "").strip():
            problems.append(f"Decision {node_id} has no member line.")
        if len(options) < 2:
            problems.append(f"Decision {node_id} needs at least two agent responses.")
        elif len({o["demonstrates_behavior"] for o in options}) < 2:
            problems.append(f"Decision {node_id} must offer both a correct and an incorrect response.")
        if len({o["id"] for o in options}) != len(options):
            problems.append(f"Decision {node_id} has duplicate response ids.")
        nodes.append(
            {"id": node_id, "member_line": str(item.get("member_line") or "").strip(), "options": options}
        )
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        problems.append("Decision ids must be unique.")
    # Fill in the default route (the next decision in order) and check every pointer.
    for position, node in enumerate(nodes):
        following = ids[position + 1] if position + 1 < len(ids) else END
        for option in node["options"]:
            option["next"] = option["next"] or following
            if option["next"] != END and option["next"] not in ids:
                problems.append(f"Decision {node['id']} points to unknown decision '{option['next']}'.")
    if not problems and _has_cycle(nodes):
        problems.append("The decisions loop back on themselves.")
    return ([], problems) if problems else (nodes, [])


def _has_cycle(nodes: list[dict[str, Any]]) -> bool:
    graph = {n["id"]: {o["next"] for o in n["options"] if o["next"] != END} for n in nodes}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in done:
            return False
        if node_id in visiting:
            return True
        visiting.add(node_id)
        looped = any(visit(target) for target in graph.get(node_id, ()))
        visiting.discard(node_id)
        done.add(node_id)
        return looped

    return any(visit(node_id) for node_id in graph)


def start_roleplay(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Fresh practice state positioned at the first decision."""
    if not nodes:
        raise WorkflowError("This package has no decision-point roleplay to practise.")
    return {"current": nodes[0]["id"], "path": [], "finished": False}


def choose(nodes: list[dict[str, Any]], state: dict[str, Any], option_id: str) -> dict[str, Any]:
    """Record the trainee's choice at the current decision and move to the next one."""
    if state.get("finished"):
        raise WorkflowError("This practice run is finished. Start again to retry.")
    node = next((n for n in nodes if n["id"] == state.get("current")), None)
    if node is None:
        raise WorkflowError("The practice state does not match this package. Start again.")
    option = next((o for o in node["options"] if o["id"] == option_id), None)
    if option is None:
        raise WorkflowError("That response is not available at this decision.")
    updated = copy.deepcopy(state)
    updated["path"].append(
        {
            "node": node["id"],
            "member_line": node["member_line"],
            "option": option["id"],
            "agent_line": option["agent_line"],
            "demonstrated": option["demonstrates_behavior"],
            "member_reaction": option["member_reaction"],
            "coach_feedback": option["coach_feedback"],
        }
    )
    updated["current"] = "" if option["next"] == END else option["next"]
    updated["finished"] = option["next"] == END
    return updated


def roleplay_summary(state: dict[str, Any]) -> dict[str, Any]:
    """How the trainee did on a finished or in-progress practice run."""
    path = state.get("path", [])
    demonstrated = sum(1 for step in path if step["demonstrated"])
    return {
        "decisions": len(path),
        "demonstrated": demonstrated,
        "finished": bool(state.get("finished")),
        "all_demonstrated": bool(path) and demonstrated == len(path),
    }


# --------------------------------------------------------------------------------------
# Guided practice
# --------------------------------------------------------------------------------------
RATINGS: dict[str, str] = {"met": "Met", "not_met": "Not met", "not_observed": "Not observed"}


def guided_practice_result(criteria: list[str], ratings: list[str]) -> dict[str, Any]:
    """Summarize a coach's rating of each success criterion."""
    if len(ratings) != len(criteria) or any(r not in RATINGS for r in ratings):
        raise WorkflowError("Rate every success criterion before recording the practice.")
    met = sum(1 for r in ratings if r == "met")
    return {"met": met, "total": len(criteria), "all_met": bool(criteria) and met == len(criteria)}


# --------------------------------------------------------------------------------------
# Completion tracking (within this session)
# --------------------------------------------------------------------------------------
PENDING, COMPLETED, NEEDS_REPEAT = "pending", "completed", "needs_repeat"
SCHEDULED, PASSED, FAILED = "scheduled", "passed", "failed"
ITEM_STATUS_LABELS: dict[str, str] = {
    PENDING: "Not started",
    COMPLETED: "Completed",
    NEEDS_REPEAT: "Needs another attempt",
    SCHEDULED: "Scheduled",
    PASSED: "Passed on re-evaluation",
    FAILED: "Missed again on re-evaluation",
}
# completion item -> (label, component that must be in the package)
TRACKED_ITEMS: dict[str, tuple[str, str]] = {
    "coaching_conversation": ("Coaching conversation", "targeted_coaching"),
    "knowledge_check": ("Knowledge check", "knowledge_check"),
    "guided_practice": ("Guided practice", "guided_practice"),
    "roleplay": ("Roleplay practice", "roleplay"),
    "follow_up_evaluation": ("Follow-up evaluation", "follow_up_evaluation"),
}
_DONE = frozenset({COMPLETED, PASSED})
_ALLOWED_STATUS: dict[str, frozenset[str]] = {
    "follow_up_evaluation": frozenset({PENDING, SCHEDULED, PASSED, FAILED}),
}
_DEFAULT_ALLOWED = frozenset({PENDING, COMPLETED, NEEDS_REPEAT})


def new_tracker(employees: list[str], components: list[str]) -> dict[str, dict[str, dict[str, str]]]:
    """A completion record for each assigned employee, covering the package's components."""
    items = [key for key, (_label, component) in TRACKED_ITEMS.items() if component in components]
    return {
        employee: {item: {"status": PENDING, "detail": "", "updated_at": "", "updated_by": ""} for item in items}
        for employee in employees
    }


def record_item(
    tracker: dict[str, dict[str, dict[str, str]]],
    employee: str,
    item: str,
    *,
    status: str,
    detail: str = "",
    actor: str,
    role: str,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return a copy of ``tracker`` with one item updated for one employee."""
    require(role, "record_practice")
    if employee not in tracker:
        raise WorkflowError(f"{employee} is not assigned to this coaching package.")
    if item not in tracker[employee]:
        raise WorkflowError("That activity is not part of this coaching package.")
    if status not in _ALLOWED_STATUS.get(item, _DEFAULT_ALLOWED):
        raise WorkflowError("That status does not apply to this activity.")
    updated = copy.deepcopy(tracker)
    updated[employee][item] = {
        "status": status,
        "detail": " ".join(str(detail or "").split())[:300],
        "updated_at": now_iso(),
        "updated_by": str(actor or "").strip(),
    }
    return updated


def validate_follow_up_date(value: str, today: date | None = None) -> str:
    """A follow-up evaluation is scheduled for today or later (ISO date returned)."""
    try:
        when = date.fromisoformat(str(value))
    except ValueError as exc:
        raise WorkflowError("The follow-up date must be a valid date (YYYY-MM-DD).") from exc
    if when < (today or date.today()):
        raise WorkflowError("Schedule the follow-up evaluation for today or later.")
    return when.isoformat()


def tracker_summary(tracker: dict[str, dict[str, dict[str, str]]]) -> dict[str, Any]:
    """Completion counts for one package."""
    by_employee = {}
    done = total = 0
    follow_up = {PENDING: 0, SCHEDULED: 0, PASSED: 0, FAILED: 0}
    for employee, items in tracker.items():
        finished = sum(1 for entry in items.values() if entry["status"] in _DONE)
        by_employee[employee] = {
            "done": finished,
            "total": len(items),
            "percent": round(100 * finished / len(items)) if items else 0,
        }
        done += finished
        total += len(items)
        if "follow_up_evaluation" in items:
            follow_up[items["follow_up_evaluation"]["status"]] += 1
    return {
        "employees": len(tracker),
        "done": done,
        "total": total,
        "percent": round(100 * done / total) if total else 0,
        "by_employee": by_employee,
        "follow_up": follow_up,
    }


# --------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------
def package_markdown(
    module: dict[str, Any],
    finding: dict[str, Any],
    decision: dict[str, Any] | None,
    components: list[str],
    source_note: str,
) -> str:
    """Render one coaching package as Markdown for use outside the application."""
    lines = [
        f"# Coaching package: {module.get('module_title', '')}",
        "",
        f"_{source_note}_",
        "",
        f"**Finding {finding.get('id', '')}:** {finding.get('title', '')}",
        f"**QA requirement:** {finding.get('behavior', '')} ({finding.get('section', '')})",
        f"**Defect rate:** {finding.get('defect_rate_pct', '?')}% "
        f"({finding.get('failed', '?')} of {finding.get('evaluated', '?')} scored items)",
        f"**Root cause type:** {finding.get('root_cause_type', '')}",
    ]
    if decision:
        lines.append(
            f"**Supervisor decision:** {decision.get('outcome_label', '')} by "
            f"{decision.get('decided_by', '')} at {decision.get('decided_at', '')}"
        )
    lines += ["", "## Targeted coaching", f"**Learning objective:** {module.get('learning_objective', '')}", ""]
    lines += [f"**Why this addresses the gap:** {module.get('why_this_fixes_the_gap', '')}", ""]
    lines += [f"- {step}" for step in module.get("instruction", [])]

    if "policy_refresher" in components and module.get("policy_refresher"):
        refresher = module["policy_refresher"]
        lines += ["", "## Policy refresher", str(refresher.get("summary", "")), ""]
        lines += [f"- {point}" for point in refresher.get("key_points", [])]
        if refresher.get("confirm_with_policy_owner"):
            lines += ["", "Confirm with the policy owner before use:"]
            lines += [f"- {point}" for point in refresher["confirm_with_policy_owner"]]
    if "knowledge_check" in components:
        lines += ["", "## Knowledge check"]
        for number, question in enumerate(quiz_questions(module), start=1):
            lines += ["", f"{number}. {question['question']}"]
            lines += [f"   - {option}" for option in question["options"]]
            if question["correct_index"] is not None:
                lines.append(f"   Answer: {question['options'][question['correct_index']]}")
            if question["feedback"]:
                lines.append(f"   Why: {question['feedback']}")
    if "guided_practice" in components and module.get("hands_on_exercise"):
        exercise = module["hands_on_exercise"]
        lines += [
            "",
            f"## Guided practice: {exercise.get('title', '')}",
            f"Trainee: {exercise.get('trainee_instructions', '')}",
            f"Facilitator: {exercise.get('facilitator_instructions', '')}",
            "Success criteria:",
        ]
        lines += [f"- {item}" for item in exercise.get("success_criteria", [])]
    if "customer_scenario" in components and module.get("simulation_scenario"):
        scenario = module["simulation_scenario"]
        lines += [
            "",
            "## Customer scenario (fictional)",
            f"Call driver: {scenario.get('call_driver', '')}",
            f"Member persona: {scenario.get('member_persona', '')}",
            f"Member emotion: {scenario.get('member_emotion', '')}",
            f"Context: {scenario.get('scenario_context', '')}",
            f"Behavioral target: {scenario.get('behavioral_target', '')}",
            f"Expected agent response: {scenario.get('expected_agent_response', '')}",
            "Scoring criteria:",
        ]
        lines += [f"- {item}" for item in scenario.get("scoring_criteria", [])]
    if "roleplay" in components:
        lines += ["", "## Roleplay script"]
        for turn in module.get("roleplay_script", []):
            speaker = str(turn.get("speaker", "")).upper()
            if speaker == "BRANCH":
                lines.append(
                    f"- BRANCH - if demonstrated: {turn.get('if_agent_demonstrates_behavior', '')} "
                    f"| if not: {turn.get('if_agent_does_not', '')}"
                )
            else:
                note = f"  _(coach: {turn['coach_note']})_" if turn.get("coach_note") else ""
                lines.append(f"- **{speaker}:** {turn.get('line', '')}{note}")
    if "coaching_notes" in components and module.get("coaching_notes"):
        notes = module["coaching_notes"]
        lines += ["", "## Supervisor coaching notes", str(notes.get("opening", "")), "", "Questions to ask:"]
        lines += [f"- {item}" for item in notes.get("questions_to_ask", [])]
        lines += ["", "What good looks like:"] + [f"- {item}" for item in notes.get("what_good_looks_like", [])]
        lines += ["", "Commitments to agree:"] + [f"- {item}" for item in notes.get("commitments_to_agree", [])]
    if "remediation_plan" in components and module.get("remediation_plan"):
        plan = module["remediation_plan"]
        lines += ["", "## Remediation plan", f"Goal: {plan.get('goal', '')}", ""]
        for number, step in enumerate(plan.get("steps", []), start=1):
            lines.append(
                f"{number}. {step.get('step', '')} (owner: {step.get('owner', '')}; timing: {step.get('timing', '')})"
            )
        if plan.get("escalation_trigger"):
            lines += ["", f"Escalate if: {plan['escalation_trigger']}"]
    if "follow_up_evaluation" in components and module.get("follow_up_evaluation"):
        follow = module["follow_up_evaluation"]
        lines += [
            "",
            "## Follow-up evaluation",
            f"What to evaluate: {follow.get('what_to_evaluate', '')}",
            f"Sample: {follow.get('sample', '')}",
            f"Success measure: {follow.get('success_measure', '')}",
            f"Review after: {follow.get('review_after', '')}",
        ]
    return "\n".join(lines) + "\n"
