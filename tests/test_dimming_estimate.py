"""Collision-driven dimming suggestions are bounded planning estimates with sparse colors."""

from unittest import TestCase

from src.recommendations.dimming_estimate import BASIS, POLICY_VERSION, estimate_dimming


def rows(values):
    return [{"bin": str(i), "night_date": "2026-11-08", "expected_collision_count": value}
            for i, value in enumerate(values)]


class DimmingEstimateTests(TestCase):
    def test_policy_anchors_and_bounds(self):
        estimates = estimate_dimming(rows([0, .01, .25, 1, 10, 1e308]))
        amounts = [row["suggested_dimming_pct"] for row in estimates]
        self.assertEqual(amounts[0], 0)
        self.assertEqual(amounts[2], 50)
        self.assertEqual(amounts[3], 80)
        self.assertTrue(all(0 <= value <= 100 for value in amounts))
        self.assertEqual(amounts, sorted(amounts))
        self.assertTrue(all(row["dimming_basis"] == BASIS for row in estimates))
        self.assertTrue(all(row["dimming_policy_version"] == POLICY_VERSION for row in estimates))

    def test_missing_or_invalid_forecasts_do_not_generate_dimming(self):
        estimates = estimate_dimming(rows([None, -1, float("nan"), float("inf")]))
        self.assertTrue(all(row["suggested_dimming_pct"] is None for row in estimates))
        self.assertTrue(all(row["dimming_band"] == "unavailable" for row in estimates))
        self.assertTrue(all(row["dimming_rank"] is None for row in estimates))

    def test_same_prediction_gives_same_percent_across_area_and_night(self):
        row = rows([.4])[0]
        single = estimate_dimming([row])[0]
        group = estimate_dimming([row, *rows([5, 4, 3, 2, 1])])[0]
        another_night = estimate_dimming([{**row, "night_date": "2026-11-09"}])[0]
        self.assertEqual(single["suggested_dimming_pct"], group["suggested_dimming_pct"])
        self.assertEqual(single["suggested_dimming_pct"], another_night["suggested_dimming_pct"])
        self.assertNotEqual(single["dimming_rank"], group["dimming_rank"])
        self.assertNotIn("suggested_dimming_pct", row)

    def test_color_order_tracks_dimming_and_only_top_five_percent_is_red(self):
        estimates = estimate_dimming(rows([n / 1000 for n in range(100, 0, -1)]))
        self.assertEqual([r["dimming_band"] for r in estimates], ["high"] * 5 + ["medium"] * 20 + ["low"] * 75)
        amounts = [r["suggested_dimming_pct"] for r in estimates]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_incompatible_light_proxies_are_not_treated_as_measured_targets(self):
        row = rows([.25])[0]
        with_light = {**row, "moon_illum_fraction": .8, "prior_neighborhood_radiance": 90,
                      "target_artificial_light_output": 20, "light_output_unit": "lm"}
        self.assertEqual(estimate_dimming([with_light])[0]["suggested_dimming_pct"],
                         estimate_dimming([row])[0]["suggested_dimming_pct"])
        self.assertNotIn("projected_collisions_after_dimming", estimate_dimming([with_light])[0])
