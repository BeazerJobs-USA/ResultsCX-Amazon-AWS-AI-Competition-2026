"""New controls are tested offline: no AWS credentials or customer data."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import business_impact  # noqa: E402
import get_data  # noqa: E402
import pipeline  # noqa: E402
import security_controls  # noqa: E402
from harness_client import HarnessError  # noqa: E402
import sample_walkthrough  # noqa: E402


class PrivacyCheckpointTests(unittest.TestCase):
    def test_common_identifiers_and_known_names_are_masked(self):
        message = (
            "Member name: Maria Gomez, contact maria@example.org or 404-555-0199, "
            "SSN 123-45-6789, account ID: AB1234567, date 2026-09-18. "
            "Reviewer Jane Doe."
        )
        cleaned, count = security_controls.sanitize_text(message, ["Jane Doe"])
        for forbidden in (
            "Maria Gomez", "maria@example.org", "404-555-0199",
            "123-45-6789", "AB1234567", "2026-09-18", "Jane Doe",
        ):
            self.assertNotIn(forbidden, cleaned)
        self.assertIn("[EMAIL]", cleaned)
        self.assertIn("[PERSON]", cleaned)
        self.assertGreaterEqual(count, 6)

    def test_nested_payload_is_a_copy_and_preserves_metrics(self):
        raw = {
            "failed": 3,
            "evidence": [{"record_id": "CO-001", "comment": "Call patient 404-555-0123"}],
            "agent": "Agent-01",
            "failed_behaviors": {"Account ID: XY123456": 2},
        }
        original = copy.deepcopy(raw)
        cleaned, count = security_controls.sanitize_payload(raw)
        self.assertEqual(raw, original)  # original evidence remains intact for supervisor
        self.assertEqual(cleaned["failed"], 3)
        self.assertEqual(cleaned["agent"], "Agent-01")
        self.assertEqual(cleaned["evidence"][0]["record_id"], "CO-001")
        self.assertNotIn("404-555-0123", str(cleaned))
        self.assertNotIn("XY123456", str(cleaned))
        self.assertGreaterEqual(count, 2)

    def test_bedrock_input_and_tool_response_use_same_safe_boundary(self):
        data = get_data.get_all_data(source_override="synthetic")
        data["behaviors"][0]["evidence"][0]["comment"] = "john@example.com"
        data["agent_map"]["Agent-01"] = "John Smith"
        payload = pipeline.build_safe_model_payload(data)
        self.assertNotIn("john@example.com", str(payload))
        self.assertNotIn("John Smith", str(payload))
        self.assertIn("[EMAIL]", str(payload))
        self.assertNotIn("agent_map", payload)
        prompt = pipeline.sanitize_model_prompt(
            "STAGE 4 note from John Smith: reach me at 404-555-0199", data
        )
        self.assertNotIn("John Smith", prompt)
        self.assertNotIn("404-555-0199", prompt)


class ComplianceAlertTests(unittest.TestCase):
    def test_failed_identity_checks_are_flagged_from_qa_scores(self):
        data = get_data.get_all_data(source_override="synthetic")
        flags = security_controls.critical_compliance_alerts(data)
        self.assertEqual({f["record_id"] for f in flags}, {"CO-001", "CO-003"})
        self.assertTrue(all(f["agent"].startswith("Agent-") for f in flags))

    def test_noncompliance_and_passing_items_do_not_raise_alert(self):
        records = [
            {"record_id": "BP-1", "source": "BP", "result": "No", "behavior": "Verify identity"},
            {"record_id": "CO-1", "source": "CO", "result": "Yes", "behavior": "Verify identity"},
            {"record_id": "CO-2", "source": "CO", "result": "No", "behavior": "Read greeting"},
        ]
        # A failed CO row still merits review even without privacy keywords.
        flags = security_controls.critical_compliance_alerts({"records": records})
        self.assertEqual([f["record_id"] for f in flags], ["CO-2"])
        self.assertEqual(flags[0]["priority"], "Compliance review")

    def test_compliance_identity_failure_gets_urgent_priority(self):
        data = get_data.get_all_data(source_override="synthetic")
        flags = security_controls.critical_compliance_alerts(data)
        self.assertTrue(all(f["priority"].startswith("Urgent") for f in flags))


class UnexpectedModelOutputTests(unittest.TestCase):
    def test_sanitized_evidence_quote_can_still_verify(self):
        data = get_data.get_all_data(source_override="synthetic")
        behavior = data["behaviors"][0]
        evidence = behavior["evidence"][0]
        row = next(r for r in data["records"] if r["record_id"] == evidence["record_id"])
        row["comment"] = "Member contact: jane@example.org"
        diagnosis = {"findings": [{
            "behavior_id": behavior["id"],
            "evidence": [{"record_id": row["record_id"], "quote": "Member contact: [EMAIL]"}],
        }]}
        validated = pipeline.validate_diagnosis(diagnosis, data)
        self.assertEqual(validated["findings"][0]["evidence_verified"], 1)

    def test_malformed_finding_is_reported_as_pipeline_error(self):
        data = get_data.get_all_data(source_override="synthetic")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_diagnosis({"findings": [None]}, data)

    def test_failed_live_module_is_reported_without_crashing_other_stages(self):
        class FailedSession:
            session_id = "test-session"

            def ask(self, *_args, **_kwargs):
                raise HarnessError("Model response timed out")

        data = get_data.get_all_data(source_override="synthetic")
        approved = pipeline.build_approved_findings(
            sample_walkthrough.demo_diagnosis(data), {}
        )[:1]
        with patch.object(pipeline, "save_json", return_value=None):
            result = pipeline.run_training(FailedSession(), approved, data)
        self.assertEqual(result["modules"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("timed out", result["errors"][0]["error"])


class BusinessCalculatorTests(unittest.TestCase):
    def test_example_hours_and_labor_capacity(self):
        impact = business_impact.calculate_business_impact(40, 30, 18, 45, 50)
        self.assertAlmostEqual(impact["manual_weekly_hours"], 20)
        self.assertAlmostEqual(impact["assisted_weekly_hours"], 12)
        self.assertAlmostEqual(impact["saved_weekly_hours"], 8)
        self.assertAlmostEqual(impact["saved_annual_hours"], 400)
        self.assertAlmostEqual(impact["annual_capacity_value"], 18000)
        self.assertAlmostEqual(impact["time_reduction_pct"], 40)

    def test_no_cases_means_no_savings(self):
        impact = business_impact.calculate_business_impact(0, 30, 18, 45)
        self.assertEqual(impact["time_reduction_pct"], 0)
        self.assertEqual(impact["annual_capacity_value"], 0)

    def test_ai_workflow_can_take_more_time(self):
        impact = business_impact.calculate_business_impact(10, 15, 25, 40, 50)
        self.assertLess(impact["saved_weekly_hours"], 0)
        self.assertLess(impact["annual_capacity_value"], 0)

    def test_negative_input_is_rejected(self):
        with self.assertRaises(ValueError):
            business_impact.calculate_business_impact(10, 30, -1, 40)


if __name__ == "__main__":
    unittest.main()
