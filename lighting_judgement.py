"""Turn scored building-nights into lighting recommendations and exports.

The command uses the trained observed model and operator light budget. The legacy `judge`
and `judge_all` helpers remain available for comparisons with the original radar rules.

The original radar rules assign points:

    Night migration level (radar)   high +2, medium +1, unknown +1, low = no action
    Weather (optional)              low visibility or low cloud ceiling +1
    Building                        tall tower +1, large footprint +1
    Neighborhood birds (radar)      3x3-cell density >= 1.25x typical +1, <= 0.75x typical -1

    Score 4+ -> "off"     turn off nonessential lighting (100% reduction)
    Score 3  -> "dim"     50% reduction
    Score 2  -> "reduce"  25% reduction
    Below 2  -> "none"

The radar decides whether and when to act; building traits decide who acts most. Radar cells are
too coarse to rank neighboring buildings, so neighborhood density only nudges the score.
Emergency, egress, and other required lighting is always outside the reduction.

Usage:
    python lighting_judgement.py --night 2021-09-10 --area midtown
    python lighting_judgement.py --night 2021-09-10 --weather dryad --fetch

Functions
---------
night_weather(night, source, sunset_utc=None, sunrise_utc=None, cache=Path("data/raw")) -> dict | None
    Weather for the night, or None if unavailable. source "dryad" gives observed LaGuardia weather
    (study nights 2017-2021 only, so after the fact). source "hrrr" gives the NOAA forecast issued
    before sunset, which is what a live alert would have. Returns
    {"source": str, "visibility_m": float | None, "cloud_ceiling_m": float | None}.

score(row, weather=None) -> (int | None, list[str])
    Points for one building-night row (from building_analysis.attach_migration) and the reason
    behind each point. Returns (None, [reason]) for buildings outside the lighting program.

judge(row, weather=None) -> dict
    The decision for one building:
    {
      "bin": str, "name": str | None, "latitude": float, "longitude": float,
      "tier": "off" | "dim" | "reduce" | "none" | "not_in_program",
      "reduction_pct": int,                # share of nonessential lighting to cut
      "action": str,                       # plain-language instruction
      "score": int | None,
      "start_utc": datetime | None, "end_utc": datetime | None,     # None when no action
      "start_local": str | None, "end_local": str | None,          # e.g. "7:13 PM"
      "peak_window_utc": (datetime, datetime) | None,              # densest migration hours
      "reasons": list[str],
      "basis": "demo rules",
    }

judge_all(rows, weather=None, *, include_out_of_program=False) -> list[dict]
    judge() for every row, sorted by score (highest first), then height (tallest first).

write(decisions, rows, name) -> (csv_path, geojson_path)
    Saves to data/processed/judgements/<name>.csv and <name>.geojson (footprints with tiers).

main() -> None
    Command-line interface: prints tier counts and the top buildings with their reasons.
"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from math import inf
from pathlib import Path
from zoneinfo import ZoneInfo

import building_analysis
from src.features.training import write_csv, write_json
from src.recommendations.dimming import load_operator_settings, recommend

NYC = ZoneInfo("America/New_York")
OUTPUT_DIR = Path("data/processed/judgements")

# A high night alone earns "reduce"; building and neighborhood factors escalate from there, so
# full blackouts are reserved for the buildings that matter most.
NIGHT_POINTS = {"high": 2, "medium": 1, "unknown": 1, "low": 0}
LOW_VISIBILITY_M = 8_000       # ~5 miles; Dryad and HRRR cap clear visibility at ~16 km
LOW_CEILING_M = 1_000          # low clouds push migrants down toward lit buildings
LARGE_FOOTPRINT_M2 = 5_000     # roughly a full-block commercial building
DENSE_NEIGHBORHOOD = 1.25
SPARSE_NEIGHBORHOOD = 0.75


@dataclass(frozen=True)
class Tier:
    name: str
    min_score: float
    reduction_pct: int
    action: str


TIERS = (
    Tier("off", 4, 100, "Turn off all nonessential exterior and interior lighting"),
    Tier("dim", 3, 50, "Dim nonessential lighting by 50% and draw shades where possible"),
    Tier("reduce", 2, 25, "Reduce nonessential lighting by 25%; turn off decorative and spire lighting"),
    Tier("none", -inf, 0, "No change needed"),
)


def night_weather(night, source, sunset_utc=None, sunrise_utc=None, cache=Path("data/raw")):
    if source in (None, "none"):
        return None
    from src.ingest.http import Cache

    store = Cache(cache)
    try:
        if source == "dryad":
            from src.ingest import dryad

            row = next((r for r in dryad.surveys(store) if r["night_date"] == night), None)
            if row is None:
                return None
            return {"source": "Dryad observed (LaGuardia)", "visibility_m": row["observed_visibility_m"],
                    "cloud_ceiling_m": row["observed_cloud_ceiling_m"]}
        if source == "hrrr":
            from src.ingest import hrrr
            from src.ingest.nyc import NYC_POINT

            forecast = hrrr.weather_forecast(store, sunset_utc, sunrise_utc, {None: NYC_POINT})[None]
            if not forecast["hrrr_required_field_hours"]:
                return None
            return {"source": f"HRRR forecast, run {forecast['hrrr_run_utc']:%Y-%m-%d %H}Z",
                    "visibility_m": forecast["forecast_visibility_m"],
                    "cloud_ceiling_m": forecast["forecast_cloud_ceiling_m"]}
        raise ValueError(f"Unknown weather source {source!r}")
    finally:
        store.session.close()


def score(row, weather=None):
    if not row["in_program"]:
        return None, ["Not a commercial or mixed-use building in the lighting program"]

    level = row.get("night_level", "unknown")
    traffic = row.get("night_traffic_birds_per_km_h")
    points = NIGHT_POINTS[level]
    if level == "unknown":
        reasons = [f"+{points} Radar unavailable tonight (often rain, when grounded migrants are most at "
                   "risk); precautionary"]
    else:
        reasons = [f"+{points} {level.capitalize()} migration tonight ({traffic:,.0f} birds/km/h)"]
    if level == "low":
        return 0, reasons + ["Low migration: no reduction needed tonight"]

    if weather:
        bad = []
        if weather["visibility_m"] is not None and weather["visibility_m"] < LOW_VISIBILITY_M:
            bad.append(f"visibility {weather['visibility_m'] / 1000:.1f} km")
        if weather["cloud_ceiling_m"] is not None and weather["cloud_ceiling_m"] < LOW_CEILING_M:
            bad.append(f"cloud ceiling {weather['cloud_ceiling_m']:.0f} m")
        if bad:
            points += 1
            reasons.append(f"+1 Poor flying weather ({', '.join(bad)}; {weather['source']})")

    if row["height_class"] == "tall":
        points += 1
        reasons.append(f"+1 Tall tower ({row['roof_height_m']:.0f} m) reaches into the altitudes most "
                       "migrants fly at and is visible from far away")
    if row["footprint_area_m2"] >= LARGE_FOOTPRINT_M2:
        points += 1
        reasons.append(f"+1 Large building ({row['footprint_area_m2']:,.0f} m2 footprint), "
                       "so a large lit area")

    density = row.get("neighborhood_relative_density")
    if density is None:
        reasons.append("+0 No radar cell covers this building; neighborhood not adjusted")
    elif density >= DENSE_NEIGHBORHOOD:
        points += 1
        reasons.append(f"+1 More birds than usual over this neighborhood ({density:.1f}x typical)")
    elif density <= SPARSE_NEIGHBORHOOD:
        points -= 1
        reasons.append(f"-1 Fewer birds than usual over this neighborhood ({density:.1f}x typical)")
    return points, reasons


def _local(when):
    return when.astimezone(NYC).strftime("%I:%M %p").lstrip("0") if when else None


def judge(row, weather=None):
    points, reasons = score(row, weather)
    if points is None:
        tier = Tier("not_in_program", inf, 0, "Not covered by the lighting program")
    else:
        tier = next(t for t in TIERS if points >= t.min_score)
    acting = tier.reduction_pct > 0
    # Migrants take off ~30-60 min after sunset and land around dawn, so cover the whole night.
    start, end = (row.get("sunset_utc"), row.get("sunrise_utc")) if acting else (None, None)
    peak = (row.get("active_window_start_utc"), row.get("active_window_end_utc"))
    return {
        "bin": row["bin"], "name": row.get("name"),
        "latitude": row["latitude"], "longitude": row["longitude"],
        "tier": tier.name, "reduction_pct": tier.reduction_pct, "action": tier.action, "score": points,
        "start_utc": start, "end_utc": end, "start_local": _local(start), "end_local": _local(end),
        "peak_window_utc": peak if acting and all(peak) else None,
        "reasons": reasons,
        "basis": "demo rules",
    }


def judge_all(rows, weather=None, *, include_out_of_program=False):
    by_bin = {row["bin"]: row for row in rows}
    decisions = [judge(row, weather) for row in rows if include_out_of_program or row["in_program"]]
    return sorted(decisions, key=lambda d: (-(d["score"] if d["score"] is not None else -inf),
                                            -(by_bin[d["bin"]]["roof_height_m"] or 0)))


def model_decisions(rows, settings):
    decisions = []
    for row in rows:
        decision = recommend({**row, **settings.get(row["bin"], {})})
        decisions.append({
            **decision, "bin": row["bin"], "bbl": row.get("bbl"), "name": row.get("name"),
            "night_date": row["night_date"],
            "latitude": row["latitude"], "longitude": row["longitude"],
            "score": row["expected_collision_count"], "expected_collision_count": row["expected_collision_count"],
            "risk_priority": row["risk_priority"], "risk_basis": row["risk_basis"],
            "risk_features": row["risk_features"], "decision_at_utc": row.get("decision_at_utc"),
            "priority_rank": row.get("priority_rank"),
            "priority_scored_buildings": row.get("priority_scored_buildings", 0),
            "priority_basis": row.get("priority_basis"),
            **{key: row.get(key) for key in ("suggested_dimming_pct", "dimming_band", "dimming_rank",
                                            "dimming_scored_buildings", "dimming_basis", "dimming_policy_version")},
            "peak_window_utc": None, "reasons": [*row["risk_reasons"], *decision["reasons"]],
        })
    rank = {"high": 0, "medium": 1, "low": 2, "unavailable": 3}
    return sorted(decisions, key=lambda d: (rank[d["risk_priority"]], -(d["score"] or 0), d["bin"]))


def write(decisions, rows, name):
    geometry = {row["bin"]: row["geometry"] for row in rows}
    flat = [{**{k: v for k, v in d.items() if k != "peak_window_utc"},
             "peak_start_utc": d["peak_window_utc"] and d["peak_window_utc"][0],
             "peak_end_utc": d["peak_window_utc"] and d["peak_window_utc"][1],
             "reasons": " | ".join(d["reasons"])} for d in decisions]
    csv_path, geojson_path = OUTPUT_DIR / f"{name}.csv", OUTPUT_DIR / f"{name}.geojson"
    write_csv(csv_path, [{key: json.dumps(value) if isinstance(value, (list, tuple)) else value
                          for key, value in decision.items()} for decision in flat])
    write_json(geojson_path, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": geometry[d["bin"]], "properties": d} for d in flat]})
    return csv_path, geojson_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\nUsage:")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--night", type=date.fromisoformat, required=True, help="Local night, YYYY-MM-DD")
    parser.add_argument("--area", choices=building_analysis.AREAS, default="pilot", help="Preset map extent")
    parser.add_argument("--weather", choices=["none", "dryad", "hrrr"], default="dryad",
                        help="Add weather: Dryad observed (study nights) or HRRR forecast")
    parser.add_argument("--fetch", action="store_true", help="Run the radar pipeline if the night is missing")
    parser.add_argument("--top", type=int, default=8, help="How many buildings to print")
    parser.add_argument("--model", type=Path, help="Override the observed model artifact")
    parser.add_argument("--operator-settings", type=Path, help="Operator lighting budget CSV")
    args = parser.parse_args()

    rows = building_analysis.prepare(args.night, building_analysis.AREAS[args.area], fetch=args.fetch,
                                     weather=args.weather, model_path=args.model)
    first = rows[0] if rows else {}
    settings = load_operator_settings(args.operator_settings, args.night) if args.operator_settings else {}
    decisions = model_decisions(rows, settings)
    print(f"Night of {args.night}, {args.area}: {first.get('night_level', 'n/a')} migration")
    counts = Counter(d["tier"] for d in decisions)
    print("  " + ", ".join(f"{tier}: {count:,}" for tier, count in counts.items()))
    acting = [d for d in decisions if d["reduction_pct"]]
    if acting:
        print(f"  Window: {acting[0]['start_local']} to {acting[0]['end_local']} local time")
    for d in decisions[:args.top]:
        expected = d["expected_collision_count"]
        count = f"{expected:.3f}" if expected is not None else "unavailable"
        print(f"\n  BIN {d['bin']} {d['name'] or ''}  {count} expected collisions  [{d['tier']}]")
        for reason in d["reasons"]:
            print(f"           {reason}")

    csv_path, geojson_path = write(decisions, rows, f"{args.night}_{args.area}")
    print(f"\nWrote {csv_path} and {geojson_path}")


if __name__ == "__main__":
    main()
