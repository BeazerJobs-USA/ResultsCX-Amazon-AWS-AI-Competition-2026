"""QA diagnosis and coaching prompt templates for the public prototype.

Metrics are calculated in Python. The model must cite supplied record IDs,
and its interpretation is reviewed before coaching material is used.
"""

# ======================================================================================
# System prompt (sent with every call, both stages)
# ======================================================================================
SYSTEM_PROMPT = """You are a QA coaching assistant in an educational member-services prototype. Your job is to turn supplied Quality Assurance (QA) records into (1) an evidence-based diagnosis and (2) specific coaching exercises after a reviewer has checked the finding. Do not invent company policy or claim a training outcome.

Ground rules:
1. Evidence first. Every conclusion must cite the record IDs (for example ME-012, BP-004, CO-133) of the evaluator comments that support it. Never invent a record, a number, a name or a quote. If the data does not show something, say so.
2. Keep OBSERVED (what the evaluator wrote) separate from INFERRED (your explanation of why it happened).
3. The defect rates, counts and patterns you receive were computed by software from the raw exports. Use them as given; do not recompute or change them.
4. Classify the likely root cause with these common needs-analysis categories:
   - Knowledge Gap: the agent does not know what to do or what the policy is -> fix with content (eLearning / instructor-led).
   - Skill Gap: the agent knows what to do but does not execute it consistently under real call conditions -> fix with practice (simulation, role-play, coaching).
   - Process Gap: a system, tool, policy or workflow makes the right behavior hard or impossible -> training is NOT the answer; escalate to operations.
   - Will Gap: the agent can do it and knows it, but chooses not to -> accountability coaching, not a course.
   Distinguish recurring patterns (many agents, many calls) from one-off incidents (one agent, one call).
5. Privacy: treat every input as potentially sensitive. The public fixture uses invented records and agent codes. Never guess or reconstruct identities or repeat identifiers that appear unexpectedly.
6. When asked for JSON, reply with ONLY one JSON object: no prose before or after, no markdown code fences.

The fictional examples are not a source of approved procedures, clinical guidance, or HIPAA compliance advice."""


# ======================================================================================
# Rendering helpers: turn the Python data into compact text the model reads easily
# ======================================================================================
def _md_table(headers, rows):
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(out)


def render_metrics_table(payload):
    rows = []
    for b in payload["behaviors"]:
        rows.append(
            [
                b["id"],
                b["section"],
                b["behavior"],
                f"{b['failed']}/{b['evaluated']}",
                f"{b['defect_rate_pct']}%",
                b["pattern"],
                ", ".join(b["agents_failed"]) or "-",
            ]
        )
    return _md_table(
        [
            "id",
            "section",
            "behavior",
            "failed/evaluated",
            "defect rate",
            "pattern",
            "agents that failed",
        ],
        rows,
    )


def render_agent_table(payload):
    rows = []
    for a in payload["agents"]:
        fb = "; ".join(f"{k} (x{v})" for k, v in a["failed_behaviors"].items()) or "-"
        rows.append(
            [
                a["agent"],
                a["calls_evaluated"],
                f"{a['items_failed']}/{a['items_evaluated']}",
                f"{a['fail_rate_pct']}%",
                fb,
            ]
        )
    return _md_table(
        [
            "agent",
            "agent-date groups (proxy)",
            "items failed/evaluated",
            "fail rate",
            "failed behaviors",
        ],
        rows,
    )


def render_evidence(payload, only_behavior_ids=None):
    lines = []
    for b in payload["behaviors"]:
        if only_behavior_ids and b["id"] not in only_behavior_ids:
            continue
        if not b["evidence"]:
            continue
        lines.append(
            f"[{b['id']}] {b['behavior']}  ({b['section']}; {b['failed']}/{b['evaluated']} failed)"
        )
        for e in b["evidence"]:
            result = "FAIL" if e["result"] == "No" else "PASS"
            lines.append(
                f"  {e['record_id']} | {e['agent']} | {e['date']} | {result} | {e['comment']}"
            )
    return "\n".join(lines)


def render_outline(outline, include_resources=False, max_entries=None):
    lines = []
    for e in outline["entries"][:max_entries]:
        parts = [e["week"], e.get("day") or "", e.get("topic") or ""]
        if e.get("detail"):
            parts.append(e["detail"])
        if include_resources and e.get("resource"):
            parts.append(f"(resource: {e['resource']})")
        lines.append(" / ".join(p for p in parts if p))
    return "\n".join(lines)


# ======================================================================================
# STAGE 2 - Needs analysis (diagnosis)
# ======================================================================================
DIAGNOSIS_SCHEMA = """{
  "summary": "3-5 sentences: the overall picture, the dominant root-cause type, and what training should focus on first",
  "findings": [
    {
      "id": "F1",
      "behavior_id": "the id from the DEFECT METRICS table, e.g. B01",
      "title": "short supervisor-facing name of the problem",
      "observed": "what evaluators actually wrote, summarised without interpretation",
      "root_cause_type": "one of: Knowledge Gap | Skill Gap | Process Gap | Will Gap",
      "root_cause": "your inference of WHY this keeps happening, tied to the cited evidence",
      "pattern": "recurring or isolated, and what that implies",
      "evidence": [ {"record_id": "ME-012", "quote": "short exact phrase copied from that comment"} ],
      "curriculum_coverage": "one of: Yes | Partial | No",
      "curriculum_note": "which week/topic of the existing outline covers it, or what is missing",
      "training_recommended": true,
      "recommended_intervention": "one of: simulation | eLearning/ILT | coaching | escalate to operations",
      "confidence": "one of: high | medium | low",
      "confidence_note": "what evidence is thin or what would change your mind"
    }
  ],
  "not_a_training_problem": [ "any issue where training is NOT the answer, and why (may be empty)" ],
  "open_questions": [ "things a supervisor should confirm before training design begins" ]
}"""


def build_diagnosis_prompt(payload, max_findings=5):
    t = payload["totals"]
    sections = "\n".join(
        f"- {v['name']} ({code}): {v['description']}"
        for code, v in payload["sections"].items()
    )
    return f"""STAGE 2 - NEEDS ANALYSIS

Below are QA records for an educational member-services example: {t["calls_evaluated"]} agent-date groups (not unique calls), {t["agents"]} agents, {t["line_items"]} scored line items, {t["failed_items"]} failures, dated {t["date_range"][0]} to {t["date_range"][1]}. Three QA sections were scored:
{sections}

=== DEFECT METRICS (computed by software from the raw exports - use as given) ===
{render_metrics_table(payload)}

=== PER-AGENT VIEW (helps separate "everyone misses one thing" from "one person misses everything") ===
{render_agent_table(payload)}

=== EVALUATOR COMMENTS - THE EVIDENCE (FAIL = behavior not demonstrated on that call) ===
{render_evidence(payload)}

=== EXISTING NEW-HIRE TRAINING OUTLINE (week / day / topic / detail) - use it to judge curriculum coverage ===
{render_outline(payload["training_outline"])}

YOUR TASK
Draft the needs analysis for reviewer validation before any training is designed. Prioritize problems (at most {max_findings}) based on the observed line-item defect rate, how many agents are affected, and risk. Agent-date groups are not unique calls. For each finding include the inferred root cause, 2-5 record IDs with exact supporting phrases copied from their comments, likely curriculum coverage, and whether training is an appropriate response. Do not claim measured impact or cite records not supplied.

Respond with ONLY one JSON object in exactly this structure (the values below describe what to write):
{DIAGNOSIS_SCHEMA}"""


# ======================================================================================
# STAGE 4 - Training design for ONE approved finding
# ======================================================================================
MODULE_SCHEMA = """{
  "finding_id": "the approved finding id, e.g. F1",
  "module_title": "specific title naming the behavior being fixed",
  "learning_objective": "By the end of this module the care coordinator will ... (observable and measurable on a QA form)",
  "why_this_fixes_the_gap": "1-2 sentences tying the objective to the approved root cause and the cited evidence",
  "delivery": "one of: simulation | eLearning/ILT | coaching",
  "duration_minutes": 30,
  "instruction": [ "4-7 concrete teaching points or steps, each specific to this behavior" ],
  "existing_material_to_reuse": [ "entries from the existing outline to reuse or reference (may be empty)" ],
  "knowledge_check": {
    "question": "one scenario-based question about this behavior",
    "options": [ "A) ...", "B) ...", "C) ...", "D) ..." ],
    "correct": "the letter of the correct option",
    "feedback": "why the correct answer is right and the tempting wrong one is wrong"
  },
  "hands_on_exercise": {
    "title": "...",
    "trainee_instructions": "what the trainee does, step by step",
    "facilitator_instructions": "how the facilitator runs and observes it",
    "success_criteria": [ "observable things that mean the trainee demonstrated the behavior" ]
  },
  "simulation_scenario": {
    "scenario_id": "S-1",
    "call_driver": "why the member is calling (must match the situations in the evidence)",
    "member_persona": "fictional first name, age range, plan situation, and temperament",
    "member_emotion": "e.g. frustrated - third contact on the same issue",
    "scenario_context": "realistic backstory: what happened before the call and what the member actually needs",
    "behavioral_target": "the exact QA behavior the agent must demonstrate to pass",
    "expected_agent_response": "benchmark description of excellent performance on this call",
    "scoring_criteria": [ "3-5 pass/fail checks an evaluator could score with the QA form" ]
  },
  "roleplay_script": [
    {"speaker": "MEMBER", "line": "..."},
    {"speaker": "AGENT", "line": "model answer the trainee should approximate", "coach_note": "what to notice here"},
    {"speaker": "BRANCH", "if_agent_demonstrates_behavior": "what the member says next", "if_agent_does_not": "how the member reacts instead, and the coach note"}
  ]
}"""


def build_training_prompt(finding, evidence_lines, related_outline_lines):
    supervisor_note = finding.get("supervisor_note") or "(none)"
    approval_status = finding.get("approval_mode", "human_review")
    return f"""STAGE 4 - TRAINING DESIGN (review status: {approval_status}; use the finding as provided, including edits)

APPROVED FINDING
id: {finding["id"]}
QA behavior: {finding.get("behavior", "")}   (section: {finding.get("section", "")})
defect rate: {finding.get("defect_rate_pct", "?")}% - {finding.get("failed", "?")} of {finding.get("evaluated", "?")} scored line items; pattern: {finding.get("pattern", "")}; agents affected: {", ".join(finding.get("agents_failed", [])) or "-"}
title: {finding.get("title", "")}
observed: {finding.get("observed", "")}
root cause type: {finding.get("root_cause_type", "")}
root cause: {finding.get("root_cause", "")}
recommended intervention: {finding.get("recommended_intervention", "")}
curriculum coverage today: {finding.get("curriculum_coverage", "")} - {finding.get("curriculum_note", "")}
supervisor note: {supervisor_note}

EVIDENCE (evaluator comments this finding is based on)
{evidence_lines or "(no comments available)"}

EXISTING OUTLINE ENTRIES THAT MAY RELATE (reuse or reference them; do not duplicate)
{related_outline_lines or "(none found)"}

REQUIREMENTS
- Design ONE targeted module that fixes this specific gap. Every element must target the approved behavior and root cause. No generic customer-service content.
- Follow the design chain: learning objective -> instruction -> knowledge check -> hands-on exercise -> simulation scenario -> role-play script.
- The scenario must be a realistic healthcare member-services call (benefits, claims, EOBs, care plans, provider outreach, HIPAA verification) that reproduces the situations the evaluators described.
- The role-play script is for a trainee playing the agent against a simulated member: 8-14 turns alternating MEMBER and AGENT, with at least one BRANCH turn where the member's reaction depends on whether the agent demonstrated the target behavior. Add short coach notes on the AGENT turns where the target behavior must appear.
- Scoring criteria must map to the QA behavior so an evaluator could score the practice call with the same form.
- The knowledge-check question must directly test the approved QA behavior, not a generic or adjacent topic.
- Use only fictional member names. Do not reference agent codes or real people.

Respond with ONLY one JSON object in exactly this structure (the values below describe what to write):
{MODULE_SCHEMA}"""

