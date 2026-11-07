import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py

from src.features.training import building_nights, date_coverage
from src.ingest.hrrr import _field_ranges, forecast_hours
from src.ingest.nyc import PILOT_BOUNDS, buildings
from src.ingest.usno import natural_light
from src.ingest.viirs import FIELDS, sample_file


class IngestTests(unittest.TestCase):
    def test_duplicate_surveys_do_not_sum_or_invent_collisions(self):
        night = date(2021, 10, 1)
        surveys = [
            {"study_building_id": site, "night_date": night, "observed_collision_count": count}
            for site, count in [("a", 1), ("a", 1), ("b", 0), ("b", 2), ("c", 0)]
        ]
        rows = list(building_nights(surveys))
        self.assertEqual([row["observed_collision_count"] for row in rows], [1, None, 0])
        self.assertEqual([row["survey_records"] for row in rows], [2, 2, 1])

    def test_coverage_separates_gaps_from_contiguous_nights(self):
        dates = [date(2021, 10, day) for day in [7, 2, 3, 3, 4, 8]]
        coverage = date_coverage(dates)
        self.assertEqual(coverage["nights"], 5)
        self.assertEqual(coverage["longest_contiguous"], {
            "start": date(2021, 10, 2), "end": date(2021, 10, 4), "nights": 3,
        })

    def test_astronomy_aligns_utc_events_across_the_fall_time_change(self):
        def events(*entries):
            return [{"time": time, "phen": event} for time, event in entries]

        class Astronomy:
            def json(self, source, url, *, params):
                days = {
                    "2021-11-06": {
                        "fracillum": "5%",
                        "sundata": events(("21:47", "Set"), ("22:15", "End Civil Twilight")),
                        "moondata": events(("14:30", "Rise"), ("22:05", "Set")),
                    },
                    "2021-11-07": {
                        "sundata": events(("11:03", "Begin Civil Twilight"), ("11:31", "Rise")),
                        "moondata": events(("15:50", "Rise"),),
                    },
                }
                assert params["tz"] == 0
                return {"properties": {"data": days[params["date"]]}}

        features = natural_light(Astronomy(), date(2021, 11, 6))
        self.assertEqual(features["sunrise_utc"], datetime(2021, 11, 7, 11, 31, tzinfo=UTC))
        self.assertEqual(features["moon_above_horizon_minutes"], 18)
        self.assertEqual(features["moon_illum_fraction"], 0.05)

    def test_hrrr_uses_only_night_hours_and_an_earlier_run(self):
        sunset = datetime(2021, 10, 1, 22, 37, tzinfo=UTC)
        sunrise = datetime(2021, 10, 2, 10, 54, tzinfo=UTC)
        decision, run, leads = forecast_hours(sunset, sunrise)
        self.assertEqual(decision, datetime(2021, 10, 1, 21, 37, tzinfo=UTC))
        self.assertEqual(run, datetime(2021, 10, 1, 19, tzinfo=UTC))
        self.assertEqual(list(leads), list(range(4, 16)))

    def test_hrrr_ranges_stop_before_the_next_message(self):
        index = ("1:0:d=2021100119:VIS:surface:4 hour fcst:\n"
                 "2:150:d=2021100119:TMP:surface:4 hour fcst:\n"
                 "3:250:d=2021100119:PRATE:surface:4 hour fcst:\n")
        self.assertEqual(list(_field_ranges(index)), [
            (("VIS", "surface"), (0, 149)), (("PRATE", "surface"), (250, None)),
        ])

    def test_footprint_join_normalizes_pluto_numeric_lot_ids(self):
        class Socrata:
            def json(self, source, url, *, params):
                if url.endswith("64uk-42ks.json"):
                    return [{"bbl": "1000010001.00000000", "landuse": "5"}]
                return [{
                    "bin": "1000001", "mappluto_bbl": "1000010001", "height_roof": "100",
                    "the_geom": {"type": "Polygon", "coordinates": [[
                        [-74.01, 40.71], [-74.011, 40.71], [-74.01, 40.711], [-74.01, 40.71],
                    ]]},
                }]

        props = buildings(Socrata(), PILOT_BOUNDS, set())["1000001"]["properties"]
        self.assertEqual(props["bbl"], "1000010001")
        self.assertEqual(props["land_use"], "5")
        self.assertAlmostEqual(props["roof_height_m"], 30.48)
        self.assertGreater(props["footprint_area_m2"], 0)

    def test_black_marble_respects_grid_orientation_and_quality(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "test.h5"
            with h5py.File(path, "w") as file:
                fields = file.create_group(FIELDS)
                fields["lat"] = [41, 40]
                fields["lon"] = [-75, -74]
                fields["DNB_BRDF-Corrected_NTL"] = [[2.5, 6.0], [-999.9, 1.0]]
                fields["Mandatory_Quality_Flag"] = [[0, 1], [0, 0]]
                fields["QF_Cloud_Mask"] = [[0, 128], [65535, 0]]
            samples = sample_file(path, {
                "good": (40.9, -75), "cloudy": (41, -74),
                "fill": (40, -75), "outside": (38, -75),
            }, "test.h5")
        self.assertEqual(samples["good"]["observed_neighborhood_radiance"], 2.5)
        self.assertIsNone(samples["cloudy"]["observed_neighborhood_radiance"])
        self.assertEqual(samples["cloudy"]["radiance_cloud_mask"], 128)
        self.assertIsNone(samples["fill"]["observed_neighborhood_radiance"])
        self.assertNotIn("outside", samples)
