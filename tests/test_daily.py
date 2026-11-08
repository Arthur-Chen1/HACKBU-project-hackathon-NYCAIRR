import csv
import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import building_analysis
import lighting_judgement
import main
from src.dashboard.server import _json, _night_payload
from src.features import daily
from src.ingest.hrrr import forecast_hours

NIGHT = date(2021, 10, 1)
SUNSET = datetime(2021, 10, 1, 22, 38, tzinfo=UTC)
SUNRISE = datetime(2021, 10, 2, 10, 54, tzinfo=UTC)
BIN = "1000001"
BUILDING = {"bin": BIN, "bbl": "1000000001", "latitude": 40.75, "longitude": -73.98,
            "in_program": True, "roof_height_m": 20, "land_use_label": "Office",
            "construction_year": 1999, "geometry": None}
LIGHT = {"sunset_utc": SUNSET, "sunrise_utc": SUNRISE,
         "civil_dusk_utc": datetime(2021, 10, 1, 23, 5, tzinfo=UTC),
         "civil_dawn_utc": datetime(2021, 10, 2, 10, 27, tzinfo=UTC),
         "moon_illum_fraction": 0, "moon_above_horizon_minutes": 280}
WEATHER = {"forecast_visibility_m": 10_000, "forecast_cloud_ceiling_m": None,
           "hrrr_run_utc": datetime(2021, 10, 1, 19, tzinfo=UTC), "hrrr_expected_hours": 12,
           "hrrr_required_field_hours": 12}


class DailyForecastTests(unittest.TestCase):
    def test_daily_run_keeps_one_row_per_bin_without_using_radar(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            forecast = folder / "daily.csv"
            forecast.write_text(
                "night_date,bin,forecast_migration_birds_per_km_h,forecast_migration_issue_utc,"
                "forecast_migration_region,prior_neighborhood_radiance,prior_radiance_quality,"
                "prior_radiance_available_at_utc,prior_radiance_granule\n"
                "2021-10-01,,2500,2021-10-01T19:30:00Z,NYC,,,,\n"
                f"2021-10-01,{BIN},,,,0,0,2021-09-30T18:00:00Z,sample\n")
            operator = folder / "operator.csv"
            operator.write_text(
                "bin,eligible_zones,approved_reductions_pct,scheduled_eligible_light_output,"
                "fixed_light_output,target_artificial_light_output,light_output_unit\n"
                f"{BIN},facade,0|25|50|100,80,20,60,lm\n")
            inputs = daily.load_daily_inputs(forecast)
            with (patch.dict(main._buildings, {"pilot": [BUILDING]}, clear=True),
                  patch.object(daily.usno, "natural_light", return_value=LIGHT),
                  patch.object(daily.hrrr, "weather_forecast", return_value={BIN: WEATHER}) as weather,
                  patch.object(building_analysis, "_model", return_value=None),
                  patch.object(main.migration_analysis, "fetch") as fetch,
                  patch.object(lighting_judgement, "OUTPUT_DIR", folder)):
                result = main.run_night(NIGHT, "pilot", forecast=True, daily_inputs=inputs, operator_settings=operator,
                                        log=lambda _: None)
                csv_path, _ = lighting_judgement.write(result["decisions"], result["rows"], "forecast")
                fetch.assert_not_called()
                self.assertEqual(weather.call_args.kwargs["decision_at"], datetime(2021, 10, 1, 21, 38, tzinfo=UTC))
            self.assertEqual(len(result["rows"]), 1)
            row = result["rows"][0]
            self.assertEqual((row["bin"], row["night_date"], row["prior_neighborhood_radiance"]), (BIN, NIGHT, 0))
            feature = json.loads(_json(_night_payload(result)))["buildings"]["features"][0]["properties"]
            self.assertEqual((feature["risk_priority"], feature["reduction_pct"]), ("high", 50))
            self.assertIsNone(feature["expected_collision_count"])
            with csv_path.open() as file:
                self.assertEqual(next(csv.DictReader(file))["expected_collision_count"], "")

    def test_late_migration_makes_priority_unavailable(self):
        row = {**LIGHT, **WEATHER, "night_date": NIGHT, "forecast_migration_birds_per_km_h": 2500,
               "forecast_migration_issue_utc": "2021-10-01T21:45:00Z", "decision_at_utc": "2021-10-01T21:38:00Z"}
        result = building_analysis.score_buildings([row], None, mode="forecast")[0]
        self.assertEqual(result["risk_priority"], "unavailable")
        self.assertIn("forecast_migration_issue_utc", result["risk_reasons"][0])

    def test_morning_forecast_selects_extended_hrrr_run(self):
        now = datetime(2021, 10, 1, 12, 30, tzinfo=UTC)
        decision, run, leads = forecast_hours(SUNSET, SUNRISE, decision_at=now)
        self.assertEqual(decision, now)
        self.assertEqual(run, datetime(2021, 10, 1, 6, tzinfo=UTC))
        self.assertEqual(list(leads), list(range(17, 29)))


if __name__ == "__main__":
    unittest.main()
