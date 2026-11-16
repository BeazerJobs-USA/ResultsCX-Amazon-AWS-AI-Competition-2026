"""Serialize the reviewed coaching package for download."""


def training_markdown(result, approval, source, status):
    by_id = {finding["id"]: finding for finding in result["approved_findings"]}
    lines = [
        "# Coaching review and training package",
        f"Source: {source}",
        f"Review date: {approval['approved_at']}",
        f"Training status: {status.replace('_', ' ')} ({len(result['modules'])} ready, {len(result['errors'])} failed)",
        "",
    ]
    lines += [
        f"Reviewed by {approval['approved_by']}. "
        f"Findings selected for training: {', '.join(approval['approved_findings']) or 'none'}.",
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
    for error in result["errors"]:
        lines.append(f"- Failed finding {error['finding_id']}: {error['error']}")
    return "\n".join(lines)
