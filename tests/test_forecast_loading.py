"""Offline regressions for weather loading, coverage, and unnecessary forecast work."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.ingest import hrrr
from src.models import migration_forecast


class ForecastLoadingTests(TestCase):
    sunset = datetime(2026, 11, 8, 22, tzinfo=UTC)
    sunrise = sunset + timedelta(hours=3)
    points = {"1000001": (40.71, -74.01), "1000002": (40.72, -74.02)}

    def weather(self, workers, download):
        def sample(message, cells, field):
            # A present ceiling field with missing samples represents clear sky.
            if field == ("HGT", "cloud ceiling"):
                return []
            return [(key, float(message) + index) for index, key in enumerate(self.points)]

        with (patch.object(hrrr, "_download_hour", side_effect=download),
              patch.object(hrrr, "codes_new_from_message", side_effect=lambda value: value),
              patch.object(hrrr, "codes_release"),
              patch.object(hrrr, "_grid_cells", return_value=(list(self.points), [0, 1])) as cells,
              patch.object(hrrr, "_sample", side_effect=sample)):
            logs = []
            result = hrrr.weather_forecast(SimpleNamespace(root=Path("unused")), self.sunset, self.sunrise,
                                           self.points, workers=workers, log=logs.append)
            cells.assert_called_once()
        return result, logs

    @staticmethod
    def download(root, run, lead):
        return [(field, SimpleNamespace(read_bytes=lambda: str(lead).encode())) for field in hrrr.FIELDS], None

    def test_parallel_downloads_preserve_sequential_values_and_clear_sky_coverage(self):
        sequential, _ = self.weather(1, self.download)
        barrier = Barrier(2)

        def concurrent(root, run, lead):
            barrier.wait(timeout=5)  # Fails if independent hours are serialized.
            return self.download(root, run, lead)

        parallel, logs = self.weather(2, concurrent)
        self.assertEqual(parallel, sequential)
        for row in parallel.values():
            self.assertEqual(row["hrrr_expected_hours"], 4)
            self.assertEqual(row["hrrr_required_field_hours"], 4)
            self.assertEqual(row["hrrr_ceiling_hours"], 0)
            self.assertIsNone(row["forecast_cloud_ceiling_m"])
        self.assertIn("HRRR: processed 4/4 forecast hours", logs)

    def test_failed_hour_keeps_other_hours_but_cannot_pass_coverage(self):
        def missing(root, run, lead):
            return ([], "HRRR lead 4 unavailable") if lead == 4 else self.download(root, run, lead)

        result, logs = self.weather(2, missing)
        for row in result.values():
            self.assertEqual(row["hrrr_required_field_hours"], 3)
            self.assertEqual(row["hrrr_expected_hours"], 4)
            self.assertIn("HRRR lead 4 unavailable", row["hrrr_source_issues"])
        self.assertIn("HRRR lead 4 unavailable", logs)

    def test_beyond_horizon_makes_no_downloads(self):
        with patch.object(hrrr, "_download_hour") as download:
            result = hrrr.weather_forecast(SimpleNamespace(root=Path("unused")), self.sunset, self.sunrise,
                                           self.points, decision_at=self.sunset - timedelta(days=4))
        download.assert_not_called()
        for row in result.values():
            self.assertEqual(row["hrrr_required_field_hours"], 0)
            self.assertIn("48-hour horizon", row["hrrr_source_issues"][0])

    def test_absent_ceiling_field_is_not_treated_as_clear_sky(self):
        cache = MagicMock()
        with (patch.object(hrrr, "Cache", return_value=cache),
              patch.object(hrrr, "_field_ranges", return_value=[
                  (field, (0, 1)) for field in hrrr.FIELDS if field != ("HGT", "cloud ceiling")])):
            paths, issue = hrrr._download_hour(Path("unused"), self.sunset, 4)
        self.assertEqual(paths, [])
        self.assertIn("fields missing", issue)
        cache.session.close.assert_called_once()
        self.assertEqual(cache.get.call_count, 1)

    def test_incomplete_weather_does_not_download_or_train_migration_model(self):
        with patch.object(migration_forecast, "_model") as model:
            estimate, reason = migration_forecast.estimate(self.sunset.date(), {}, self.sunset)
        model.assert_not_called()
        self.assertIsNone(estimate)
        self.assertIn("incomplete", reason)
