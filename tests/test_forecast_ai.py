"""The gradient-boosted forecast model: proxies, decision-time gates, extrapolation, and scoring."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import numpy as np

import building_analysis
from src.dashboard import server
from src.ingest import hrrr
from src.models import migration_forecast
from src.models.__main__ import _join_sites
from src.models.forecast_ai import (
    BASIS, CLEAR_SKY_CEILING_M, STATION_VISIBILITY_CAP_M, features, load_forecast_model, train_forecast,
)
from tests.test_building_model import survey_rows, write_sites

SUNSET = datetime(2019, 9, 1, 23, 20, tzinfo=UTC)


def trained():
    rows = list(survey_rows())
    with TemporaryDirectory() as folder:
        _join_sites(rows, write_sites(folder))
    return train_forecast(rows)


def forecast_row(**changes):
    row = {
        "night_date": date(2019, 9, 1), "season": "fall", "bin": "1099999", "feature_location": "matched_footprint",
        "roof_height_m": 60, "footprint_area_m2": 1500, "footprint_perimeter_m": 200,
        "forecast_migration_birds_per_km_h": 800, "forecast_visibility_m": 12000, "forecast_cloud_ceiling_m": 900,
        "forecast_wind_u_m_s": -1, "forecast_wind_v_m_s": 0,
        "sunset_utc": SUNSET, "sunrise_utc": SUNSET + timedelta(hours=11),
        "decision_at_utc": SUNSET - timedelta(hours=1), "forecast_migration_issue_utc": SUNSET - timedelta(hours=5),
        "hrrr_run_utc": SUNSET - timedelta(hours=4), "hrrr_expected_hours": 11, "hrrr_required_field_hours": 11,
    }
    return {**row, **changes}


class ForecastModelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = trained()

    def test_training_uses_observed_proxies_and_reports_both_holdouts(self):
        profiles = self.model.report["profiles"]
        self.assertEqual([fit.name for fit in self.model.fits], ["footprint", "base"])
        for name in ("footprint", "base"):
            for split in ("future_nights", "unseen_buildings"):
                scores = profiles[name]["evaluation"][split]
                self.assertIn("gradient_boosting", scores)
                self.assertIn("poisson_same_features", scores)
        self.assertEqual(features(survey_rows().__next__(), "observed")["forecast_visibility_m"], 12000)

    def test_scores_use_forecast_fields_only(self):
        row = forecast_row()
        expected = self.model.predict(row)
        self.assertIsNotNone(expected)
        retrospective = {**row, "observed_migration_birds_per_km_h": 1e6, "observed_visibility_m": 1}
        self.assertEqual(self.model.predict(retrospective), expected)
        self.assertGreater(self.model.predict({**row, "forecast_migration_birds_per_km_h": 3000}), expected)

    def test_estimate_rises_smoothly_with_migration(self):
        estimates = [self.model.predict(forecast_row(forecast_migration_birds_per_km_h=t))
                     for t in (200, 300, 450, 650, 900, 1300)]
        self.assertTrue(all(later > earlier for earlier, later in zip(estimates, estimates[1:])), estimates)

    def test_late_or_incomplete_inputs_leave_the_estimate_blank(self):
        row = forecast_row()
        for field, value in (
            ("forecast_migration_issue_utc", SUNSET),
            ("hrrr_run_utc", row["decision_at_utc"]),
            ("decision_at_utc", SUNSET),
            ("hrrr_required_field_hours", 10),
            ("forecast_migration_birds_per_km_h", None),
        ):
            with self.subTest(field=field):
                changed = {**row, field: value}
                self.assertIsNone(self.model.predict(changed))
                self.assertIn(field, self.model.missing_inputs(changed))

    def test_buildings_differ_and_extrapolate_below_the_study_range(self):
        row = forecast_row()
        short, tall = (self.model.predict({**row, "roof_height_m": h}) for h in (20, 200))
        self.assertGreater(tall, 2 * short)
        # The shortest study building is 20 m. Trees alone would score a 10 m building the same.
        self.assertLess(self.model.predict({**row, "roof_height_m": 10}), short)
        self.assertIn("roof_height_m", self.model.features_for(row))

    def test_estimate_scales_with_facade_and_falls_back_without_traits(self):
        row = forecast_row()
        ratio = self.model.predict({**row, "footprint_perimeter_m": 400}) / self.model.predict(row)
        self.assertAlmostEqual(ratio, 2.0, places=6)
        unknown_height = {**row, "roof_height_m": 0}
        self.assertNotIn("roof_height_m", self.model.features_for(unknown_height))
        self.assertIsNotNone(self.model.predict(unknown_height))

    def test_forecast_units_match_the_training_station(self):
        values = features(forecast_row(forecast_cloud_ceiling_m=None, forecast_visibility_m=27000))
        self.assertEqual(values["forecast_cloud_ceiling_m"], CLEAR_SKY_CEILING_M)
        self.assertEqual(values["forecast_visibility_m"], STATION_VISIBILITY_CAP_M)

    def test_artifact_round_trips(self):
        rows = [forecast_row(roof_height_m=h) for h in (15, 60, 250)]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "forecast.joblib"
            self.model.save(path)
            self.assertTrue(path.with_suffix(".report.json").is_file())
            loaded = load_forecast_model(path)
        self.assertEqual([s.expected for s in loaded.score_rows(rows)], [s.expected for s in self.model.score_rows(rows)])

    def test_dashboard_rows_carry_the_same_outputs_as_the_regression(self):
        rows = [forecast_row(), forecast_row(forecast_migration_issue_utc=SUNSET)]
        scored = building_analysis.score_buildings(rows, self.model, mode="forecast")
        self.assertEqual(scored[0]["risk_basis"], BASIS)
        self.assertIn(scored[0]["risk_priority"], ("low", "medium", "high"))
        self.assertEqual(len(scored[0]["risk_reasons"]), 3)
        self.assertTrue(all(reason.endswith("the estimate") for reason in scored[0]["risk_reasons"]))
        self.assertIsNone(scored[1]["expected_collision_count"])
        self.assertEqual(scored[1]["risk_priority"], "unavailable")


class HrrrSamplingTests(TestCase):
    def test_each_point_reads_its_nearest_grid_cell_once(self):
        lat, lon = np.meshgrid(np.arange(40.0, 41.5, 0.03), np.arange(-74.5, -73.0, 0.03), indexing="ij")
        grid = {"latitudes": lat.ravel(), "longitudes": lon.ravel() % 360}
        values = np.arange(lat.size, dtype=float)
        values[5] = 9999  # missing
        points = {"a": (40.71, -74.01), "b": (40.76, -73.98), "far": (35.0, -80.0)}
        with (patch.object(hrrr, "codes_get_array", side_effect=lambda _, key: grid[key]),
              patch.object(hrrr, "codes_get_values", return_value=values),
              patch.object(hrrr, "codes_get", return_value=9999)):
            cells = hrrr._grid_cells(None, points)
            sampled = dict(hrrr._sample(None, cells, ("UGRD", "10 m above ground")))
        self.assertEqual(set(sampled), {"a", "b"})
        for key in sampled:
            p_lat, p_lon = points[key]
            nearest = np.argmin((lat.ravel() - p_lat) ** 2 + ((lon.ravel() - p_lon) * np.cos(np.radians(p_lat))) ** 2)
            self.assertEqual(sampled[key], values[nearest])


class ManualMigrationTests(TestCase):
    def test_dashboard_entry_is_logged_with_its_issue_time(self):
        start, end = date(2021, 10, 1), date(2021, 10, 2)
        inputs = server._manual_migration({"migration_forecast": "800", "migration_issued": "2021-10-01T12:00"},
                                          start, end, {})
        entry = inputs[(start, "")]
        self.assertEqual(entry["forecast_migration_birds_per_km_h"], 800)
        self.assertEqual(entry["forecast_migration_issue_utc"], datetime(2021, 10, 1, 16, tzinfo=UTC))
        self.assertIn((end, ""), inputs)
        self.assertEqual(server._manual_migration({"migration_forecast": ""}, start, end, {}), {})
        with self.assertRaises(ValueError):
            server._manual_migration({"migration_forecast": "-5", "migration_issued": "2021-10-01T12:00"}, start, end, {})


def dryad_nights(years=(2017, 2018, 2019)):
    """Synthetic nightly study rows: northwest (negative u and v) winds bring more migrants."""
    rng = np.random.default_rng(3)
    for year in years:
        for offset in range(60):
            night = date(year, 9, 1) + timedelta(days=offset)
            u, v = rng.uniform(-6, 6, 2)
            yield {"study_building_id": "a", "night_date": night, "season": "fall",
                   "observed_collision_count": 0, "observed_migration_birds_per_km_h": float(np.exp(5 - 0.3 * (u + v))),
                   "observed_visibility_m": 16000.0, "observed_cloud_ceiling_m": 3000.0,
                   "observed_wind_u_m_s": float(u), "observed_wind_v_m_s": float(v), "observed_temperature_c": 15.0}


class MigrationEstimateTests(TestCase):
    WEATHER = {"hrrr_expected_hours": 11, "hrrr_required_field_hours": 11, "forecast_visibility_m": 20000,
               "forecast_cloud_ceiling_m": None, "forecast_wind_u_m_s": -4, "forecast_wind_v_m_s": -4}

    def setUp(self):
        migration_forecast._model.cache_clear()
        self.addCleanup(migration_forecast._model.cache_clear)

    def test_estimate_follows_weather_and_is_issued_at_the_cutoff(self):
        decision = datetime(2026, 9, 20, 22, tzinfo=UTC)
        with patch.object(migration_forecast.dryad, "surveys", side_effect=lambda _: dryad_nights()):
            tailwind, _ = migration_forecast.estimate(date(2026, 9, 20), self.WEATHER, decision)
            headwind, _ = migration_forecast.estimate(
                date(2026, 9, 20), {**self.WEATHER, "forecast_wind_u_m_s": 4, "forecast_wind_v_m_s": 4}, decision)
        self.assertGreater(tailwind["forecast_migration_birds_per_km_h"], 2 * headwind["forecast_migration_birds_per_km_h"])
        self.assertEqual(tailwind["forecast_migration_issue_utc"], decision)
        self.assertIn("2017-2019", tailwind["forecast_migration_region"])

    def test_estimate_uses_only_earlier_seasons_and_known_dates(self):
        decision = datetime(2018, 9, 20, 22, tzinfo=UTC)
        with patch.object(migration_forecast.dryad, "surveys", side_effect=lambda _: dryad_nights()):
            earlier, _ = migration_forecast.estimate(date(2018, 9, 20), self.WEATHER, decision)
            winter, reason = migration_forecast.estimate(date(2026, 1, 15), self.WEATHER, decision)
            first, why = migration_forecast.estimate(date(2017, 9, 20), self.WEATHER, decision)
            partial, gap = migration_forecast.estimate(
                date(2026, 9, 20), {**self.WEATHER, "hrrr_required_field_hours": 3}, decision)
        self.assertIn("2017-2017", earlier["forecast_migration_region"])
        self.assertIsNone(winter)
        self.assertIn("outside the monitored migration seasons", reason)
        self.assertIsNone(first)
        self.assertIn("no earlier", why)
        self.assertIsNone(partial)
        self.assertIn("incomplete", gap)

    def test_forecast_rows_fall_back_to_the_estimate_only_without_an_issued_forecast(self):
        from src.features import daily
        sunset = datetime(2026, 9, 20, 23, tzinfo=UTC)
        light = {"sunset_utc": sunset, "sunrise_utc": sunset + timedelta(hours=11)}
        building = {"bin": "1000001", "latitude": 40.71, "longitude": -74.01}
        weather = {"1000001": self.WEATHER, daily.REGION_KEY: self.WEATHER}
        estimate = {"forecast_migration_birds_per_km_h": 640.0, "forecast_migration_issue_utc": sunset,
                    "forecast_migration_region": "estimate"}
        issued = {"forecast_migration_birds_per_km_h": 900.0, "forecast_migration_issue_utc": sunset,
                  "forecast_migration_region": "BirdCast"}
        night = date(2026, 9, 20)
        with (patch.object(daily.usno, "natural_light", return_value=light),
              patch.object(daily.hrrr, "weather_forecast", return_value=weather),
              patch.object(daily.migration_forecast, "estimate", return_value=(estimate, None)) as estimator):
            with TemporaryDirectory() as folder:
                automatic = daily.forecast_rows([building], night, {}, folder, now=sunset - timedelta(hours=5))[0]
                supplied = daily.forecast_rows([building], night, {(night, ""): issued}, folder,
                                               now=sunset - timedelta(hours=5))[0]
        self.assertEqual(automatic["forecast_migration_birds_per_km_h"], 640.0)
        self.assertEqual(supplied["forecast_migration_region"], "BirdCast")
        self.assertEqual(estimator.call_count, 1)
