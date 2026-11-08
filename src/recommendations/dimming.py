"""Select an operator-approved setting against an artificial light budget."""

import csv
from datetime import timedelta
from math import isclose, isfinite
from pathlib import Path

from src.features.model_inputs import NYC, local_night, number, parse_utc

OPERATOR_FIELDS = (
    "eligible_zones",
    "approved_reductions_pct",
    "scheduled_eligible_light_output",
    "fixed_light_output",
    "target_artificial_light_output",
    "light_output_unit",
)


def load_operator_settings(path, night):
    """Read per-BIN settings, giving a matching dated row precedence over the default."""
    defaults, dated, seen = {}, {}, set()
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = {"bin", *OPERATOR_FIELDS} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Operator settings missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            bin_id = (row["bin"] or "").strip()
            night_text = (row.get("night_date") or "").strip()
            applies_to = local_night(night_text)
            if night_text and applies_to is None:
                raise ValueError(f"Invalid night_date in operator settings on line {reader.line_num}")
            if not bin_id:
                raise ValueError(f"Missing BIN in operator settings on line {reader.line_num}")
            key = (bin_id, applies_to)
            if key in seen:
                raise ValueError(f"Duplicate operator settings for BIN {bin_id} on {applies_to or 'default'}")
            seen.add(key)
            if applies_to is None or applies_to == night:
                settings = dated if applies_to else defaults
                settings[bin_id] = {field: row[field] for field in OPERATOR_FIELDS}
    return defaults | dated


def _parts(value):
    if isinstance(value, str):
        value = value.split("|")
    if not isinstance(value, (tuple, list)):
        return []
    return list(filter(None, (str(part).strip() for part in value if part is not None)))


def _approved(value):
    settings = [number(part, 0, 100) for part in _parts(value)]
    if any(pct is None or not pct.is_integer() for pct in settings):
        return []
    return sorted({int(pct) for pct in settings})


def _twilight(row):
    start = parse_utc(row.get("civil_dusk_utc"))
    end = parse_utc(row.get("civil_dawn_utc"))
    if start is None or end is None or start >= end:
        return None
    dusk, dawn = start.astimezone(NYC), end.astimezone(NYC)
    if dusk.date() != local_night(row.get("night_date")) or dawn.date() - dusk.date() != timedelta(days=1):
        return None
    return {
        "start_utc": start, "end_utc": end,
        "start_local": dusk.strftime("%-I:%M %p %Z"), "end_local": dawn.strftime("%-I:%M %p %Z"),
    }


def _review(decision, status, reason):
    return {**decision, "recommendation_status": status, "reasons": [*decision["reasons"], reason]}


def recommend(row):
    """Choose the smallest approved cut that meets the configured light-output cap."""
    zones = list(dict.fromkeys(_parts(row.get("eligible_zones"))))
    unit = str(row.get("light_output_unit") or "").strip()
    eligible = number(row.get("scheduled_eligible_light_output"), 0)
    fixed = number(row.get("fixed_light_output"), 0)
    target = number(row.get("target_artificial_light_output"), 0)
    scheduled = eligible + fixed if eligible is not None and fixed is not None else None
    decision = {
        "tier": "review", "reduction_pct": None, "action": "Review with the building operator",
        "start_utc": None, "end_utc": None, "start_local": None, "end_local": None,
        "reasons": [], "basis": "operator light-output budget",
        "recommendation_status": None, "eligible_zones": zones,
        "scheduled_artificial_light_output": scheduled if scheduled is not None and isfinite(scheduled) else None,
        "target_artificial_light_output": target, "projected_artificial_light_output": None,
        "light_output_unit": unit or None, "approved_light_scenarios": [],
    }
    if row.get("in_program") is not True:
        return {**decision, "tier": "not_in_program", "recommendation_status": "not_in_program",
                "action": "Outside the lighting program",
                "reasons": ["Building is outside the lighting program"]}
    if row.get("risk_priority") not in ("high", "medium", "low"):
        return _review(decision, "missing_risk", "Collision priority is unavailable for this night")

    approved = _approved(row.get("approved_reductions_pct"))
    if not zones or not unit or not approved or target is None or decision["scheduled_artificial_light_output"] is None:
        return _review(decision, "missing_operator_settings",
                       "Enter eligible zones, approved reductions, and a complete light budget in one unit")

    scenarios = [{"reduction_pct": pct, "projected_artificial_light_output": fixed + eligible * ((100 - pct) / 100)}
                 for pct in approved]
    decision["approved_light_scenarios"] = scenarios
    best = next((scenario for scenario in scenarios
                 if scenario["projected_artificial_light_output"] <= target
                 or isclose(scenario["projected_artificial_light_output"], target, rel_tol=1e-12)), None)
    if best is None:
        minimum = scenarios[-1]["projected_artificial_light_output"]
        return _review(decision, "cap_unmet",
                       f"The lowest approved output is {minimum:g} {unit} and the cap is {target:g} {unit}")

    pct = best["reduction_pct"]
    projected = best["projected_artificial_light_output"]
    if pct == 0:
        return {**decision, **best, "tier": "none", "recommendation_status": "cap_met",
                "action": "Keep the scheduled output",
                "reasons": [f"Scheduled output of {projected:g} {unit} meets the {target:g} {unit} cap"]}
    window = _twilight(row)
    if window is None:
        return _review(decision, "missing_twilight", "Civil dusk and dawn are required to schedule the reduction")

    tier = "off" if pct == 100 else "dim" if pct >= 50 else "reduce"
    return {
        **decision, **best, **window, "tier": tier, "recommendation_status": "scheduled",
        "action": f"Reduce output in approved zones by {pct}%",
        "reasons": [
            f"{pct}% is the smallest approved reduction that meets the {target:g} {unit} cap",
            f"Projected output is {projected:g} {unit}, including {fixed:g} {unit} of fixed lighting",
        ],
    }
