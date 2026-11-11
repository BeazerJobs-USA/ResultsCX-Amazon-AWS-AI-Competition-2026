"""Workflow logic tests. No Streamlit, no AWS, no network: synthetic data only."""

import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import business_impact  # noqa: E402
import get_data  # noqa: E402
import sample_walkthrough  # noqa: E402
from workflow import analytics, audit, coaching, compliance, intake, operations, referrals, review  # noqa: E402
from workflow.common import WorkflowError, can, is_meaningful_note  # noqa: E402

SUPERVISOR = "Supervisor"
NOTE = "Reviewed the evaluator comment and the scored item; I agree with this finding."


def _data():
    return get_data.load_synthetic_fixture()


def _diagnosis(data):
    return sample_walkthrough.demo_diagnosis(data, None)


def _approve_draft(finding):
    draft = review.new_draft(finding)
    draft.update(decision="approve", control="confirmed", note=NOTE)
    return draft


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.data = _data()
        self.rows = intake.build_queue(self.data)

    def test_queue_matches_the_dataset(self):
        self.assertEqual(len(self.rows), len(self.data["records"]))
        fails = [r for r in self.rows if r["result"] == "Fail"]
        self.assertEqual(len(fails), 11)

    def test_filters_narrow_the_queue(self):
        agent = self.rows[0]["employee"]
        only = intake.filter_queue(self.rows, employees=[agent])
        self.assertTrue(only)
        self.assertTrue(all(r["employee"] == agent for r in only))
        failed = intake.filter_queue(self.rows, results=["Fail"])
        self.assertTrue(all(r["result"] == "Fail" for r in failed))
        self.assertEqual(intake.filter_queue(self.rows), self.rows)

    def test_evidence_contains_no_invented_material(self):
        record_id = next(r["record_id"] for r in self.rows if r["result"] == "Fail")
        evidence = intake.evidence_for(self.data, record_id)
        self.assertIsNotNone(evidence)
        text = repr(evidence).lower()
        # The fixture carries no audio, so none may be produced.
        self.assertNotIn("recording_url", text)
        self.assertIsNone(intake.evidence_for(self.data, "XX-999"))

    def test_data_checks_pass_for_the_fixture(self):
        checks = intake.data_checks(self.data)
        self.assertTrue(checks)
        self.assertTrue(all(c["ok"] for c in checks), [c for c in checks if not c["ok"]])

    def test_template_upload_is_accepted(self):
        loaded = intake.parse_upload("qa.csv", intake.UPLOAD_TEMPLATE_CSV.encode())
        self.assertEqual(loaded["source_kind"], "upload")
        self.assertTrue(loaded["records"])

    def test_invalid_uploads_are_rejected_with_reasons(self):
        bad_inputs = {
            "empty.csv": b"",
            "nocolumns.csv": b"foo,bar\n1,2\n",
            "notjson.json": b"{not json",
            "binary.csv": b"\x00\x01\x02\xff\xfe",
        }
        for name, raw in bad_inputs.items():
            with self.subTest(name=name):
                with self.assertRaises(intake.UploadError) as caught:
                    intake.parse_upload(name, raw)
                self.assertTrue(caught.exception.args)

    def test_oversized_upload_is_rejected(self):
        with self.assertRaises(intake.UploadError):
            intake.parse_upload("big.csv", b"a" * (intake.MAX_UPLOAD_BYTES + 1))

    def test_bad_result_value_rejects_the_whole_file(self):
        csv_text = (
            "record_id,section,agent,team_lead,call_date,behavior,result,comment\n"
            "CO-001,Compliance,Agent-01,TL-01,2026-09-01,Verify identity,maybe,Comment\n"
        )
        with self.assertRaises(intake.UploadError):
            intake.parse_upload("x.csv", csv_text.encode())


class ComplianceTests(unittest.TestCase):
    def test_repeat_failures_raise_the_risk_one_level(self):
        once = compliance.risk_for_failure("identity_verification", 1)
        twice = compliance.risk_for_failure("identity_verification", 2)
        self.assertEqual(once["level"], compliance.HIGH)
        self.assertEqual(twice["level"], compliance.CRITICAL)
        self.assertTrue(twice["reasons"])

    def test_risk_levels_are_not_legal_determinations(self):
        self.assertIn("not", compliance.RISK_DISCLAIMER.lower())
        labels = " ".join(compliance.STATUS_LABELS.values()).lower()
        self.assertNotIn("hipaa violation", labels)

    def test_flags_exist_only_for_failed_items(self):
        data = _data()
        flags = compliance.build_flags(data["records"])
        failed_ids = {r["record_id"] for r in data["records"] if r["result"] == "No"}
        self.assertTrue(set(flags) <= failed_ids)
        self.assertTrue(flags)

    def test_dismissal_needs_a_real_note(self):
        flag = next(iter(compliance.build_flags(_data()["records"]).values()))
        with self.assertRaises(WorkflowError):
            compliance.apply_action(flag, "dismiss", actor="Pat", role=SUPERVISOR, note="ok")

    def test_actions_respect_roles_and_do_not_mutate_the_input(self):
        flag = next(iter(compliance.build_flags(_data()["records"]).values()))
        before = copy.deepcopy(flag)
        with self.assertRaises(WorkflowError):
            compliance.apply_action(
                flag, "confirm_qa_failure", actor="Pat", role="Trainer", note=NOTE
            )
        updated = compliance.apply_action(
            flag, "confirm_qa_failure", actor="Pat", role=SUPERVISOR, note=NOTE
        )
        self.assertEqual(flag, before)
        self.assertEqual(updated["status"], compliance.QA_FAILURE_CONFIRMED)
        self.assertEqual(len(updated["history"]), 1)

    def test_risk_override_requires_reason_and_keeps_the_rule_level(self):
        flag = next(iter(compliance.build_flags(_data()["records"]).values()))
        with self.assertRaises(WorkflowError):
            compliance.override_risk(flag, compliance.LOW, actor="Pat", role=SUPERVISOR, note="")
        changed = compliance.override_risk(
            flag, compliance.LOW, actor="Pat", role=SUPERVISOR, note=NOTE
        )
        self.assertEqual(compliance.effective_risk(changed), compliance.LOW)
        self.assertEqual(changed["rule_risk"], flag["rule_risk"])


class ReviewAndOperationsTests(unittest.TestCase):
    def setUp(self):
        self.data = _data()
        self.diagnosis = _diagnosis(self.data)
        self.finding = self.diagnosis["findings"][0]
        self.store = operations.new_store(self.data["records"])

    def test_new_draft_starts_undecided(self):
        problems = review.validate(
            self.finding, review.new_draft(self.finding), actor="Pat", role=SUPERVISOR
        )
        self.assertTrue(any("decision" in p.lower() for p in problems))

    def test_valid_approval_has_no_problems(self):
        self.assertEqual(
            review.validate(self.finding, _approve_draft(self.finding), actor="Pat", role=SUPERVISOR),
            [],
        )

    def test_roles_without_permission_cannot_decide(self):
        problems = review.validate(
            self.finding, _approve_draft(self.finding), actor="Pat", role="Trainer"
        )
        self.assertTrue(problems)

    def test_rejection_requires_a_note_and_cannot_lead_to_coaching(self):
        draft = review.new_draft(self.finding)
        draft.update(decision="reject", control="not_supported", next_action="close", note="")
        self.assertTrue(
            any("note" in p.lower() for p in review.validate(self.finding, draft, actor="Pat", role=SUPERVISOR))
        )
        draft.update(note=NOTE, next_action="coaching")
        self.assertTrue(review.validate(self.finding, draft, actor="Pat", role=SUPERVISOR))

    def test_approval_cannot_leave_the_control_undetermined(self):
        draft = _approve_draft(self.finding)
        draft["control"] = "undetermined"
        self.assertTrue(review.validate(self.finding, draft, actor="Pat", role=SUPERVISOR))

    def test_decision_keeps_the_original_ai_text_and_audits_it(self):
        draft = _approve_draft(self.finding)
        draft["title"] = "Supervisor-corrected title"
        original_title = self.finding["title"]
        outcome = operations.record_decision(
            self.store, self.finding, draft, actor="Pat", role=SUPERVISOR
        )
        record = outcome["record"]
        self.assertEqual(record["ai_original"]["title"], original_title)
        self.assertEqual(record["final"]["title"], "Supervisor-corrected title")
        self.assertTrue(any(c["field"] == "title" for c in record["changes"]))
        self.assertEqual(self.store["decisions"][self.finding["id"]]["revision"], 1)
        self.assertTrue(audit.verify(self.store["audit"])["ok"])
        self.assertTrue(self.store["audit"])

    def test_revising_a_decision_adds_a_revision(self):
        operations.record_decision(
            self.store, self.finding, _approve_draft(self.finding), actor="Pat", role=SUPERVISOR
        )
        record = self.store["decisions"][self.finding["id"]]
        revised = review.draft_from_record(self.finding, record)
        revised["note"] = "Second look at the evidence; the decision stands after more review."
        operations.record_decision(self.store, self.finding, revised, actor="Pat", role=SUPERVISOR)
        self.assertEqual(self.store["decisions"][self.finding["id"]]["revision"], 2)

    def test_failed_validation_leaves_the_store_untouched(self):
        before = copy.deepcopy(self.store)
        draft = _approve_draft(self.finding)
        draft["control"] = "undetermined"
        with self.assertRaises(WorkflowError):
            operations.record_decision(self.store, self.finding, draft, actor="Pat", role=SUPERVISOR)
        self.assertEqual(self.store, before)

    def test_progress_and_approved_findings_follow_decisions(self):
        self.assertEqual(review.approved_findings(self.diagnosis, self.store["decisions"]), [])
        operations.record_decision(
            self.store, self.finding, _approve_draft(self.finding), actor="Pat", role=SUPERVISOR
        )
        approved = review.approved_findings(self.diagnosis, self.store["decisions"])
        self.assertEqual([f["id"] for f in approved], [self.finding["id"]])


class AuditTrailTests(unittest.TestCase):
    def _log(self):
        log = []
        for number in range(3):
            audit.append(
                log, actor="Pat", role=SUPERVISOR, action="test", subject_type="finding",
                subject_id=f"F{number}", details={"n": number},
            )
        return log

    def test_untouched_chain_verifies(self):
        self.assertEqual(audit.verify(self._log()), {"ok": True, "checked": 3, "first_invalid": None})

    def test_editing_an_entry_is_detected(self):
        log = self._log()
        log[1]["details"]["n"] = 99
        result = audit.verify(log)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_invalid"], 2)

    def test_removing_an_entry_is_detected(self):
        log = self._log()
        del log[0]
        self.assertFalse(audit.verify(log)["ok"])


class ReferralTests(unittest.TestCase):
    def setUp(self):
        self.employees = ["Agent-01", "Agent-02"]
        self.cases = ["F1"]
        self.form = {
            "employee": "Agent-01",
            "case_id": "F1",
            "action": "verbal_counseling",
            "justification": "The scored items and evaluator comments show a repeated missed step on calls.",
            "review_date": "2026-09-20",
            "approvals": [],
            "witness_name": "",
            "witness_acknowledged": False,
            "attested": True,
        }
        self.kwargs = dict(
            actor="Pat", role=SUPERVISOR, employees=self.employees, case_ids=self.cases,
            submitting=True, today=date(2026, 9, 21),
        )

    def test_valid_referral_is_created_as_a_simulation(self):
        record = referrals.create([], self.form, **self.kwargs)
        self.assertIn("simulat", repr(record).lower())

    def test_justification_is_required(self):
        self.form["justification"] = "bad"
        self.assertTrue(referrals.validate(self.form, **self.kwargs))

    def test_serious_actions_need_their_approvals(self):
        self.form["action"] = "termination_referral"
        problems = referrals.validate(self.form, **self.kwargs)
        self.assertTrue(any("approval" in p.lower() for p in problems))
        self.form["approvals"] = list(referrals.ACTIONS["termination_referral"].minimum_approvals)
        self.assertEqual(referrals.validate(self.form, **self.kwargs), [])

    def test_only_permitted_roles_can_write_referrals(self):
        kwargs = dict(self.kwargs, role="Trainer")
        self.assertTrue(referrals.validate(self.form, **kwargs))

    def test_future_review_dates_and_unknown_employees_are_rejected(self):
        self.form["review_date"] = "2030-01-01"
        self.form["employee"] = "Agent-99"
        self.assertGreaterEqual(len(referrals.validate(self.form, **self.kwargs)), 2)

    def test_submitting_requires_the_attestation(self):
        self.form["attested"] = False
        self.assertTrue(referrals.validate(self.form, **self.kwargs))
        self.assertEqual(referrals.validate(self.form, **dict(self.kwargs, submitting=False)), [])


class CoachingTests(unittest.TestCase):
    def setUp(self):
        data = _data()
        self.finding = _diagnosis(data)["findings"][0]
        training = sample_walkthrough.demo_training([self.finding], data, None, None)
        self.module = training["modules"][0]
        self.questions = coaching.quiz_questions(self.module)

    def test_quiz_has_gradable_questions(self):
        self.assertTrue(self.questions)
        self.assertTrue(any(q["correct_index"] is not None for q in self.questions))

    def test_quiz_grades_right_and_wrong_answers(self):
        correct = {q["id"]: q["correct_index"] for q in self.questions}
        passed = coaching.grade_quiz(self.questions, correct)
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["percent"], 100)
        wrong = {
            q["id"]: (q["correct_index"] + 1) % len(q["options"]) if q["correct_index"] is not None else 0
            for q in self.questions
        }
        failed = coaching.grade_quiz(self.questions, wrong)
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["score"], 0)

    def test_quiz_requires_every_question_to_be_answered(self):
        with self.assertRaises(WorkflowError):
            coaching.grade_quiz(self.questions, {})

    def test_quiz_rejects_out_of_range_answers(self):
        answers = {q["id"]: 0 for q in self.questions}
        answers[self.questions[0]["id"]] = 99
        with self.assertRaises(WorkflowError):
            coaching.grade_quiz(self.questions, answers)

    def test_question_without_an_answer_key_is_never_graded(self):
        module = {"knowledge_check": {"question": "Pick one", "options": ["a", "b"], "correct": "zzz"}}
        questions = coaching.quiz_questions(module)
        self.assertIsNone(questions[0]["correct_index"])
        with self.assertRaises(WorkflowError):
            coaching.grade_quiz(questions, {questions[0]["id"]: 0})

    def test_guided_practice_counts_ratings(self):
        result = coaching.guided_practice_result(["a", "b"], ["met", "not_met"])
        self.assertEqual((result["met"], result["total"], result["all_met"]), (1, 2, False))
        with self.assertRaises(WorkflowError):
            coaching.guided_practice_result(["a", "b"], ["met"])

    def test_follow_up_date_must_be_a_future_date(self):
        with self.assertRaises(WorkflowError):
            coaching.validate_follow_up_date("not a date")
        with self.assertRaises(WorkflowError):
            coaching.validate_follow_up_date("2020-01-01", today=date(2026, 1, 1))

    def test_components_always_include_targeted_coaching(self):
        self.assertIn(coaching.ALWAYS_INCLUDED, coaching.normalize_components([]))


class RoleplayTests(unittest.TestCase):
    def setUp(self):
        data = _data()
        finding = _diagnosis(data)["findings"][0]
        training = sample_walkthrough.demo_training([finding], data, None, None)
        self.module = training["modules"][0]

    def test_demonstration_roleplay_is_valid(self):
        nodes, problems = coaching.roleplay_nodes(self.module)
        self.assertEqual(problems, [])
        self.assertTrue(nodes)

    def test_different_choices_lead_to_different_places(self):
        nodes, _ = coaching.roleplay_nodes(self.module)
        state = coaching.start_roleplay(nodes)
        options = nodes[0]["options"]
        self.assertGreaterEqual(len(options), 2)
        results = {
            option["id"]: coaching.choose(nodes, state, option["id"]) for option in options
        }
        demonstrated = {r["path"][0]["demonstrated"] for r in results.values()}
        self.assertEqual(demonstrated, {True, False}, "both a correct and an incorrect response exist")
        reactions = {r["path"][0]["member_reaction"] for r in results.values()}
        self.assertGreater(len(reactions), 1, "the member reacts differently to different responses")

    def test_a_run_finishes_and_cannot_continue(self):
        nodes, _ = coaching.roleplay_nodes(self.module)
        state = coaching.start_roleplay(nodes)
        while not state["finished"]:
            node = next(n for n in nodes if n["id"] == state["current"])
            state = coaching.choose(nodes, state, node["options"][0]["id"])
        summary = coaching.roleplay_summary(state)
        self.assertTrue(summary["finished"])
        with self.assertRaises(WorkflowError):
            coaching.choose(nodes, state, "a")

    def test_unknown_response_is_refused(self):
        nodes, _ = coaching.roleplay_nodes(self.module)
        with self.assertRaises(WorkflowError):
            coaching.choose(nodes, coaching.start_roleplay(nodes), "does-not-exist")

    def test_a_package_without_decisions_has_no_roleplay(self):
        nodes, problems = coaching.roleplay_nodes({"roleplay_script": "A static script only."})
        self.assertEqual(nodes, [])
        self.assertTrue(problems)


class BusinessImpactTests(unittest.TestCase):
    GOOD = dict(
        cases_analyzed=200, cases_requiring_coaching=40, manual_minutes=45,
        assisted_minutes=18, hourly_rate=32.0, weeks_per_year=50, employees=25,
    )

    def test_formulas_use_the_inputs(self):
        result = business_impact.efficiency_estimate(**self.GOOD)
        self.assertAlmostEqual(result["saved_weekly_hours"], 40 * 27 / 60)
        self.assertAlmostEqual(result["saved_annual_hours"], 40 * 27 / 60 * 50)
        self.assertAlmostEqual(result["annual_capacity_value"], 40 * 27 / 60 * 50 * 32.0)
        self.assertTrue(result["formulas"])

    def test_invalid_inputs_are_named_and_refused(self):
        bad_cases = [
            dict(self.GOOD, cases_analyzed=-1),
            dict(self.GOOD, cases_requiring_coaching=500),
            dict(self.GOOD, manual_minutes=0),
            dict(self.GOOD, hourly_rate=-5),
            dict(self.GOOD, employees=0),
            dict(self.GOOD, weeks_per_year=60),
            dict(self.GOOD, hourly_rate="abc"),
            dict(self.GOOD, hourly_rate=float("nan")),
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                self.assertTrue(business_impact.validate_scenario(**case))
                with self.assertRaises(ValueError):
                    business_impact.efficiency_estimate(**case)

    def test_a_slower_assisted_process_is_reported_not_hidden(self):
        result = business_impact.efficiency_estimate(**dict(self.GOOD, assisted_minutes=60))
        self.assertTrue(result["assisted_is_slower"])
        self.assertLess(result["saved_weekly_hours"], 0)


class AnalyticsTests(unittest.TestCase):
    def test_overview_totals_match_the_queue(self):
        rows = intake.build_queue(_data())
        overview = analytics.qa_overview(rows)
        self.assertEqual(overview["total"], 48)
        self.assertEqual(overview["failed"], 11)
        self.assertEqual(overview["passed"], 37)

    def test_roles_are_simulated_permissions(self):
        self.assertTrue(can(SUPERVISOR, "decide_finding"))
        self.assertFalse(can("Trainer", "decide_finding"))
        self.assertFalse(is_meaningful_note("ok"))
        self.assertTrue(is_meaningful_note(NOTE))


if __name__ == "__main__":
    unittest.main()
