import unittest
from datetime import UTC, datetime

import lighting_judgement as lj

SUNSET = datetime(2021, 9, 10, 23, 13, tzinfo=UTC)
SUNRISE = datetime(2021, 9, 11, 10, 33, tzinfo=UTC)


def row(**changes):
    base = {
        "bin": "1000001", "name": None, "latitude": 40.75, "longitude": -73.98,
        "in_program": True, "height_class": "low", "roof_height_m": 20.0, "footprint_area_m2": 800.0,
        "night_level": "high", "night_traffic_birds_per_km_h": 6000.0,
        "neighborhood_relative_density": 1.0,
        "sunset_utc": SUNSET, "sunrise_utc": SUNRISE,
        "active_window_start_utc": None, "active_window_end_utc": None,
    }
    return {**base, **changes}


class JudgementTests(unittest.TestCase):
    def test_plain_building_on_high_night_reduces_for_the_whole_night(self):
        decision = lj.judge(row())
        self.assertEqual((decision["tier"], decision["reduction_pct"]), ("reduce", 25))
        self.assertEqual((decision["start_utc"], decision["end_utc"]), (SUNSET, SUNRISE))
        self.assertEqual(decision["start_local"], "7:13 PM")

    def test_tall_large_building_in_dense_area_turns_off(self):
        decision = lj.judge(row(height_class="tall", roof_height_m=300.0, footprint_area_m2=8000.0,
                                neighborhood_relative_density=1.4))
        self.assertEqual((decision["tier"], decision["score"]), ("off", 5))
        self.assertEqual(len(decision["reasons"]), 4)

    def test_low_night_takes_no_action_even_for_towers(self):
        decision = lj.judge(row(night_level="low", night_traffic_birds_per_km_h=50.0, height_class="tall",
                                roof_height_m=300.0, footprint_area_m2=8000.0))
        self.assertEqual(decision["tier"], "none")
        self.assertIsNone(decision["start_utc"])

    def test_poor_weather_escalates_one_step(self):
        fog = {"source": "test", "visibility_m": 2000.0, "cloud_ceiling_m": 22000.0}
        self.assertEqual(lj.judge(row(), fog)["tier"], "dim")
        clear = {"source": "test", "visibility_m": 16000.0, "cloud_ceiling_m": 22000.0}
        self.assertEqual(lj.judge(row(), clear)["tier"], "reduce")

    def test_sparse_neighborhood_lowers_score(self):
        self.assertEqual(lj.judge(row(neighborhood_relative_density=0.6))["tier"], "none")

    def test_unknown_radar_is_precautionary_not_zero(self):
        decision = lj.judge(row(night_level="unknown", night_traffic_birds_per_km_h=None,
                                height_class="tall", roof_height_m=200.0))
        self.assertEqual(decision["tier"], "reduce")
        self.assertIn("Radar unavailable", decision["reasons"][0])

    def test_out_of_program_buildings_are_skipped_by_default(self):
        rows = [row(), row(bin="1000002", in_program=False)]
        self.assertEqual([d["bin"] for d in lj.judge_all(rows)], ["1000001"])
        self.assertEqual(lj.judge_all(rows, include_out_of_program=True)[1]["tier"], "not_in_program")


if __name__ == "__main__":
    unittest.main()
