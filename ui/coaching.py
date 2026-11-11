"""Stage 4: coaching, practice, and follow-up.

For each finding a supervisor sent to coaching or remediation, the supervisor
chooses what the package contains and who it is assigned to, then generates it.
The package workspace then offers:

* a knowledge check that is graded against the package's answer key, with the
  key shown only after submission
* decision-point roleplay, where the trainee chooses the agent's response and a
  scripted member reaction and coach feedback follow from that choice
* guided practice rated criterion by criterion
* follow-up scheduling and a completion record per employee

The roleplay is branching practice with fixed content. It is not a live
conversation with a model, and the page says so where the practice starts.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import streamlit as st

from ui import actions, components as ui, state
from workflow import coaching, common, operations, review
from workflow.common import WorkflowError

COMPLETION = "completion"
_SECTION_LABELS = {key: component.label for key, component in coaching.COMPONENTS.items()}
_SECTION_LABELS[COMPLETION] = "Completion record"
_FOLLOW_UP_OUTCOMES = {
    coaching.PASSED: "Requirement met on re-evaluation",
    coaching.FAILED: "Requirement missed again",
}


def render() -> None:
    ui.page_header(
        state.COACHING,
        "Build a coaching package for each finding a supervisor approved, run the practice with the "
        "employee, and record what was completed.",
    )
    diagnosis = st.session_state["diagnosis"]
    if not diagnosis:
        ui.empty_state(
            "There are no approved findings yet. Start the analysis in stage 1.",
            "Open intake",
            state.INTAKE,
            key="coach_to_intake",
        )
        return
    store = state.store()
    ready = [f for f in diagnosis["findings"] if review.coaching_ready(store["decisions"].get(f["id"]))]
    if not ready:
        ui.empty_state(
            "No finding has been sent to coaching. In supervisor review, approve a finding and choose "
            "targeted coaching or a remediation plan as its next action.",
            "Open supervisor review",
            state.REVIEW,
            key="coach_to_review",
        )
        return
    settings = state.page("coaching")
    modules = state.modules_by_finding()
    by_id = {f["id"]: f for f in ready}
    if settings.get("finding_id") not in by_id:
        settings["finding_id"] = next((fid for fid in by_id if fid not in modules), list(by_id)[0])
    finding_id = ui.selectbox(
        "Approved finding",
        list(by_id),
        settings,
        "finding_id",
        key="coach_finding",
        format_func=lambda fid: (
            f"{fid} · {by_id[fid]['title']} ({'package generated' if fid in modules else 'no package yet'})"
        ),
        dynamic=True,
    )
    finding = actions.approved_finding(finding_id)
    record = store["decisions"][finding_id]
    if finding is None:
        st.error("This finding is no longer approved for coaching. Check its decision in supervisor review.")
        return
    _finding_summary(finding, record)
    _approach(finding, record)

    training = st.session_state["training"] or {}
    failure = next((e for e in training.get("errors", []) if e["finding_id"] == finding_id), None)
    module = modules.get(finding_id)
    package = store["packages"].get(finding_id)
    if failure:
        st.error(
            "The last attempt to generate this package failed"
            + (". The earlier package is shown below. " if module else ". ")
            + ui.esc(failure["error"], keep_newlines=True)
        )
    if module and package:
        _workspace(finding, module, package, record)


def _finding_summary(finding: dict[str, Any], record: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(
            f"**{ui.esc(finding['id'])} · {ui.esc(finding['title'])}** "
            + ui.badge(record["outcome_label"], "green", ":material/person:")
        )
        ui.facts(
            [
                ("QA requirement", finding.get("behavior", "")),
                ("Root cause the supervisor approved", f"{finding['root_cause_type']}: {finding['root_cause']}"),
                ("Next action", review.NEXT_ACTIONS.get(record["next_action"], record["next_action"])),
                ("Employees who missed it", ", ".join(finding.get("agents_failed", []))),
            ]
        )


# -- approach ----------------------------------------------------------------------------
def _approach(finding: dict[str, Any], record: dict[str, Any]) -> None:
    finding_id = finding["id"]
    package = state.store()["packages"].get(finding_id)
    approaches = state.page("coaching").setdefault("approach", {})
    if finding_id not in approaches:
        selected = package["components"] if package else coaching.default_components(record["next_action"])
        approaches[finding_id] = {
            "components": [key for key in selected if key != coaching.ALWAYS_INCLUDED],
            "employees": list(package["employees"] if package else finding.get("agents_failed", [])),
        }
    approach = approaches[finding_id]
    optional = [key for key in coaching.COMPONENTS if key != coaching.ALWAYS_INCLUDED]
    with st.container(border=True):
        st.markdown("#### Coaching approach")
        allowed = ui.role_allows("generate_coaching", "generate a coaching package")
        chosen = ui.multiselect(
            "Package contents (targeted coaching is always included)",
            optional,
            approach,
            "components",
            key=f"coach_components_{finding_id}",
            format_func=lambda key: coaching.COMPONENTS[key].label,
            disabled=not allowed,
        )
        employees = ui.multiselect(
            "Assign to",
            finding.get("agents_failed", []),
            approach,
            "employees",
            key=f"coach_employees_{finding_id}",
            help="Employees who missed this requirement in the loaded QA data.",
            dynamic=True,
            disabled=not allowed,
        )
        with st.expander("What each component contains"):
            st.markdown(
                ui.md_table(
                    ["Component", "Contents"],
                    [[ui.esc(c.label), ui.esc(c.description)] for c in coaching.COMPONENTS.values()],
                )
            )
        if state.mode() == state.DEMO:
            st.caption(
                "Offline demonstration: the package is handwritten content for this requirement, not "
                "model output. No AWS call is made."
            )
        elif state.mode() == state.LIVE:
            st.caption(
                "Sends the approved finding, its evidence, and the selected contents to Amazon Bedrock "
                "AgentCore in one call. The reply is validated before it is shown."
            )
        if package and package.get("stale"):
            st.warning(
                "The supervisor decision changed after this package was generated. Regenerate it so the "
                "package matches the current decision."
            )
        if not employees:
            st.caption("Assign at least one employee to generate the package.")
        if st.button(
            "Regenerate with these contents" if package else "Generate coaching package",
            key=f"coach_generate_{finding_id}",
            type="secondary" if package else "primary",
            disabled=not allowed or not employees,
        ):
            _generate(finding_id, [coaching.ALWAYS_INCLUDED, *chosen], employees)


def _generate(finding_id: str, components: list[str], employees: list[str]) -> None:
    try:
        if state.mode() == state.LIVE:
            with st.status("Asking Amazon Bedrock AgentCore for the coaching package...", expanded=True) as status:
                progress = st.empty()
                received = [0]

                def on_text(chunk: str) -> None:
                    received[0] += len(chunk)
                    if received[0] % 400 < len(chunk):
                        progress.caption(f"Receiving the package: {received[0]:,} characters")

                actions.generate_package(finding_id, components, employees, on_text)
                status.update(label="Package received and validated.", state="complete", expanded=False)
        else:
            actions.generate_package(finding_id, components, employees)
    except actions.ACTION_ERRORS as exc:
        st.error("The package was not generated. " + ui.esc(str(exc), keep_newlines=True))
        return
    state.flash("success", f"Coaching package ready for {finding_id}, assigned to {', '.join(employees)}.")
    st.rerun()


# -- workspace ---------------------------------------------------------------------------
def _recordable() -> tuple[bool, str]:
    """Whether practice results can be recorded, and why not when they cannot."""
    _name, role = state.actor()
    if state.read_only():
        return False, "Results are shown but not recorded, because this is a replayed run."
    if not common.can(role, "record_practice"):
        return False, (
            f"Results are shown but not recorded. Recording needs the "
            f"{common.allowed_roles('record_practice')} role."
        )
    return True, ""


def _record(finding_id: str, employee: str, item: str, status: str, detail: str) -> bool:
    name, role = state.actor()
    try:
        operations.record_practice(
            state.store(), finding_id, employee, item, status=status, detail=detail, actor=name, role=role
        )
    except WorkflowError as exc:
        st.error(ui.esc(str(exc)))
        return False
    actions.persist_review()
    return True


def _workspace(
    finding: dict[str, Any], module: dict[str, Any], package: dict[str, Any], record: dict[str, Any]
) -> None:
    finding_id = finding["id"]
    st.subheader("Coaching package")
    source_label = ui.DEMO_LABEL if package["source"] == state.DEMO else ui.AI_LABEL
    st.markdown(f"### {ui.esc(module['module_title'])}")
    st.markdown(source_label + " " + ui.badge("Built from a supervisor-approved finding", "green", ":material/person:"))
    ui.facts(
        [
            ("Delivery", module.get("delivery", "")),
            ("Planned length", f"{module.get('duration_minutes', '?')} minutes"),
            ("Assigned to", ", ".join(package["employees"])),
        ]
    )
    _checks(finding_id, module)

    practice = state.page("coaching").setdefault("practice", {}).setdefault(finding_id, {})
    employee = ui.selectbox(
        "Practising with",
        package["employees"],
        practice,
        "employee",
        key=f"coach_employee_{finding_id}",
        help="Practice and follow-up results are recorded for this employee.",
        dynamic=True,
    )
    recordable, reason = _recordable()
    if not recordable:
        st.caption(reason)
    sections = [*package["components"], COMPLETION]
    section = ui.radio(
        "Package section",
        sections,
        practice,
        "section",
        key=f"coach_section_{finding_id}",
        format_func=_SECTION_LABELS.get,
        horizontal=True,
        dynamic=True,
    )
    with st.container(border=True):
        if section == "targeted_coaching":
            _targeted_coaching(finding_id, module, employee, recordable)
        elif section == "policy_refresher":
            _policy_refresher(module)
        elif section == "knowledge_check":
            _knowledge_check(finding_id, module, employee, recordable)
        elif section == "guided_practice":
            _guided_practice(finding_id, module, employee, recordable)
        elif section == "customer_scenario":
            _customer_scenario(module)
        elif section == "roleplay":
            _roleplay(finding_id, module, employee, recordable)
        elif section == "coaching_notes":
            _coaching_notes(module)
        elif section == "remediation_plan":
            _remediation_plan(module)
        elif section == "follow_up_evaluation":
            _follow_up(finding_id, module, employee, recordable)
        else:
            _completion(finding_id)
    _exports(finding, module, package, record)


def _checks(finding_id: str, module: dict[str, Any]) -> None:
    training = st.session_state["training"] or {}
    alignment = next((r for r in training.get("alignment", []) if r["finding_id"] == finding_id), None)
    with st.expander("Evidence and alignment checks on this package"):
        total = module.get("citations_total", 0)
        if not total:
            st.caption("The package text cites no QA record IDs, so there were none to check.")
        else:
            invented = [c["record_id"] for c in module.get("citations", []) if not c["exists"]]
            off_topic = module.get("citations_off_behavior", [])
            if invented:
                st.error(
                    "The package cites record IDs that are not in the QA data: "
                    + ui.esc(", ".join(invented))
                    + ". Do not rely on those statements."
                )
            elif off_topic:
                st.warning(
                    "These cited records exist but belong to a different requirement: "
                    + ui.esc(", ".join(off_topic))
                    + "."
                )
            else:
                st.markdown(
                    ui.badge(f"All {total} cited record IDs belong to this requirement", "green", ":material/check_circle:")
                )
        if alignment:
            components = alignment.get("components") or list(coaching.CORE_COMPONENTS)

            def mark(ok: bool, wanted: bool = True) -> str:
                if not wanted:
                    return "Not requested"
                return ui.badge("Yes", "green", ":material/check_circle:") if ok else ui.badge("Check by hand", "orange", ":material/warning:")

            st.markdown(
                ui.md_table(
                    ["Check", "Result"],
                    [
                        ["Objective or scenario names the diagnosed requirement", mark(alignment["target_matches_behavior"])],
                        ["Knowledge check names the requirement", mark(alignment["quiz_matches_behavior"], "knowledge_check" in components)],
                        ["Scenario has scoring criteria", mark(alignment["has_scoring"], "customer_scenario" in components)],
                        ["Roleplay script has at least four turns", mark(alignment["has_roleplay"], "roleplay" in components)],
                    ],
                )
            )
        st.caption(
            "These are keyword and structure checks. They show the package is about the right "
            "requirement, not that it is effective training."
        )


# -- sections ----------------------------------------------------------------------------
def _targeted_coaching(finding_id: str, module: dict[str, Any], employee: str, recordable: bool) -> None:
    st.markdown("**Learning objective**")
    ui.quote(module.get("learning_objective"))
    st.markdown("**Why this addresses the gap**")
    ui.quote(module.get("why_this_fixes_the_gap"))
    st.markdown("**Teaching points**")
    ui.bullets(module.get("instruction", []))
    if module.get("existing_material_to_reuse"):
        st.markdown("**Existing training material to reuse**")
        ui.bullets(module["existing_material_to_reuse"])
    st.divider()
    settings = state.page("coaching").setdefault("conversation", {}).setdefault(f"{finding_id}|{employee}", {})
    today = dt.date.today()
    left, right = st.columns([2, 1], vertical_alignment="bottom")
    with left:
        held = ui.date_input(
            f"Date the coaching conversation with {employee} took place",
            settings,
            "held",
            key=f"coach_conv_date_{finding_id}_{employee}",
            default=today,
            max_value=today,
        )
    with right:
        if st.button(
            "Record conversation",
            key=f"coach_conv_record_{finding_id}_{employee}",
            disabled=not recordable,
        ):
            if _record(finding_id, employee, "coaching_conversation", coaching.COMPLETED, f"held {held.isoformat()}"):
                state.flash("success", f"Coaching conversation recorded for {employee}.")
                st.rerun()


def _policy_refresher(module: dict[str, Any]) -> None:
    refresher = module.get("policy_refresher") or {}
    st.markdown("**What the requirement asks for**")
    ui.quote(refresher.get("summary"), "No summary was provided.")
    st.markdown("**Key points**")
    ui.bullets(refresher.get("key_points", []))
    if refresher.get("confirm_with_policy_owner"):
        st.markdown("**Confirm with the policy owner before use**")
        ui.bullets(refresher["confirm_with_policy_owner"])
    st.caption(
        "This refresher restates the QA requirement. It is not the organization's policy text and "
        "cites no policy document."
    )


def _knowledge_check(finding_id: str, module: dict[str, Any], employee: str, recordable: bool) -> None:
    questions = coaching.quiz_questions(module)
    if not questions:
        st.info("This package has no usable knowledge check.")
        return
    practice = st.session_state["practice"].setdefault(
        f"quiz|{finding_id}|{employee}", {"attempt": 1, "answers": {}, "result": None}
    )
    attempt, result = practice["attempt"], practice["result"]
    st.caption(
        f"Attempt {attempt} for {ui.esc(employee)}. Answer every question, then submit. The pass mark is "
        f"{round(100 * coaching.PASS_MARK)}%. The answer key is shown only after submission."
    )
    graded = {r["id"]: r for r in result["results"]} if result else {}
    for number, question in enumerate(questions, start=1):
        options = question["options"]
        ui.radio(
            f"Question {number}: {ui.esc(question['question'])}",
            list(range(len(options))),
            practice["answers"],
            question["id"],
            key=f"quiz_{finding_id}_{employee}_{attempt}_{question['id']}",
            format_func=lambda index, options=options: ui.esc(options[index]),
            allow_empty=True,
            disabled=result is not None,
        )
        if result:
            outcome = graded[question["id"]]
            if not outcome["gradable"]:
                st.markdown(ui.insufficient_badge("Not graded: the answer key does not identify an option"))
            elif outcome["is_correct"]:
                st.markdown(ui.badge("Correct", "green", ":material/thumb_up:"))
            else:
                st.markdown(
                    ui.badge("Incorrect", "red", ":material/thumb_down:")
                    + " Correct answer: "
                    + ui.esc(options[outcome["correct_index"]])
                )
            if outcome["feedback"]:
                st.caption(ui.esc(outcome["feedback"]))
    if result is None:
        if st.button("Submit answers", key=f"quiz_submit_{finding_id}_{employee}_{attempt}", type="primary"):
            try:
                graded_result = coaching.grade_quiz(questions, practice["answers"])
            except WorkflowError as exc:
                st.error(ui.esc(str(exc)))
                return
            practice["result"] = graded_result
            if recordable:
                _record(
                    finding_id,
                    employee,
                    "knowledge_check",
                    coaching.COMPLETED if graded_result["passed"] else coaching.NEEDS_REPEAT,
                    f"{graded_result['score']} of {graded_result['total']} correct on attempt {attempt}",
                )
            st.rerun()
        return
    verdict = (
        ui.badge("Passed", "green", ":material/thumb_up:")
        if result["passed"]
        else ui.badge("Not passed", "red", ":material/thumb_down:")
    )
    st.markdown(
        f"**Score: {result['score']} of {result['total']} ({result['percent']}%)** {verdict}"
        + (f" {result['ungraded']} question(s) could not be graded." if result["ungraded"] else "")
    )
    if st.button("Try again", key=f"quiz_retry_{finding_id}_{employee}_{attempt}"):
        practice.update({"attempt": attempt + 1, "answers": {}, "result": None})
        st.rerun()


def _guided_practice(finding_id: str, module: dict[str, Any], employee: str, recordable: bool) -> None:
    exercise = module.get("hands_on_exercise") or {}
    st.markdown(f"**{ui.esc(exercise.get('title', 'Guided practice'))}**")
    ui.facts(
        [
            ("Trainee", exercise.get("trainee_instructions", "")),
            ("Facilitator", exercise.get("facilitator_instructions", "")),
        ]
    )
    criteria = [str(c) for c in exercise.get("success_criteria", [])]
    if not criteria:
        st.info("This exercise lists no success criteria to rate.")
        return
    ratings = st.session_state["practice"].setdefault(f"guided|{finding_id}|{employee}", {})
    st.markdown(f"**Rate each success criterion after observing {ui.esc(employee)}**")
    choices = ["", *coaching.RATINGS]
    for index, criterion in enumerate(criteria):
        ui.selectbox(
            ui.esc(criterion),
            choices,
            ratings,
            str(index),
            key=f"guided_{finding_id}_{employee}_{index}",
            format_func=lambda key: coaching.RATINGS.get(key, "Not rated yet"),
        )
    if st.button("Record guided practice", key=f"guided_record_{finding_id}_{employee}", disabled=not recordable):
        try:
            outcome = coaching.guided_practice_result(criteria, [ratings.get(str(i), "") for i in range(len(criteria))])
        except WorkflowError as exc:
            st.error(ui.esc(str(exc)))
            return
        status = coaching.COMPLETED if outcome["all_met"] else coaching.NEEDS_REPEAT
        if _record(finding_id, employee, "guided_practice", status, f"{outcome['met']} of {outcome['total']} criteria met"):
            state.flash(
                "success" if outcome["all_met"] else "warning",
                f"Guided practice recorded for {employee}: {outcome['met']} of {outcome['total']} criteria met.",
            )
            st.rerun()


def _customer_scenario(module: dict[str, Any]) -> None:
    scenario = module.get("simulation_scenario") or {}
    st.markdown(ui.badge("Fictional scenario", "orange", ":material/theater_comedy:"))
    ui.facts(
        [
            ("Why the member is calling", scenario.get("call_driver", "")),
            ("Member", scenario.get("member_persona", "")),
            ("Member's state of mind", scenario.get("member_emotion", "")),
            ("Background", scenario.get("scenario_context", "")),
            ("Behavior the agent must demonstrate", scenario.get("behavioral_target", "")),
            ("What good looks like", scenario.get("expected_agent_response", "")),
        ]
    )
    st.markdown("**Scoring criteria**")
    ui.bullets(scenario.get("scoring_criteria", []))


def _roleplay(finding_id: str, module: dict[str, Any], employee: str, recordable: bool) -> None:
    st.markdown("**Decision-point practice**")
    st.caption(
        "Branching practice with fixed content. At each decision you choose what the agent says, and "
        "the scripted member reaction and coach feedback follow from that choice. It is not a live "
        "conversation with an AI."
    )
    nodes, problems = coaching.roleplay_nodes(module)
    if not nodes:
        st.info(
            "This package has no usable decision-point practice ("
            + ui.esc(" ".join(problems))
            + "). The script below can still be read through with a coach."
        )
    else:
        _roleplay_practice(finding_id, nodes, employee, recordable)
    with st.expander("Script to read through with a coach (not interactive)"):
        for turn in module.get("roleplay_script", []):
            speaker = str(turn.get("speaker", "")).upper()
            if speaker == "BRANCH":
                st.markdown(
                    f"**Branch.** If the agent demonstrates the behavior: "
                    f"{ui.esc(turn.get('if_agent_demonstrates_behavior', ''))}  \n"
                    f"If not: {ui.esc(turn.get('if_agent_does_not', ''))}"
                )
            else:
                who = "Agent (trainee)" if speaker == "AGENT" else "Member"
                st.markdown(f"**{who}:** {ui.esc(turn.get('line', ''))}")
                if turn.get("coach_note"):
                    st.caption("Coach note: " + ui.esc(turn["coach_note"]))


def _roleplay_practice(finding_id: str, nodes: list[dict[str, Any]], employee: str, recordable: bool) -> None:
    practice = st.session_state["practice"]
    key = f"roleplay|{finding_id}|{employee}"
    if key not in practice:
        practice[key] = {"run": 1, "state": coaching.start_roleplay(nodes), "choices": {}}
    run = practice[key]
    current = run["state"]
    for number, step in enumerate(current["path"], start=1):
        with st.container(border=True):
            verdict = (
                ui.badge("Target behavior demonstrated", "green", ":material/thumb_up:")
                if step["demonstrated"]
                else ui.badge("Target behavior missed", "red", ":material/thumb_down:")
            )
            st.markdown(
                f"**Decision {number}. Member:** {ui.esc(step['member_line'])}  \n"
                f"**You said:** {ui.esc(step['agent_line'])}  \n"
                f"{verdict}  \n"
                f"**Member:** {ui.esc(step['member_reaction'])}"
            )
            st.caption("Coach feedback: " + ui.esc(step["coach_feedback"]))
    widget = f"{finding_id}_{employee}_{run['run']}"
    if not current["finished"]:
        node = next(n for n in nodes if n["id"] == current["current"])
        lines = {option["id"]: option["agent_line"] for option in node["options"]}
        st.markdown(f"**Decision {len(current['path']) + 1}. Member:** {ui.esc(node['member_line'])}")
        picked = ui.radio(
            "What does the agent say?",
            list(lines),
            run["choices"],
            node["id"],
            key=f"rp_choice_{widget}_{node['id']}",
            format_func=lambda option_id: ui.esc(lines[option_id]),
            allow_empty=True,
        )
        if st.button("Say this", key=f"rp_say_{widget}_{node['id']}", type="primary", disabled=picked is None):
            try:
                run["state"] = coaching.choose(nodes, current, picked)
            except WorkflowError as exc:
                st.error(ui.esc(str(exc)))
                return
            if run["state"]["finished"] and recordable:
                summary = coaching.roleplay_summary(run["state"])
                _record(
                    finding_id,
                    employee,
                    "roleplay",
                    coaching.COMPLETED if summary["all_demonstrated"] else coaching.NEEDS_REPEAT,
                    f"target behavior at {summary['demonstrated']} of {summary['decisions']} decisions",
                )
            st.rerun()
        return
    summary = coaching.roleplay_summary(current)
    verdict = (
        ui.badge("Practice complete", "green", ":material/thumb_up:")
        if summary["all_demonstrated"]
        else ui.badge("Repeat the practice", "orange", ":material/replay:")
    )
    st.markdown(
        f"**Result: the target behavior was demonstrated at {summary['demonstrated']} of "
        f"{summary['decisions']} decisions.** {verdict}"
    )
    if st.button("Start again", key=f"rp_restart_{widget}"):
        practice[key] = {"run": run["run"] + 1, "state": coaching.start_roleplay(nodes), "choices": {}}
        st.rerun()


def _coaching_notes(module: dict[str, Any]) -> None:
    notes = module.get("coaching_notes") or {}
    st.markdown("**Opening the conversation**")
    ui.quote(notes.get("opening"), "No opening was provided.")
    for title, field in (
        ("Questions to ask", "questions_to_ask"),
        ("What good looks like", "what_good_looks_like"),
        ("Commitments to agree", "commitments_to_agree"),
    ):
        if notes.get(field):
            st.markdown(f"**{title}**")
            ui.bullets(notes[field])


def _remediation_plan(module: dict[str, Any]) -> None:
    plan = module.get("remediation_plan") or {}
    st.markdown("**Goal**")
    ui.quote(plan.get("goal"), "No goal was provided.")
    if plan.get("steps"):
        st.markdown(
            ui.md_table(
                ["Step", "Owner", "Timing"],
                [[ui.esc(s.get("step")), ui.esc(s.get("owner")), ui.esc(s.get("timing"))] for s in plan["steps"]],
            )
        )
    if plan.get("escalation_trigger"):
        st.markdown("**Return to the supervisor for another decision if**")
        ui.quote(plan["escalation_trigger"])
    st.caption(
        "A remediation plan contains coaching and practice steps only. Administrative action is a "
        "separate, supervisor-written referral and is never proposed by the AI."
    )


def _follow_up(finding_id: str, module: dict[str, Any], employee: str, recordable: bool) -> None:
    plan = module.get("follow_up_evaluation") or {}
    ui.facts(
        [
            ("What to re-evaluate", plan.get("what_to_evaluate", "")),
            ("Sample", plan.get("sample", "")),
            ("Success measure", plan.get("success_measure", "")),
            ("Review after", plan.get("review_after", "")),
        ]
    )
    st.divider()
    entry = state.store()["trackers"].get(finding_id, {}).get(employee, {}).get("follow_up_evaluation")
    if entry is None:
        st.caption("Follow-up evaluation is not tracked for this employee.")
        return
    st.markdown(f"**Follow-up for {ui.esc(employee)}:** {_item_badge(entry)}")
    settings = state.page("coaching").setdefault("follow_up", {}).setdefault(f"{finding_id}|{employee}", {})
    today = dt.date.today()
    left, right = st.columns([2, 1], vertical_alignment="bottom")
    with left:
        when = ui.date_input(
            "Re-evaluation date",
            settings,
            "date",
            key=f"follow_date_{finding_id}_{employee}",
            default=today + dt.timedelta(days=14),
            min_value=today,
        )
    with right:
        if st.button("Schedule follow-up", key=f"follow_schedule_{finding_id}_{employee}", disabled=not recordable):
            try:
                scheduled = coaching.validate_follow_up_date(when.isoformat())
            except WorkflowError as exc:
                st.error(ui.esc(str(exc)))
                return
            if _record(finding_id, employee, "follow_up_evaluation", coaching.SCHEDULED, f"scheduled for {scheduled}"):
                state.flash("success", f"Follow-up evaluation scheduled for {employee} on {scheduled}.")
                st.rerun()
    if entry["status"] != coaching.PENDING:
        left, right = st.columns([2, 1], vertical_alignment="bottom")
        with left:
            outcome = ui.selectbox(
                "Result of the re-evaluation",
                list(_FOLLOW_UP_OUTCOMES),
                settings,
                "outcome",
                key=f"follow_outcome_{finding_id}_{employee}",
                format_func=_FOLLOW_UP_OUTCOMES.get,
            )
        with right:
            if st.button("Record result", key=f"follow_result_{finding_id}_{employee}", disabled=not recordable):
                if _record(finding_id, employee, "follow_up_evaluation", outcome, _FOLLOW_UP_OUTCOMES[outcome]):
                    state.flash("success", f"Follow-up result recorded for {employee}.")
                    st.rerun()
    st.caption(
        "The result is what a supervisor enters after re-scoring the requirement. This prototype has "
        "no post-coaching QA data and does not measure whether coaching worked."
    )


def _item_badge(entry: dict[str, str]) -> str:
    status = entry["status"]
    label = coaching.ITEM_STATUS_LABELS.get(status, status)
    if status in (coaching.COMPLETED, coaching.PASSED):
        return ui.badge(label, "green", ":material/thumb_up:" if status == coaching.PASSED else ":material/task_alt:")
    if status == coaching.FAILED:
        return ui.badge(label, "red", ":material/thumb_down:")
    if status == coaching.NEEDS_REPEAT:
        return ui.badge(label, "orange", ":material/replay:")
    if status == coaching.SCHEDULED:
        return ui.badge(label, "blue", ":material/event:")
    return ui.pending_badge(label)


def _completion(finding_id: str) -> None:
    tracker = state.store()["trackers"].get(finding_id, {})
    if not tracker:
        st.caption("Nothing is tracked for this package.")
        return
    summary = coaching.tracker_summary(tracker)
    items = list(next(iter(tracker.values())))
    headers = ["Employee", *[coaching.TRACKED_ITEMS[item][0] for item in items], "Done"]
    rows = []
    for employee, entries in tracker.items():
        cells = []
        for item in items:
            entry = entries[item]
            detail = f" {ui.esc(entry['detail'])}" if entry["detail"] else ""
            cells.append(_item_badge(entry) + detail)
        done = summary["by_employee"][employee]
        rows.append([ui.esc(employee), *cells, f"{done['done']} of {done['total']}"])
    st.markdown(ui.md_table(headers, rows))
    st.progress(summary["percent"], text=f"{summary['done']} of {summary['total']} activities completed")
    st.caption(
        "Completion is recorded within this session as practice is run or entered above. It is not "
        "connected to a learning management system."
    )


def _exports(
    finding: dict[str, Any], module: dict[str, Any], package: dict[str, Any], record: dict[str, Any]
) -> None:
    store = state.store()
    if package["source"] == state.DEMO:
        note = "Handwritten demonstration content for an invented dataset. Not model output."
    else:
        note = "AI-generated from a supervisor-approved finding. Review before use."
    left, right = st.columns(2)
    left.download_button(
        "Download this package (.md)",
        coaching.package_markdown(module, finding, record, package["components"], note),
        file_name=f"coaching_package_{finding['id']}.md",
        mime="text/markdown",
        key=f"coach_dl_md_{finding['id']}",
        icon=":material/download:",
        width="stretch",
    )
    right.download_button(
        "Download the run without referrals (.json)",
        json.dumps(
            {
                "diagnosis": st.session_state["diagnosis"],
                "training": st.session_state["training"],
                # Simulated referrals are deliberately left out of every export.
                "review": {key: store[key] for key in ("decisions", "flags", "packages", "trackers", "audit")},
            },
            indent=2,
            default=str,
        ),
        file_name="agent_qa_review_run.json",
        mime="application/json",
        key=f"coach_dl_json_{finding['id']}",
        icon=":material/download:",
        width="stretch",
    )
