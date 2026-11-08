"""An explicit planning policy driven by predicted collisions, not a lighting-response model.

The collision model does not learn the effect of dimming. This rule translates its
expected count into a suggested percentage of nonessential light output to remove.
It does not predict collisions avoided or an ideal illuminance for birds.
"""

from src.features.model_inputs import number
from src.recommendations.priority import assign_priorities

# Policy anchor, not a fitted coefficient: 0.25 expected collisions suggests 50% dimming.
# Keep this absolute so a building's percentage does not change with map selection.
HALF_DIMMING_EXPECTED_COUNT = 0.25
POLICY_VERSION = "collision-planning-v1"
BASIS = "Collision-driven planning rule of thumb; not a validated lighting target"


def estimate_dimming(rows):
    """Attach suggested percentages and a sparse color rank to already-scored rows.

    suggested % = 100 * expected / (expected + 0.25)

    The rule is monotonic in the collision estimate, so the same ordering and tie
    groups rank both. Use full precision for ranking; round only for display.
    Missing estimates remain unavailable. No measured lighting inputs are inferred.
    """
    result = assign_priorities(rows)
    for row in result:
        expected = number(row.get("expected_collision_count"), 0)
        percent = None if expected is None else 100 * (expected / (expected + HALF_DIMMING_EXPECTED_COUNT))
        row.update(
            suggested_dimming_pct=percent,
            dimming_band=row["risk_priority"],
            dimming_rank=row["priority_rank"],
            dimming_scored_buildings=row["priority_scored_buildings"],
            dimming_basis=BASIS,
            dimming_policy_version=POLICY_VERSION,
        )
    return result
