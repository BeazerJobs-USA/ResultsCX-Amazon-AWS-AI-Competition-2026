"""Optional browser test: starts the real app and drives it in headless Chrome.

Skipped unless Playwright and a Chrome/Chromium binary are available. It uses the offline
demonstration source and synthetic data; no AWS call is made.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the environment
    sync_playwright = None

CHROME = next(
    (p for p in (shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser")) if p), None
)
NOTE = "Reviewed the evaluator comment and the scored item; I agree with this finding."
BAD_UPLOAD = b"agent,call_date,behavior,result\nAgent-01,not-a-date,Verify identity,maybe\n"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipIf(sync_playwright is None or CHROME is None, "Playwright or Chrome is not available")
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "HARNESS_"))}
        cls.workdir = tempfile.TemporaryDirectory()
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless=true",
             f"--server.port={cls.port}", "--browser.gatherUsageStats=false"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.url = f"http://127.0.0.1:{cls.port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(cls.url + "/_stcore/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            cls.proc.kill()
            raise RuntimeError("the Streamlit server did not start")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        cls.proc.wait(timeout=15)
        cls.workdir.cleanup()

    def setUp(self):
        self.errors = []
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 1000})
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.addCleanup(self.page.close)
        self.page.goto(self.url)
        self.page.wait_for_selector("text=Agent QA Review", timeout=30000)

    def open(self, workspace):
        """Open a workspace and wait until its heading is shown. A click made while the first
        run is still drawing can be dropped, so the click is repeated if the heading never
        appears."""
        heading = self.page.get_by_role("heading", name=workspace, exact=True)
        for _ in range(4):
            self.page.get_by_text(workspace, exact=True).first.click()
            try:
                heading.first.wait_for(timeout=4000)
                break
            except Exception:
                continue
        else:
            self.fail(f"the {workspace!r} workspace did not open")
        self.page.wait_for_timeout(800)

    def exceptions(self):
        return self.page.locator('[data-testid="stException"]').count()

    def test_every_workspace_loads_without_errors(self):
        for workspace in ("1 · Intake & Evidence", "2 · Diagnosis & Risk", "3 · Supervisor Review",
                          "4 · Coaching & Practice", "Compliance Review", "Administrative Referral",
                          "Audit Trail", "Dashboard"):
            with self.subTest(workspace=workspace):
                self.open(workspace)
                self.assertEqual(self.exceptions(), 0)
        self.assertEqual(self.errors, [])

    def test_review_decision_and_quiz_in_a_real_browser(self):
        page = self.page
        self.open("1 · Intake & Evidence")
        page.get_by_role("button", name="Load the offline demonstration analysis").click()
        page.wait_for_timeout(3500)
        page.get_by_role("button", name="Proceed").first.click()
        page.wait_for_timeout(2500)
        page.get_by_text("Approve the diagnosis", exact=True).click()
        page.wait_for_timeout(1200)
        page.get_by_text("Yes - the QA control was missed", exact=True).click()
        page.wait_for_timeout(1200)
        page.get_by_label("Supervisor note", exact=False).fill(NOTE)
        page.keyboard.press("Control+Enter")
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Record decision").click()
        page.wait_for_timeout(2500)
        self.assertGreater(page.get_by_text("Decision recorded").count(), 0)
        self.open("4 · Coaching & Practice")
        page.get_by_role("button", name="Generate").first.click()
        page.wait_for_timeout(4000)
        self.assertGreater(page.get_by_text("Coaching package ready").count(), 0)
        package = page.locator('[data-testid="stRadio"]').filter(has_text="Package section")
        package.get_by_text("Knowledge check", exact=True).click()
        page.wait_for_selector("text=Question 1", timeout=10000)
        self.assertEqual(self.exceptions(), 0)
        # Answer nothing and submit: the app must refuse to grade an unanswered quiz.
        page.get_by_role("button", name="Submit answers").click()
        page.wait_for_timeout(1500)
        self.assertEqual(self.exceptions(), 0)
        self.assertGreater(page.get_by_text("before submitting", exact=False).count(), 0)
        self.assertEqual(page.get_by_text("Not passed", exact=False).count(), 0)
        self.assertEqual(self.errors, [])

    def test_a_bad_upload_is_rejected_with_reasons(self):
        page = self.page
        self.open("1 · Intake & Evidence")
        page.get_by_text("Upload a QA file (JSON or CSV)", exact=True).click()
        page.wait_for_timeout(1500)
        page.locator('input[type="file"]').set_input_files(
            files=[{"name": "bad.csv", "mimeType": "text/csv", "buffer": BAD_UPLOAD}]
        )
        page.wait_for_timeout(2000)
        page.get_by_role("button", name="Validate and load this file").click()
        page.wait_for_timeout(2500)
        self.assertGreater(page.get_by_text("Nothing was loaded").count(), 0)
        self.assertEqual(self.exceptions(), 0)


if __name__ == "__main__":
    unittest.main()
