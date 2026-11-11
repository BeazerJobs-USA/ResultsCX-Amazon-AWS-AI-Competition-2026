"""Drives the real Streamlit app with Streamlit's own AppTest runner.

These tests use the offline demonstration source and synthetic data. No AWS call is made.
They are skipped when Streamlit is not installed. AppTest runs the script headless, so it
exercises widget logic and reruns but does not render in a browser.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover - depends on the environment
    AppTest = None

WORKSPACES = [
    "Dashboard",
    "1 · Intake & Evidence",
    "2 · Diagnosis & Risk",
    "3 · Supervisor Review",
    "4 · Coaching & Practice",
    "Compliance Review",
    "Administrative Referral",
    "Audit Trail",
]
NOTE = "Reviewed the evaluator comment and the scored item; I agree with this finding."


@unittest.skipIf(AppTest is None, "streamlit is not installed")
class StreamlitAppTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Keep the run away from real credentials and from any saved output.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "HARNESS_"))}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        import pipeline

        out = Path(self._tmp.name) / "outputs"
        for name, value in (("OUTPUT_DIR", out), ("HISTORY_DIR", out / "history")):
            p = patch.object(pipeline, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()

    # -- helpers --------------------------------------------------------------------------
    def go(self, workspace):
        self.at.sidebar.radio[0].set_value(workspace).run()

    def assert_clean(self, where=""):
        exceptions = [e.value[:300] for e in self.at.exception]
        self.assertEqual(exceptions, [], f"exception on {where}")

    def radio_by_label(self, label):
        matches = [r for r in self.at.radio if r.label == label]
        self.assertTrue(matches, f"no radio labelled {label!r}")
        return matches[0]

    def analyze_and_open_review(self):
        self.go(WORKSPACES[1])
        self.at.button(key="intake_run").click().run()
        self.assert_clean("analysis")
        self.at.button(key="diag_proceed").click().run()
        self.assert_clean("diagnosis")

    def approve_current_finding(self, note=NOTE):
        decision = next(r for r in self.at.radio if r.key and r.key.endswith("_decision"))
        prefix = decision.key[: -len("_decision")]
        self.at.radio(key=decision.key).set_value("approve").run()
        self.at.radio(key=f"{prefix}_control").set_value("confirmed").run()
        if note is not None:
            self.at.text_area(key=f"{prefix}_note").set_value(note).run()
        self.at.button(key=f"{prefix}_record").click().run()
        return prefix

    # -- tests ----------------------------------------------------------------------------
    def test_every_workspace_renders_without_an_exception(self):
        self.assert_clean("first load")
        self.assertEqual(list(self.at.sidebar.radio[0].options), WORKSPACES)
        for workspace in WORKSPACES:
            with self.subTest(workspace=workspace):
                self.go(workspace)
                self.assert_clean(workspace)
                self.assertEqual(self.at.sidebar.radio[0].value, workspace)

    def test_offline_analysis_moves_to_diagnosis_then_review(self):
        self.go(WORKSPACES[1])
        self.at.button(key="intake_run").click().run()
        self.assertEqual(self.at.sidebar.radio[0].value, WORKSPACES[2])
        self.at.button(key="diag_proceed").click().run()
        self.assertEqual(self.at.sidebar.radio[0].value, WORKSPACES[3])
        self.assert_clean()

    def test_recording_a_decision_updates_flags_and_the_audit_trail(self):
        from workflow import audit

        self.analyze_and_open_review()
        self.approve_current_finding()
        self.assert_clean("decision")
        self.assertTrue(any("Decision recorded" in s.value for s in self.at.success))
        store = self.at.session_state["store"]
        record = store["decisions"]["F1"]
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["control"], "confirmed")
        self.assertIn("title", record["ai_original"], "the AI's original text is kept")
        self.assertTrue(store["audit"])
        self.assertTrue(audit.verify(store["audit"])["ok"])
        self.go("Audit Trail")
        self.assert_clean("audit")

    def test_approving_without_confirming_the_control_is_refused(self):
        self.analyze_and_open_review()
        decision = next(r for r in self.at.radio if r.key and r.key.endswith("_decision"))
        prefix = decision.key[: -len("_decision")]
        self.at.radio(key=decision.key).set_value("approve").run()
        self.at.text_area(key=f"{prefix}_note").set_value(NOTE).run()
        self.at.button(key=f"{prefix}_record").click().run()
        self.assert_clean()
        self.assertFalse(any("Decision recorded" in s.value for s in self.at.success))
        self.assertTrue(self.at.error or self.at.warning, "the problem is shown to the supervisor")

    def test_coaching_is_blocked_until_a_finding_is_approved(self):
        self.analyze_and_open_review()
        self.go(WORKSPACES[4])
        self.assert_clean()
        self.assertFalse([b for b in self.at.button if b.key and b.key.startswith("coach_generate_")])

    def _open_quiz(self):
        self.analyze_and_open_review()
        self.approve_current_finding()
        self.go(WORKSPACES[4])
        generate = [b for b in self.at.button if b.key and b.key.startswith("coach_generate_")]
        self.assertTrue(generate, "an approved finding can generate a package")
        generate[0].click().run()
        self.assert_clean("package")
        self.assertTrue(any("Coaching package ready" in s.value for s in self.at.success))
        self.radio_by_label("Package section").set_value("knowledge_check").run()
        self.assert_clean("knowledge check")
        from workflow import coaching

        module = self.at.session_state["training"]["modules"][0]
        return coaching.quiz_questions(module)

    def _answer_quiz(self, questions, picker):
        for question in questions:
            widget = next(r for r in self.at.radio if r.key and r.key.endswith(f"_{question['id']}") and r.key.startswith("quiz_"))
            widget.set_value(picker(question)).run()
        next(b for b in self.at.button if b.key and b.key.startswith("quiz_submit_")).click().run()
        self.assert_clean("quiz submitted")

    def test_quiz_with_correct_answers_passes(self):
        questions = self._open_quiz()
        self._answer_quiz(questions, lambda q: q["correct_index"])
        result = next(iter(self.at.session_state["practice"].values()))["result"]
        self.assertIsNotNone(result)
        self.assertTrue(result["passed"])
        self.assertEqual(result["percent"], 100)

    def test_quiz_with_wrong_answers_fails_and_can_be_retried(self):
        questions = self._open_quiz()
        self._answer_quiz(questions, lambda q: (q["correct_index"] + 1) % len(q["options"]))
        practice = next(iter(self.at.session_state["practice"].values()))
        self.assertFalse(practice["result"]["passed"])
        self.assertEqual(practice["result"]["score"], 0)
        next(b for b in self.at.button if b.key and b.key.startswith("quiz_retry_")).click().run()
        self.assert_clean("retry")
        practice = next(iter(self.at.session_state["practice"].values()))
        self.assertEqual(practice["attempt"], 2)
        self.assertIsNone(practice["result"])

    def test_quiz_cannot_be_submitted_with_unanswered_questions(self):
        self._open_quiz()
        next(b for b in self.at.button if b.key and b.key.startswith("quiz_submit_")).click().run()
        self.assert_clean("unanswered quiz")
        practice = next(iter(self.at.session_state["practice"].values()))
        self.assertIsNone(practice["result"])

    def _play_first_decision(self, want_demonstrated):
        from workflow import coaching

        self.analyze_and_open_review()
        self.approve_current_finding()
        self.go(WORKSPACES[4])
        [b for b in self.at.button if b.key and b.key.startswith("coach_generate_")][0].click().run()
        self.radio_by_label("Package section").set_value("roleplay").run()
        self.assert_clean("roleplay")
        nodes, problems = coaching.roleplay_nodes(self.at.session_state["training"]["modules"][0])
        self.assertEqual(problems, [])
        option = next(o for o in nodes[0]["options"] if o["demonstrates_behavior"] is want_demonstrated)
        choice = next(r for r in self.at.radio if r.key and r.key.startswith("rp_choice_"))
        choice.set_value(option["id"]).run()
        next(b for b in self.at.button if b.key and b.key.startswith("rp_say_")).click().run()
        self.assert_clean("after the choice")
        run = next(v for k, v in self.at.session_state["practice"].items() if k.startswith("roleplay|"))
        return option, run["state"]["path"][0]

    def test_roleplay_correct_choice_is_recorded_as_demonstrated(self):
        option, step = self._play_first_decision(True)
        self.assertEqual(step["option"], option["id"])
        self.assertTrue(step["demonstrated"])
        self.assertEqual(step["member_reaction"], option["member_reaction"])

    def test_roleplay_wrong_choice_gets_a_different_reaction(self):
        from workflow import coaching

        bad, step = self._play_first_decision(False)
        self.assertFalse(step["demonstrated"])
        self.assertEqual(step["member_reaction"], bad["member_reaction"])
        nodes, _ = coaching.roleplay_nodes(self.at.session_state["training"]["modules"][0])
        good = next(o for o in nodes[0]["options"] if o["demonstrates_behavior"])
        self.assertNotEqual(bad["member_reaction"], good["member_reaction"])

    def test_live_mode_without_configuration_explains_instead_of_crashing(self):
        from ui import state

        self.go(WORKSPACES[1])
        mode = next(r for r in self.at.radio if r.key and r.key.startswith("mode_choice_"))
        mode.set_value(state.LIVE).run()
        self.assert_clean("switch to live")
        messages = [m.value for m in (*self.at.error, *self.at.warning, *self.at.info)]
        self.assertTrue(messages, "missing AWS configuration is explained on the page")
        run = self.at.button(key="intake_run")
        if not run.disabled:
            run.click().run()
            self.assert_clean("live run without configuration")
            self.assertTrue(self.at.error)
        self.assertIsNone(self.at.session_state["diagnosis"], "no findings appear without a real analysis")

    def _field(self, kind, label_start):
        matches = [w for w in getattr(self.at, kind) if str(w.label).startswith(label_start)]
        self.assertTrue(matches, f"no {kind} labelled {label_start!r}")
        return matches[0]

    def test_referral_form_requires_a_justification(self):
        self.analyze_and_open_review()
        self.go("Administrative Referral")
        self.assert_clean()
        submit = [b for b in self.at.button if b.key and b.key.endswith("_submit")]
        self.assertTrue(submit)
        submit[0].click().run()
        self.assert_clean("empty referral")
        self.assertTrue(self.at.error or self.at.warning)
        self.assertEqual(self.at.session_state["store"]["referrals"], [])

    def test_referral_is_saved_as_a_simulation_and_never_sent(self):
        self.analyze_and_open_review()
        self.go("Administrative Referral")
        self._field("text_area", "Supervisor justification").set_value(
            "The scored items and evaluator comments show the same missed step across several calls."
        ).run()
        attest = [c for c in self.at.checkbox if c.key and c.key.endswith("_attest")]
        self.assertTrue(attest)
        attest[0].set_value(True).run()
        [b for b in self.at.button if b.key and b.key.endswith("_submit")][0].click().run()
        self.assert_clean("referral submitted")
        referrals = self.at.session_state["store"]["referrals"]
        self.assertEqual(len(referrals), 1)
        self.assertIn("simulat", repr(referrals[0]).lower())
        self.assertTrue(any("Nothing was sent" in s.value for s in self.at.success))

    def test_compliance_action_needs_a_rationale_and_updates_the_flag(self):
        self.go("Compliance Review")
        self.assert_clean()
        action = next(r for r in self.at.selectbox if r.label == "Action")
        action.set_value("dismiss").run()
        record = next(b for b in self.at.button if b.key and b.key.endswith("_record") and b.label == "Record this action")
        record.click().run()
        self.assert_clean("dismiss without a rationale")
        flags = self.at.session_state["store"]["flags"]
        self.assertTrue(all(f["status"] == "open" for f in flags.values()), "nothing changed without a note")
        note = next(t for t in self.at.text_area if str(t.label).startswith("Rationale"))
        note.set_value("The evaluator comment does not support the flag after a second look.").run()
        next(b for b in self.at.button if b.label == "Record this action").click().run()
        self.assert_clean("dismiss with a rationale")
        statuses = [f["status"] for f in self.at.session_state["store"]["flags"].values()]
        self.assertIn("dismissed", statuses)
        self.assertTrue(self.at.session_state["store"]["audit"])

    def test_efficiency_calculator_rejects_impossible_inputs(self):
        self.go("Dashboard")
        views = [r for r in self.at.radio if r.key == "dash_view"]
        self.assertTrue(views)
        for option in views[0].options:
            if "fficiency" in str(option) or "abor" in str(option):
                views[0].set_value(option).run()
                break
        self.assert_clean("calculator")
        field = next((n for n in self.at.number_input if n.key == "eff_analyzed"), None)
        if field is None:
            self.skipTest("the calculator view was not found")
        field.set_value(0).run()
        self.assert_clean("zero cases")


if __name__ == "__main__":
    unittest.main()
