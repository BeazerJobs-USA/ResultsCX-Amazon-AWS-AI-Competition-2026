"""Offline unit tests for synthetic-case reference code only."""

import unittest

from examples.qa_data_validation_reference import validate_synthetic_case


class SyntheticCaseValidationTests(unittest.TestCase):
    def test_valid_case(self):
        record = validate_synthetic_case(
            {"case_id": "F2", "focus_area": "Documentation", "defect_rate_pct": 14}
        )
        self.assertEqual(record.case_id, "F2")
        self.assertEqual(record.defect_rate_pct, 14.0)

    def test_unexpected_field_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_synthetic_case(
                {"case_id": "F2", "focus_area": "Documentation", "defect_rate_pct": 14,
                 "member_id": "PRIVATE"}
            )

    def test_defect_rate_outside_range_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_synthetic_case(
                {"case_id": "F2", "focus_area": "Documentation", "defect_rate_pct": 140}
            )

    def test_missing_field_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_synthetic_case({"case_id": "F2", "defect_rate_pct": 14})


if __name__ == "__main__":
    unittest.main()

