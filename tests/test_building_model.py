"""The model learns building-level differences when study sites carry building traits."""

import csv
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np

from src.models.__main__ import _join_sites
from src.models.count import train

SITES = {  # Taller, larger buildings collide more in this synthetic world.
    "a": (20, 400, 1925), "b": (35, 900, 1960), "c": (60, 1500, 1975), "d": (90, 2500, 1990),
    "e": (140, 4000, 2000), "f": (200, 6000, 2010), "g": (25, 600, 1930), "h": (120, 3000, 1985),
}


def survey_rows(seed=1):
    rng = np.random.default_rng(seed)
    for index in range(40):
        night = date(2019, 9, 1) + timedelta(days=index)
        migration = 200 + 150 * (index % 7)
        for site, (height, area, _) in SITES.items():
            rate = np.exp(-4 + 0.8 * np.log1p(height) + 0.0004 * migration)
            yield {"study_building_id": site, "night_date": night.isoformat(), "season": "fall",
                   "observed_collision_count": str(int(rng.poisson(rate))),
                   "observed_migration_birds_per_km_h": str(migration), "observed_visibility_m": "12000",
                   "observed_cloud_ceiling_m": str(800 + 50 * (index % 5)), "observed_wind_u_m_s": "-1",
                   "observed_wind_v_m_s": str(index % 3 - 1), "observed_temperature_c": str(12 + index % 6)}


def write_sites(folder):
    path = Path(folder) / "site_features.csv"
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, ["study_building_id", "bin", "feature_location", "roof_height_m",
                                       "footprint_area_m2", "construction_year", "surveyed_facade_m"])
        writer.writeheader()
        for index, (site, (height, area, year)) in enumerate(SITES.items()):
            writer.writerow({"study_building_id": site, "bin": str(1000001 + index),
                             "feature_location": "matched_footprint", "roof_height_m": height,
                             "footprint_area_m2": area, "construction_year": year,
                             "surveyed_facade_m": 200})
    return path


class BuildingModelTests(TestCase):
    def test_site_traits_produce_different_estimates_for_different_buildings(self):
        rows = list(survey_rows())
        with TemporaryDirectory() as folder:
            source = _join_sites(rows, write_sites(folder))
        self.assertEqual((source["sites"], source["rows_matched"]), (len(SITES), len(rows)))
        model = train(rows)
        self.assertIn("evaluation", model.report["profiles"]["footprint"])

        survey = {key: value for key, value in rows[0].items() if key != "surveyed_facade_m"}
        night = {**survey, "feature_location": "matched_footprint", "bin": "1099999"}
        short = {**night, "roof_height_m": 15, "footprint_area_m2": 500, "footprint_perimeter_m": 200}
        tall = {**night, "roof_height_m": 250, "footprint_area_m2": 5000, "footprint_perimeter_m": 200}
        self.assertIn("roof_height_m", model.features_for(tall))
        self.assertGreater(model.predict(tall), 2 * model.predict(short))

    def test_buildings_without_traits_fall_back_to_the_night_level_fit(self):
        rows = list(survey_rows())
        with TemporaryDirectory() as folder:
            _join_sites(rows, write_sites(folder))
        model = train(rows)
        unmatched = {**rows[0], "feature_location": None, "roof_height_m": None}
        self.assertNotIn("roof_height_m", model.features_for(unmatched))
        self.assertIsNotNone(model.predict(unmatched))

    def test_conflicting_site_traits_are_rejected(self):
        rows = [{**row, "roof_height_m": "1"} for row in survey_rows()]
        with TemporaryDirectory() as folder, self.assertRaises(ValueError):
            _join_sites(rows, write_sites(folder))


class ExposureTests(TestCase):
    def test_estimate_scales_with_facade_length(self):
        rows = list(survey_rows())
        with TemporaryDirectory() as folder:
            _join_sites(rows, write_sites(folder))
        model = train(rows)
        self.assertTrue(model.report["profiles"]["footprint"]["exposure_offset"])
        survey = {key: value for key, value in rows[0].items() if key != "surveyed_facade_m"}
        building = {**survey, "feature_location": "matched_footprint", "bin": "1099999",
                    "roof_height_m": 30, "footprint_area_m2": 600}
        short_wall = model.predict({**building, "footprint_perimeter_m": 80})
        long_wall = model.predict({**building, "footprint_perimeter_m": 320})
        self.assertAlmostEqual(long_wall / short_wall, 4.0, places=6)

    def test_building_without_perimeter_uses_the_night_level_fit(self):
        rows = list(survey_rows())
        with TemporaryDirectory() as folder:
            _join_sites(rows, write_sites(folder))
        model = train(rows)
        survey = {key: value for key, value in rows[0].items() if key != "surveyed_facade_m"}
        building = {**survey, "feature_location": "matched_footprint", "bin": "1099999",
                    "roof_height_m": 30, "footprint_area_m2": 600}
        self.assertNotIn("roof_height_m", model.features_for(building))
