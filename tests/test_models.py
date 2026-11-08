"""Offline checks for collision count training and decision-time feature gates."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.models.count import load_model, train


def training_rows():
    for index in range(8):
        night = date(2021, 10, 1) + timedelta(days=index)
        sunset = datetime(2021, 10, night.day, 22, 30, tzinfo=UTC)
        for building in range(4):
            yield {
                "study_building_id": str(building),
                "night_date": night,
                "observed_collision_count": index % 3 + building,
                "observed_migration_birds_per_km_h": 60 * index,
                "observed_visibility_m": 10000,
                "observed_cloud_ceiling_m": 300,
                "observed_wind_u_m_s": -2,
                "observed_wind_v_m_s": 1,
                "observed_temperature_c": 15,
                "sunset_utc": sunset,
                "sunrise_utc": sunset + timedelta(hours=12),
                "moon_illum_fraction": 0.5,
                "moon_above_horizon_minutes": 40 * index,
                "bin": str(building + 1000000),
                "feature_location": "matched_footprint",
                "roof_height_m": 30 * (building + 1),
                "footprint_area_m2": 100 * (building + 1),
                "observed_neighborhood_radiance": 10 * building,
                "radiance_quality": 0,
                "prior_neighborhood_radiance": 10 * building,
                "prior_radiance_quality": 0,
                "prior_radiance_available_at_utc": sunset - timedelta(days=3),
                "forecast_migration_birds_per_km_h": 55 * index,
                "forecast_migration_issue_utc": sunset - timedelta(hours=4),
                "decision_at_utc": sunset - timedelta(hours=1),
                "hrrr_run_utc": sunset - timedelta(hours=3, minutes=30),
                "hrrr_expected_hours": 12,
                "hrrr_required_field_hours": 12,
                "forecast_visibility_m": 10000,
                "forecast_wind_u_m_s": -2,
                "forecast_wind_v_m_s": 1,
                "forecast_cloud_cover_pct": 20,
                "forecast_precip_rate_mm_h": 0,
            }


class CountModelTests(TestCase):
    def test_evaluation_separates_nights_and_sites_and_matches_comparison_rows(self):
        model = train(training_rows())
        profiles = model.report["profiles"]
        self.assertEqual(model.report["count_distribution"][0], 3)
        future = profiles["base"]["evaluation"]["future_nights"]
        self.assertLess(future["train"]["last_night"], future["test"]["first_night"])
        sites = profiles["base"]["evaluation"]["unseen_buildings"]
        self.assertFalse(set(sites["train"]["sites"]) & set(sites["test"]["sites"]))
        all_features = profiles["natural_light+footprint+artificial_light"]["evaluation"]["future_nights"]
        self.assertIn("base_poisson_same_rows", all_features)
        self.assertEqual(all_features["test"]["rows"], future["test"]["rows"])

    def test_model_round_trips_and_uses_only_supported_enrichments(self):
        rows = list(training_rows())
        row = rows[0]
        model = train(rows)
        self.assertIn("potential_moonlit_hours", model.features_for(row))
        self.assertIn("observed_neighborhood_radiance", model.features_for(row))
        self.assertIn("roof_height_m", model.features_for(row))
        partial = {**row, "radiance_quality": 1, "feature_location": "nyc_reference_point"}
        self.assertNotIn("observed_neighborhood_radiance", model.features_for(partial))
        self.assertNotIn("roof_height_m", model.features_for(partial))
        self.assertIsNotNone(model.predict(partial))
        self.assertIsNone(model.predict({**row, "observed_visibility_m": ""}))
        with TemporaryDirectory() as folder:
            output = Path(folder) / "model.json"
            model.save(output)
            fitted = load_model(output)
            self.assertEqual(fitted.predict(row), model.predict(row))
            self.assertEqual(fitted.features_for(row), model.features_for(row))

    def test_forecast_uses_issued_inputs_and_clears_future_radiance(self):
        rows = list(training_rows())
        row = rows[0]
        model = train(rows, mode="forecast")
        self.assertIn("prior_neighborhood_radiance", model.features_for(row))
        self.assertIsNotNone(model.predict(row))
        retrospective = {**row, "observed_migration_birds_per_km_h": 100000, "observed_visibility_m": 1}
        self.assertEqual(model.predict(row), model.predict(retrospective))
        future_light = {**row, "prior_radiance_available_at_utc": row["sunset_utc"]}
        self.assertNotIn("prior_neighborhood_radiance", model.features_for(future_light))
        for field, value in (
            ("forecast_migration_issue_utc", row["sunset_utc"]),
            ("hrrr_run_utc", row["decision_at_utc"]),
            ("decision_at_utc", row["sunset_utc"]),
            ("hrrr_required_field_hours", 11),
        ):
            with self.subTest(field=field):
                changed = {**row, field: value}
                self.assertIn(field, model.missing_inputs(changed))
                self.assertIsNone(model.predict(changed))

    def test_invalid_training_data_cannot_create_a_model(self):
        rows = list(training_rows())
        with self.assertRaisesRegex(ValueError, "Duplicate study building-night"):
            train([*rows, rows[0]])
        with self.assertRaisesRegex(ValueError, "Invalid collision count"):
            train([{**rows[0], "observed_collision_count": -1}, *rows[1:]])
        with self.assertRaisesRegex(ValueError, "at least two different nights"):
            train(rows[:4], mode="forecast")
