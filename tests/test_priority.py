"""Relative priority is consistent across scoring, summary, exports, and map data."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import building_analysis
import main
from src.dashboard.server import _night_payload
from src.recommendations.priority import assign_priorities
from tests.test_daily import BUILDING, LIGHT, NIGHT


def rows(values):
    return [{"bin": str(index), "night_date": NIGHT, "expected_collision_count": value}
            for index, value in enumerate(values)]


class PriorityTests(TestCase):
    def test_distribution_uses_model_order_without_changing_estimates(self):
        original = rows([n / 1000 for n in range(10, 0, -1)])
        ranked = assign_priorities(original)
        self.assertEqual([r["risk_priority"] for r in ranked], ["high"] * 2 + ["medium"] * 3 + ["low"] * 5)
        self.assertEqual([r["expected_collision_count"] for r in ranked],
                         [r["expected_collision_count"] for r in original])
        self.assertNotIn("priority_rank", original[0])

    def test_ties_are_not_split_by_input_order(self):
        original = rows([1, 1, 1, .5, .5, .5, .1, .1, .1, .1])
        forward, backward = assign_priorities(original), assign_priorities(original[::-1])
        self.assertEqual({r["bin"]: r["risk_priority"] for r in forward},
                         {r["bin"]: r["risk_priority"] for r in backward})
        self.assertEqual([r["priority_rank"] for r in forward], [1, 1, 1, 4, 4, 4, 7, 7, 7, 7])
        self.assertEqual([r["risk_priority"] for r in forward], ["high"] * 3 + ["medium"] * 3 + ["low"] * 4)

    def test_zero_small_groups_and_invalid_values(self):
        ranked = assign_priorities(rows([None, float("nan"), float("inf"), -1, 0, 0, .001]))
        self.assertEqual([r["risk_priority"] for r in ranked], ["unavailable"] * 4 + ["low", "low", "high"])
        self.assertEqual(ranked[-1]["priority_scored_buildings"], 3)
        self.assertEqual(assign_priorities(rows([.001]))[0]["risk_priority"], "high")
        self.assertEqual([r["risk_priority"] for r in assign_priorities(rows([0, 0]))], ["low", "low"])
        self.assertEqual(assign_priorities([]), [])

    def test_nights_are_ranked_separately(self):
        original = rows([1, .01])
        original[1]["night_date"] = "another night"
        self.assertEqual([r["priority_rank"] for r in assign_priorities(original)], [1, 1])

    def test_scorer_summary_and_map_share_the_same_priority(self):
        buildings = [{**BUILDING, **LIGHT, "bin": str(index), "night_date": NIGHT}
                     for index in range(10)]
        model = SimpleNamespace(score_rows=lambda rows: [
            SimpleNamespace(expected=(10 - i) / 1000, missing=(), contributions=(), features=("roof_height_m",))
            for i, _ in enumerate(rows)])
        scored = building_analysis.score_buildings(buildings, model, mode="forecast")
        with (patch.dict(main._buildings, {"pilot": buildings}, clear=True),
              patch.object(building_analysis, "forecast_model_summary", return_value="test model"),
              patch.object(building_analysis, "analyze_forecast", return_value=scored)):
            result = main.run_night(NIGHT, "pilot", forecast=True, log=lambda _: None)
        payload = _night_payload(result)
        self.assertEqual(payload["overview"]["priorities"], {"high": 2, "medium": 3, "low": 5})
        properties = [f["properties"] for f in payload["buildings"]["features"]]
        self.assertEqual([p["risk_priority"] for p in properties], [r["risk_priority"] for r in scored])
        self.assertEqual([p["priority_rank"] for p in properties], list(range(1, 11)))
        self.assertTrue(all("action" not in p and "reduction_pct" not in p for p in properties))
        self.assertTrue(all(p["reasons"] == () for p in properties))
