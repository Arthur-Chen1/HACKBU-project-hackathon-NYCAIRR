import unittest
from datetime import UTC, datetime, timedelta

import numpy as np

from src.features import birds
from src.ingest.nexrad import every


class BirdRadarTests(unittest.TestCase):
    def test_reflectivity_conversion_matches_vol2bird_scale(self):
        # bioRad dbz_to_eta: 1000 * pi^5 * |K|^2 / lambda^4 * Z. At 10.7 cm, 0 dBZ is ~21.7 cm^2/km^3,
        # about 2 birds/km^3 at 11 cm^2 per bird.
        self.assertAlmostEqual(float(birds.eta_from_dbz(np.array(0.0))), 21.7, delta=0.1)
        self.assertAlmostEqual(float(birds.eta_from_dbz(np.array(10.0))), 217.1, delta=1)

    def test_vad_recovers_flight_vector_and_drops_aliased_gates(self):
        rng = np.random.default_rng(0)
        azimuth = rng.uniform(0, 360, 2000)
        elevation = np.full(azimuth.shape, 0.5)
        u, v = -5.0, -9.0  # Flying toward the south-southwest.
        a, e = np.radians(azimuth), np.radians(elevation)
        radial = np.cos(e) * (u * np.sin(a) + v * np.cos(a)) + rng.normal(0, 3, azimuth.size)
        radial[:50] += 48  # Aliased by twice a 24 m/s Nyquist velocity.
        fit_u, fit_v, sd, used = birds._vad(azimuth, elevation, radial)
        self.assertAlmostEqual(fit_u, u, delta=0.5)
        self.assertAlmostEqual(fit_v, v, delta=0.5)
        self.assertAlmostEqual(sd, 3, delta=0.5)
        self.assertLessEqual(used, 1950)

    def test_vad_needs_azimuth_coverage(self):
        azimuth = np.linspace(0, 80, 500)
        self.assertIsNone(birds._vad(azimuth, np.full(500, 0.5), np.zeros(500)))

    def test_nearest_ray_match_wraps_north(self):
        rays = birds._match_rays(np.array([0.2, 90.0, 180.0, 359.7]), np.array([359.9, 89.0, 0.4]))
        self.assertEqual(rays.tolist(), [3, 1, 0])

    def test_rain_and_no_echo_are_classified_separately(self):
        sweep = {
            "z": np.array([[10.0, 10.0, np.nan, 40.0]]),
            "rhohv": np.array([[0.5, 0.99, np.nan, 0.6]]),
            "cfp": None,
        }
        birds._classify(sweep)
        self.assertEqual(sweep["weather"].tolist(), [[False, True, False, True]])
        self.assertEqual(sweep["valid"].tolist(), [[True, False, True, False]])
        self.assertEqual(sweep["eta"][0, 2], 0.0)  # No echo counts as zero birds.

    def test_summary_integrates_layers_and_ignores_unknowns(self):
        layer = lambda bottom, dens, u=0.0, v=-10.0: {
            "height_bottom_m": bottom, "height_top_m": bottom + 200, "birds_per_km3": dens,
            "eta_cm2_per_km3": None if dens is None else dens * 11, "u_m_s": u, "v_m_s": v,
            "ground_speed_m_s": None if u is None else float(np.hypot(u, v)),
        }
        scan = birds.summarize([layer(0, 100), layer(200, 50), layer(400, None), layer(800, 50)])
        self.assertAlmostEqual(scan["birds_per_km2"], 40)
        self.assertAlmostEqual(scan["migration_traffic_birds_per_km_h"], 40 * 10 * 3.6)
        self.assertAlmostEqual(scan["direction_deg"], 180)
        self.assertAlmostEqual(scan["fraction_below_600m"], 150 / 200)
        self.assertFalse(scan["profile_complete"])

    def test_scan_thinning_keeps_one_per_interval(self):
        start = datetime(2021, 9, 11, tzinfo=UTC)
        found = [(start + timedelta(minutes=m), str(m)) for m in (0, 9, 18, 31, 40, 62)]
        self.assertEqual([url for _, url in every(found, 30)], ["0", "31", "62"])


if __name__ == "__main__":
    unittest.main()
