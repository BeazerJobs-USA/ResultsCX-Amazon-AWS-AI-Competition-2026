"""
Control-flow test for app.py — no browser, no AWS.

A tiny fake ``streamlit`` module executes app.py top-to-bottom the way Streamlit does (one run per
user interaction), with scripted clicks and a fake Bedrock session that returns TEST FIXTURES
(clearly labelled - these are not real model outputs).

It proves the competition-critical flow:
    data loads -> "Run AI diagnosis" makes exactly ONE model call
    -> the supervisor's form submit records the approval AND immediately generates training
       (no second prompt is typed anywhere)
    -> Stage 4 renders the modules and the exports
    -> Replay mode reproduces the saved run with ZERO model calls.

Run from the repo folder:   python -m unittest tests.test_app_flow -v
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import get_data  # noqa: E402
import harness_client  # noqa: E402
import pipeline  # noqa: E402


# ======================================================================================
# A minimal fake Streamlit
# ======================================================================================
class Rerun(Exception):
    pass


class Stop(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as exc:
            raise AttributeError(k) from exc

    def __setattr__(self, k, v):
        self[k] = v


class Element:
    """Any container/column/tab/expander: callable, usable with 'with', delegates st.* calls."""

    def __init__(self, st):
        self._st = st

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(self._st, name)


class Status(Element):
    def update(self, **kw):
        self._st.log.append(("status.update", kw.get("label", "")))


class FakeStreamlit(types.ModuleType):
    def __init__(self, clicks=None, values=None):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self.clicks = dict(clicks or {})  # button label -> True
        self.values = dict(values or {})  # widget key (or label) -> scripted value
        self.log = []  # (kind, text) of everything "drawn"
        self.downloads = []
        self.sidebar = Element(self)
        self.column_config = types.SimpleNamespace(ProgressColumn=lambda *a, **k: None)

    # ---- layout ----
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [Element(self) for _ in range(n)]

    def tabs(self, labels, **kw):
        return [Element(self) for _ in labels]

    def container(self, **kw):
        return Element(self)

    def expander(self, label, **kw):
        self.log.append(("expander", label))
        return Element(self)

    def form(self, key, **kw):
        return Element(self)

    def status(self, label, **kw):
        self.log.append(("status", label))
        return Status(self)

    def empty(self):
        return Element(self)

    # ---- text / display ----
    def _draw(self, kind):
        def f(body="", *a, **kw):
            self.log.append((kind, str(body)))

        return f

    def __getattr__(self, name):
        if name in (
            "title",
            "header",
            "subheader",
            "caption",
            "markdown",
            "write",
            "success",
            "info",
            "warning",
            "error",
            "divider",
            "set_page_config",
            "dataframe",
            "table",
            "metric",
        ):
            return self._draw(name)
        raise AttributeError(name)

    # ---- widgets ----
    def _scripted(self, key, label, default):
        if key is not None and key in self.values:
            return self.values[key]
        return self.values.get(label, default)

    def text_input(self, label, value="", key=None, **kw):
        return self._scripted(key, label, value)

    def text_area(self, label, value="", key=None, **kw):
        return self._scripted(key, label, value)

    def checkbox(self, label, value=False, key=None, **kw):
        return self._scripted(key, label, value)

    def selectbox(self, label, options, index=0, key=None, **kw):
        options = list(options)
        return self._scripted(key, label, options[index] if options else None)

    def radio(self, label, options, index=0, key=None, **kw):
        return self._scripted(key, label, list(options)[index])

    def slider(self, label, min_value=None, max_value=None, value=None, key=None, **kw):
        return self._scripted(key, label, value)

    def number_input(self, label, min_value=None, max_value=None, value=None, key=None, **kw):
        return self._scripted(key, label, value)

    def button(self, label, key=None, **kw):
        self.log.append(("button", label))
        return bool(self.clicks.pop(label, False))

    form_submit_button = button

    def download_button(self, label, data, file_name=None, **kw):
        self.downloads.append((label, file_name, data))
        return False

    # ---- control flow ----
    def rerun(self):
        raise Rerun()

    def stop(self):
        raise Stop()


# ======================================================================================
# Fake Bedrock session returning TEST fixtures built from the real (masked) data
# ======================================================================================
class FakeSession:
    prompts = []

    def __init__(self, tools=None, **kw):
        self.session_id = "test-session-000000000000000000000000"
        self.calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.tools = tools or {}

    def ask(self, user_text, system_prompt=None, on_text=None):
        FakeSession.prompts.append(user_text)
        self.calls += 1
        if user_text.startswith("STAGE 2"):
            text = json.dumps(diagnosis_fixture())
        elif user_text.startswith("STAGE 4"):
            fid = user_text.split("id: ", 1)[1].split()[0]
            text = json.dumps(module_fixture(fid))
        else:
            raise AssertionError("unexpected prompt: " + user_text[:60])
        if on_text:
            on_text(text)
        turn = harness_client.HarnessTurn(
            text=text, stop_reason="end_turn", input_tokens=10, output_tokens=5
        )
        self.total_input_tokens += 10
        self.total_output_tokens += 5
        return text, turn


_DATA = None


def data():
    global _DATA
    if _DATA is None:
        _DATA = get_data.get_all_data()
    return _DATA


def diagnosis_fixture():
    b1, b2 = data()["behaviors"][0], data()["behaviors"][1]

    def finding(i, b, rec_type):
        ev = [
            {"record_id": e["record_id"], "quote": e["comment"][:25]}
            for e in b["evidence"][:2]
        ]
        return {
            "id": f"F{i}",
            "behavior_id": b["id"],
            "title": f"TEST FIXTURE finding for {b['id']}",
            "observed": "TEST observed",
            "root_cause_type": rec_type,
            "root_cause": "TEST root cause",
            "pattern": "recurring",
            "evidence": ev,
            "curriculum_coverage": "Partial",
            "curriculum_note": "TEST",
            "training_recommended": True,
            "recommended_intervention": "simulation",
            "confidence": "medium",
            "confidence_note": "",
        }

    return {
        "summary": "TEST FIXTURE summary",
        "findings": [finding(1, b1, "Skill Gap"), finding(2, b2, "Knowledge Gap")],
        "not_a_training_problem": [],
        "open_questions": ["TEST question"],
    }


def module_fixture(fid):
    return {
        "finding_id": fid,
        "module_title": f"TEST module for {fid}",
        "learning_objective": "TEST objective",
        "why_this_fixes_the_gap": "TEST",
        "delivery": "simulation",
        "duration_minutes": 30,
        "instruction": ["step 1", "step 2"],
        "existing_material_to_reuse": [],
        "knowledge_check": {
            "question": "Q?",
            "options": ["A) x", "B) y"],
            "correct": "A",
            "feedback": "f",
        },
        "hands_on_exercise": {
            "title": "ex",
            "trainee_instructions": "t",
            "facilitator_instructions": "f",
            "success_criteria": ["s1"],
        },
        "simulation_scenario": {
            "scenario_id": "S-1",
            "call_driver": "cd",
            "member_persona": "p",
            "member_emotion": "e",
            "scenario_context": "c",
            "behavioral_target": "TEST target",
            "expected_agent_response": "r",
            "scoring_criteria": ["c1", "c2"],
        },
        "roleplay_script": [
            {"speaker": "MEMBER", "line": "m1"},
            {"speaker": "AGENT", "line": "a1", "coach_note": "n"},
            {"speaker": "MEMBER", "line": "m2"},
            {"speaker": "AGENT", "line": "a2"},
            {
                "speaker": "BRANCH",
                "if_agent_demonstrates_behavior": "ok",
                "if_agent_does_not": "bad",
            },
        ],
    }


# ======================================================================================
# Runner: executes app.py once, like one Streamlit script run
# ======================================================================================
APP_SRC = (REPO / "app.py").read_text(encoding="utf-8")


def run_app(state, clicks=None, values=None):
    selected = {"Model access": "Live — call Bedrock"}
    selected.update(values or {})
    st = FakeStreamlit(clicks, selected)
    st.session_state = state
    original_streamlit = sys.modules.get("streamlit")
    sys.modules["streamlit"] = st
    real_session = harness_client.HarnessSession
    harness_client.HarnessSession = FakeSession
    outcome = "finished"
    try:
        exec(compile(APP_SRC, "app.py", "exec"), {"__name__": "app_under_test"})
    except Rerun:
        outcome = "rerun"
    except Stop:
        outcome = "stop"
    finally:
        harness_client.HarnessSession = real_session
        if original_streamlit is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = original_streamlit
    return st, outcome


def drawn(st, kind=None):
    return "\n".join(text for k, text in st.log if kind is None or k == kind)


class AppFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os

        cls.tmp = tempfile.TemporaryDirectory()
        cls._out, cls._hist = pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR
        pipeline.OUTPUT_DIR = Path(cls.tmp.name)
        pipeline.HISTORY_DIR = Path(cls.tmp.name) / "history"
        # The fake session never uses this ARN, but app.py refuses to start Live mode without a
        # valid-looking one. Force it (not setdefault): a real .env on a teammate's machine may
        # already have set HARNESS_ARN to an unfilled placeholder, which would otherwise leak in here.
        cls._old_arn = os.environ.get("HARNESS_ARN")
        os.environ["HARNESS_ARN"] = (
            "arn:aws:bedrock-agentcore:us-east-1:123456789012:harness/agent_harness-testtest01"
        )

    @classmethod
    def tearDownClass(cls):
        import os

        pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR = cls._out, cls._hist
        cls.tmp.cleanup()
        if cls._old_arn is None:
            os.environ.pop("HARNESS_ARN", None)
        else:
            os.environ["HARNESS_ARN"] = cls._old_arn

    def test_full_flow_live_then_replay(self):
        FakeSession.prompts = []
        state = SessionState()

        # ---- run 1: first paint, Stage 1 only ----
        st, outcome = run_app(state)
        self.assertEqual(outcome, "finished")
        self.assertIsNotNone(state.data)
        self.assertIsNone(state.diagnosis)
        self.assertIn("Run AI diagnosis", drawn(st, "button"))
        self.assertIn("Defect rates by QA behavior", drawn(st, "subheader"))

        # ---- run 2: click "Run AI diagnosis" -> exactly one model call, then rerun ----
        st, outcome = run_app(state, clicks={"▶ Run AI diagnosis (Stage 2)": True})
        self.assertEqual(outcome, "rerun")
        self.assertIsNotNone(state.diagnosis)
        self.assertEqual(len(FakeSession.prompts), 1)
        self.assertEqual(state.session.calls, 1)
        f1 = state.diagnosis["findings"][0]
        self.assertTrue(f1["metrics_verified"])
        self.assertEqual(
            f1["evidence_verified"], f1["evidence_total"]
        )  # fixture cites real record IDs
        self.assertEqual(
            f1["defect_rate_pct"], data()["behaviors"][0]["defect_rate_pct"]
        )  # numbers from Python

        # ---- run 3: Stage 2 + the supervisor form are on screen ----
        st, outcome = run_app(state)
        self.assertEqual(outcome, "finished")
        self.assertIn("APPROVE DIAGNOSIS", drawn(st, "button"))
        self.assertIn("2 · AI diagnosis", drawn(st, "header"))
        self.assertIn("3 · Supervisor review", drawn(st, "header"))
        self.assertIn("IMMEDIATE REVIEW NEEDED", drawn(st, "error"))
        self.assertIn("Interactive business impact", drawn(st, "expander"))
        self.assertIsNone(state.training)

        # ---- run 4: supervisor edits F1, excludes F2, approves -> training generated automatically ----
        st, outcome = run_app(
            state,
            clicks={"✅ APPROVE DIAGNOSIS — generate training automatically": True},
            values={
                "root_F1": "SUPERVISOR EDITED ROOT CAUSE",
                "rct_F1": "Process Gap",
                "inc_F2": False,
                "Reviewer name (recorded with the approval)": "George",
            },
        )
        self.assertEqual(outcome, "rerun")
        self.assertIsNotNone(state.training)
        self.assertEqual(
            len(FakeSession.prompts),
            2,
            "one diagnosis call + one training call for the single approved finding",
        )
        self.assertTrue(
            all(p.startswith("STAGE") for p in FakeSession.prompts),
            "no free-form/user prompt was ever sent",
        )
        self.assertEqual([m["finding_id"] for m in state.training["modules"]], ["F1"])
        self.assertEqual(state.training["errors"], [])
        # the edit reached the training prompt (this is what 'no re-entering context' means)
        self.assertIn("SUPERVISOR EDITED ROOT CAUSE", FakeSession.prompts[1])
        self.assertIn("Process Gap", FakeSession.prompts[1])
        ap = state.approval
        self.assertEqual(ap["approved_by"], "George")
        self.assertEqual(ap["approved_findings"], ["F1"])
        fields = {(c["finding"], c["field"]) for c in ap["changes"]}
        self.assertEqual(
            fields, {("F1", "root_cause"), ("F1", "root_cause_type"), ("F2", "include")}
        )
        self.assertTrue(state.training["alignment"][0]["finding_exists"])

        # ---- run 5: Stage 4 on screen with exports ----
        st, outcome = run_app(state)
        self.assertEqual(outcome, "finished")
        self.assertIn("4 · Training & hands-on practice", drawn(st, "header"))
        self.assertIn("TEST objective", drawn(st, "markdown"))
        self.assertIn("BRANCH", drawn(st, "warning"))
        self.assertIn("Reviewed by **George**", drawn(st, "success"))
        labels = [d[0] for d in st.downloads]
        self.assertEqual(len(labels), 2)
        md = next(d[2] for d in st.downloads if d[1].endswith(".md"))
        self.assertIn("Learning objective", md)
        self.assertIn("Role-play script", md)
        # outputs were saved for replay
        for name in ("diagnosis", "approval", "training"):
            self.assertTrue(
                (pipeline.OUTPUT_DIR / f"latest_{name}.json").exists(), name
            )

        # ---- replay mode: same flow, zero model calls ----
        FakeSession.prompts = []
        state = SessionState()
        replay = {"Model access": "Replay — last saved run (no AWS)"}
        st, outcome = run_app(state, values=replay)
        self.assertIn("Load saved diagnosis (replay)", drawn(st, "button"))
        st, outcome = run_app(
            state, clicks={"▶ Load saved diagnosis (replay)": True}, values=replay
        )
        self.assertEqual(outcome, "rerun")
        self.assertEqual(state.diagnosis["summary"], "TEST FIXTURE summary")
        self.assertIsNotNone(state.training)
        self.assertEqual(state.approval["approved_by"], "George")
        self.assertEqual(FakeSession.prompts, [], "replay must not call the model")
        self.assertEqual([m["finding_id"] for m in state.training["modules"]], ["F1"])
        st, outcome = run_app(state, values=replay)
        self.assertIn("REPLAY", drawn(st, "info"))
        self.assertNotIn("APPROVE DIAGNOSIS", drawn(st, "button"))

    def test_missing_config_is_explained_not_crashed(self):
        import os

        old = os.environ.pop("HARNESS_ARN", None)
        try:
            state = SessionState()
            st, outcome = run_app(state, clicks={"▶ Run AI diagnosis (Stage 2)": True})
            self.assertEqual(outcome, "rerun")
            self.assertIn("HARNESS_ARN", state.error)
            self.assertIsNone(state.diagnosis)
        finally:
            if old is not None:
                os.environ["HARNESS_ARN"] = old

    def test_no_saved_run_replay_is_explained(self):
        with tempfile.TemporaryDirectory() as empty:
            out, hist = pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR
            pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR = (
                Path(empty),
                Path(empty) / "history",
            )
            try:
                state = SessionState()
                st, outcome = run_app(
                    state,
                    clicks={"▶ Load saved diagnosis (replay)": True},
                    values={"Model access": "Replay — last saved run (no AWS)"},
                )
                self.assertEqual(outcome, "rerun")
                self.assertIn("No saved run", state.error)
            finally:
                pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR = out, hist

    def test_synthetic_walkthrough_uses_no_model_calls(self):
        FakeSession.prompts = []
        state = SessionState()
        mode = {"Model access": "Synthetic walkthrough — no AWS"}
        st, outcome = run_app(state, values=mode)
        self.assertEqual(outcome, "finished")
        self.assertIn("Load synthetic walkthrough", drawn(st, "button"))
        st, outcome = run_app(
            state, clicks={"▶ Load synthetic walkthrough": True}, values=mode
        )
        self.assertEqual(outcome, "rerun")
        self.assertEqual(
            state.diagnosis["_meta"]["source"], "handwritten synthetic walkthrough"
        )
        st, outcome = run_app(
            state,
            clicks={"✅ APPROVE DIAGNOSIS — generate training automatically": True},
            values=mode,
        )
        self.assertEqual(outcome, "rerun")
        self.assertTrue(state.training["modules"])
        self.assertEqual(FakeSession.prompts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

