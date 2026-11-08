import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from src.recommendations.dimming import load_operator_settings, recommend


class DimmingTest(unittest.TestCase):
    def setUp(self):
        self.row = {
            "bin": "1000001", "in_program": True, "risk_priority": "high",
            "night_date": date(2021, 11, 6),
            "civil_dusk_utc": datetime(2021, 11, 6, 22, 15, tzinfo=UTC),
            "civil_dawn_utc": datetime(2021, 11, 7, 11, tzinfo=UTC),
            "eligible_zones": "facade|decorative",
            "approved_reductions_pct": "100|25|0|50",
            "scheduled_eligible_light_output": "80", "fixed_light_output": "20",
            "target_artificial_light_output": "65", "light_output_unit": "lm",
        }

    def test_smallest_approved_reduction_meets_cap_with_fixed_output(self):
        result = recommend(self.row)
        self.assertEqual(result["recommendation_status"], "scheduled")
        self.assertEqual(result["reduction_pct"], 50)
        self.assertEqual(result["scheduled_artificial_light_output"], 100)
        self.assertEqual(result["projected_artificial_light_output"], 60)
        self.assertEqual(result["eligible_zones"], ["facade", "decorative"])
        self.assertEqual([option["reduction_pct"] for option in result["approved_light_scenarios"]], [0, 25, 50, 100])
        self.assertEqual(result["start_local"], "6:15 PM EDT")
        self.assertEqual(result["end_local"], "6:00 AM EST")

    def test_only_eligible_zones_can_be_switched_off(self):
        result = recommend({**self.row, "target_artificial_light_output": 20})
        self.assertEqual(result["tier"], "off")
        self.assertEqual(result["reduction_pct"], 100)
        self.assertEqual(result["projected_artificial_light_output"], 20)

    def test_infeasible_cap_routes_to_review(self):
        result = recommend({**self.row, "target_artificial_light_output": 19})
        self.assertEqual(result["recommendation_status"], "cap_unmet")
        self.assertIsNone(result["reduction_pct"])
        self.assertIsNone(result["start_utc"])

    def test_zero_is_a_valid_setting_and_output(self):
        result = recommend({**self.row, "scheduled_eligible_light_output": 0, "fixed_light_output": 0,
                            "target_artificial_light_output": 0, "approved_reductions_pct": "0",
                            "civil_dusk_utc": None})
        self.assertEqual(result["recommendation_status"], "cap_met")
        self.assertEqual(result["reduction_pct"], 0)
        self.assertEqual(result["projected_artificial_light_output"], 0)

    def test_satellite_reading_cannot_replace_missing_operator_data(self):
        result = recommend({**self.row, "scheduled_eligible_light_output": "",
                            "prior_neighborhood_radiance": 85})
        self.assertEqual(result["recommendation_status"], "missing_operator_settings")
        self.assertIsNone(result["reduction_pct"])

    def test_requires_valid_risk_and_twilight_before_scheduling(self):
        for overrides, status in (
            ({"risk_priority": "unavailable"}, "missing_risk"),
            ({"civil_dawn_utc": "2021-11-07T11:00:00"}, "missing_twilight"),
            ({"civil_dawn_utc": "2021-11-08T11:00:00Z"}, "missing_twilight"),
            ({"in_program": False}, "not_in_program"),
            ({"approved_reductions_pct": "50|101"}, "missing_operator_settings"),
        ):
            with self.subTest(overrides=overrides):
                result = recommend({**self.row, **overrides})
                self.assertEqual(result["recommendation_status"], status)
                self.assertIsNone(result["reduction_pct"])

    def test_per_night_operator_settings_override_defaults(self):
        header = ("bin,night_date,eligible_zones,approved_reductions_pct,scheduled_eligible_light_output,"
                  "fixed_light_output,target_artificial_light_output,light_output_unit")
        default = "1000001,,facade,0|50,80,20,60,lm"
        dated = "1000001,2021-11-06,decorative,0|100,70,30,40,lm"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operators.csv"
            path.write_text(f"{header}\n{dated}\n{default}\n", encoding="utf-8")
            self.assertEqual(load_operator_settings(path, self.row["night_date"])["1000001"]["eligible_zones"],
                             "decorative")
            self.assertEqual(load_operator_settings(path, date(2021, 11, 7))["1000001"]["eligible_zones"],
                             "facade")
            path.write_text(f"{header}\n{default}\n{default}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate operator settings"):
                load_operator_settings(path, self.row["night_date"])
