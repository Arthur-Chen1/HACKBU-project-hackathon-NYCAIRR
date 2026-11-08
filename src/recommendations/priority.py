"""Shared ranking for the dimming map and legacy priority export fields."""

from collections import defaultdict
from itertools import groupby
from math import ceil

from src.features.model_inputs import number

HIGH_SHARE = 0.05
MEDIUM_SHARE = 0.25
PRIORITY_BASIS = "Expected collisions ranked within the selected area and night"


def assign_priorities(rows):
    """Return new rows, preserving counts and keeping equal estimates in the same tier.

    At most ceil(5% of scored buildings) are high and ceil(25%) are high/medium.
    Keep tied groups together in the lower band if they cross a boundary; this
    prevents uniform night-level estimates from coloring an entire area red.
    Zero is always low; invalid or missing estimates are unavailable, never ranked.
    """
    result = [{**row, "risk_priority": "unavailable", "priority_rank": None,
               "priority_scored_buildings": 0, "priority_basis": PRIORITY_BASIS} for row in rows]
    nights = defaultdict(list)
    for index, row in enumerate(result):
        value = number(row.get("expected_collision_count"), 0)
        if value is not None:
            nights[row.get("night_date")].append((index, value))
    for scored in nights.values():
        scored.sort(key=lambda item: -item[1])
        high_limit = ceil(HIGH_SHARE * len(scored))
        medium_limit = ceil(MEDIUM_SHARE * len(scored))
        position = 0
        for value, matches in groupby(scored, key=lambda item: item[1]):
            group = list(matches)
            rank = position + 1
            position += len(group)
            priority = "low" if value == 0 else (
                "high" if position <= high_limit else "medium" if position <= medium_limit else "low")
            for index, _ in group:
                result[index].update(risk_priority=priority, priority_rank=rank,
                                     priority_scored_buildings=len(scored))
    return result
