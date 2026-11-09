"""Exercise the actual Streamlit script without a browser or AWS account."""

import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

import pipeline


class PublicWalkthroughIntegrationTests(unittest.TestCase):
    def test_business_inputs_recalculate_without_model_calls(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-3].value, "8.0")
        self.assertEqual(app.metric[-1].value, "$18,000")

        app.number_input[0].set_value(10).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-3].value, "2.0")
        self.assertEqual(app.metric[-1].value, "$4,500")

        # If human review takes longer than preparing everything manually,
        # the model should show a cost instead of inventing positive savings.
        app.number_input[2].set_value(50).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-3].value, "-3.3")

    def test_live_button_without_config_shows_error_not_traceback(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"HARNESS_ARN": ""}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            app.run(timeout=30)
            app.radio[0].set_value("Live — call Bedrock").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            app.button[0].click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("Stage 2 failed" in e.value for e in app.error))

    def test_unselected_training_recommendations_do_not_crash(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.run(timeout=30)
        app.button[0].click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        for checkbox in app.checkbox:
            if checkbox.label == "Include in training":
                checkbox.set_value(False)
        # Widgets inside st.form are submitted as one batch, not on each edit.
        app.button[0].click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertIsNone(app.session_state.training)
        self.assertTrue(any("No finding is ticked" in w.value for w in app.warning))

    def test_synthetic_ui_progresses_to_training(self):
        with tempfile.TemporaryDirectory() as temp:
            old_output, old_history = pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR
            pipeline.OUTPUT_DIR = Path(temp)
            pipeline.HISTORY_DIR = Path(temp) / "history"
            try:
                app = AppTest.from_file(
                    str(Path(__file__).resolve().parents[1] / "app.py")
                )
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.title[0].value, "Agent QA Review")

                app.button[0].click().run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(
                    app.session_state.diagnosis["_meta"]["source"],
                    "handwritten synthetic walkthrough",
                )

                app.button[0].click().run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.session_state.training["modules"]), 2)
                self.assertEqual(
                    app.session_state.training["_meta"]["source"],
                    "handwritten synthetic walkthrough",
                )
                self.assertTrue(
                    any("IMMEDIATE COMPLIANCE REVIEW NEEDED" in e.value for e in app.error)
                )

                reset = next(b for b in app.button if b.label == "Start over")
                reset.click().run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertIsNone(app.session_state.diagnosis)
                self.assertIsNone(app.session_state.training)
            finally:
                pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR = old_output, old_history
