"""One relative priority scale for model estimates within an area and night."""

from collections import defaultdict

from src.features.model_inputs import number

HIGH_SHARE = 0.20
MEDIUM_SHARE = 0.50
PRIORITY_BASIS = "Expected collisions ranked within the selected area and night"


def assign_priorities(rows):
    """Return new rows, preserving counts and keeping equal estimates in the same tier.

    The share of scored buildings with a strictly greater estimate sets the tier:
    under 20% is high, under 50% is medium, otherwise low. Ties may expand a tier.
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
        previous, rank = None, 0
        for position, (index, value) in enumerate(scored):
            if value != previous:
                rank = position + 1
            share = (rank - 1) / len(scored)
            priority = "low" if value == 0 else (
                "high" if share < HIGH_SHARE else "medium" if share < MEDIUM_SHARE else "low")
            result[index].update(risk_priority=priority, priority_rank=rank,
                                 priority_scored_buildings=len(scored))
            previous = value
    return result
