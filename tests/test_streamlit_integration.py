"""Exercise the actual Streamlit script without a browser or AWS account."""

import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

import pipeline


class PublicWalkthroughIntegrationTests(unittest.TestCase):
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
            finally:
                pipeline.OUTPUT_DIR, pipeline.HISTORY_DIR = old_output, old_history
