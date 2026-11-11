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


class ComplianceFlagTests(unittest.TestCase):
    """Flags come from workflow.compliance (rule-based, not legal determinations)."""

    def test_failed_identity_checks_are_flagged_and_raised_on_repeat(self):
        from workflow import compliance

        data = get_data.get_all_data(source_override="synthetic")
        flags = compliance.build_flags(data["records"])
        identity = [f for f in flags.values() if f["category"] == "identity_verification"]
        self.assertTrue(identity)
        self.assertTrue(all(f["employee"].startswith("Agent-") for f in identity))
        self.assertTrue(all(f["privacy_related"] for f in identity))
        repeated = [f for f in identity if f["repeat_count"] >= 2]
        self.assertTrue(all(f["rule_risk"] == compliance.CRITICAL for f in repeated))

    def test_passing_items_never_raise_flags(self):
        from workflow import compliance

        records = [
            {"record_id": "CO-1", "source": "CO", "section": "Compliance", "employee": "Agent-01",
             "team_lead": "TL-01", "call_date": "2026-09-01", "result": "Yes",
             "requirement": "Verify identity", "comment": ""},
            {"record_id": "CO-2", "source": "CO", "section": "Compliance", "employee": "Agent-01",
             "team_lead": "TL-01", "call_date": "2026-09-02", "result": "No",
             "requirement": "Read greeting", "comment": ""},
        ]
        flags = compliance.build_flags(records)
        self.assertNotIn("CO-1", flags)
        self.assertIn("CO-2", flags)


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
