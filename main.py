"""DarkSky Migrations: radar migration -> NYC buildings -> lighting judgement.

Press Run (or `python main.py`) to open the dashboard in your browser. Pick a period, an area,
and options; each night in the period is processed and judged, then shown on a map with a
per-night summary table, the migration timeline, and every building's reasons.

Terminal mode, for quick tests:
    python main.py --cli --start 2021-09-10 --area queens --weather dryad

The first run of a night downloads ~100 MB of NEXRAD radar (about a minute at hourly scans); later
runs read the cache in data/raw.

Functions
---------
nights(start, end) -> list[date]
    Every local night from start to end, inclusive.

run_night(night, area="manhattan", weather="none", minutes=60, log=print) -> dict
    Runs the whole pipeline for one night and returns
    {"night": date, "overview": dict, "decisions": list[dict], "rows": list[dict], "analysis": dict}.
    "overview" is the one-line summary shown in the table (see overview()). "decisions" come from
    lighting_judgement.judge_all, "rows" from building_analysis, "analysis" from migration_analysis.

run_period(start, end, area="manhattan", weather="none", minutes=60, log=print) -> list[dict]
    run_night() for every night; a failed night returns {"night", "error"} instead of stopping.

overview(analysis, decisions, weather) -> dict
    Night-level numbers plus counts of buildings in each tier.

main() -> None
    Opens the dashboard, or prints a table with --cli.
"""

import argparse
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # Data paths are relative to the project, wherever Run is pressed from.
sys.path.insert(0, str(ROOT))

try:
    import building_analysis
    import lighting_judgement
    import migration_analysis
except ImportError as error:
    sys.exit(f"Missing dependency ({error.name}). Install them with:\n"
             "  python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")

MAX_NIGHTS = 31
_buildings = {}  # Area -> buildings, so a multi-night period loads footprints once.


def nights(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _weather_text(weather, source):
    if source == "none":
        return "not used"
    if weather is None:
        return "unavailable"
    return (f"{weather['source']}: visibility {(weather['visibility_m'] or 0) / 1000:.1f} km, "
            f"ceiling {weather['cloud_ceiling_m'] or 0:,.0f} m")


def overview(analysis, decisions, weather, source="none"):
    n = analysis["night"]
    counts = Counter(d["tier"] for d in decisions)
    if n["scans_processed"] == 0:
        note = "No radar scans in the archive for this night"
    elif n["level"] == "unknown":
        note = "Radar profiles unusable (usually rain); precautionary rules applied"
    else:
        note = ""
    window = n["active_window_utc"]
    local = lighting_judgement._local
    return {
        "night": n["night_date"],
        "level": n["level"],
        "traffic_birds_per_km_h": n["mean_migration_traffic_birds_per_km_h"],
        "peak_birds_per_km2": n["peak_birds_per_km2"],
        "direction_deg": n["direction_deg"],
        "fraction_below_600m": n["fraction_below_600m"],
        "mean_height_m": n["mean_height_m"],
        "sunset_local": local(n["sunset_utc"]), "sunrise_local": local(n["sunrise_utc"]),
        "peak_local": local(n["peak_scan_utc"]),
        "active_window_local": f"{local(window[0])} to {local(window[1])}" if window else None,
        "scans_used": n["scans_used"], "scans_processed": n["scans_processed"],
        "weather": _weather_text(weather, source),
        "buildings": len(decisions),
        "tiers": {t.name: counts.get(t.name, 0) for t in lighting_judgement.TIERS},
        "note": note,
    }


def run_night(night, area=building_analysis.DEFAULT_AREA, weather="none", minutes=60, log=print):
    log(f"{night}: radar")
    migration_analysis.fetch(night, minutes)
    analysis = migration_analysis.analyze(night)
    if area not in _buildings:
        log(f"{night}: loading {area} buildings")
        borough = building_analysis.BOROUGH_CODES.get(area)
        bounds = building_analysis.AREAS["citywide" if borough else area]
        land_uses = building_analysis.LIGHTING_PROGRAM_LAND_USES if borough or area == "citywide" else None
        _buildings[area] = building_analysis.load_buildings(bounds, land_uses=land_uses, borough=borough)
    rows = building_analysis.attach_migration(_buildings[area], analysis)
    conditions = None
    if weather != "none":
        log(f"{night}: weather ({weather})")
        n = analysis["night"]
        conditions = lighting_judgement.night_weather(night, weather, n["sunset_utc"], n["sunrise_utc"])
    decisions = lighting_judgement.judge_all(rows, conditions)
    log(f"{night}: judged {len(decisions):,} buildings")
    return {"night": night, "overview": overview(analysis, decisions, conditions, weather),
            "decisions": decisions, "rows": rows, "analysis": analysis}


def run_period(start, end, area=building_analysis.DEFAULT_AREA, weather="none", minutes=60, log=print):
    results = []
    for night in nights(start, end):
        try:
            results.append(run_night(night, area, weather, minutes, log))
        except Exception as error:  # Keep going so one bad night doesn't sink the period.
            log(f"{night}: failed ({error})")
            results.append({"night": night, "error": str(error)})
    return results


def validate(start, end):
    if start > end:
        raise ValueError("Start must be on or before end")
    if end >= date.today():
        raise ValueError("Pick nights before today; the radar archive covers completed nights")
    if len(nights(start, end)) > MAX_NIGHTS:
        raise ValueError(f"Pick at most {MAX_NIGHTS} nights at a time")


def _print_table(results):
    print(f"\n{'Night':<11} {'Level':<8} {'Birds/km/h':>10} {'Dir':>5} {'<600m':>6}  "
          f"{'Off':>5} {'Dim':>5} {'Reduce':>6} {'None':>5}  Note")
    for result in results:
        if "error" in result:
            print(f"{result['night']!s:<11} error: {result['error']}")
            continue
        o = result["overview"]
        fmt = lambda x, spec: "-" if x is None else format(x, spec)
        print(f"{o['night']!s:<11} {o['level']:<8} {fmt(o['traffic_birds_per_km_h'], ',.0f'):>10} "
              f"{fmt(o['direction_deg'], '.0f'):>5} {fmt(o['fraction_below_600m'] and 100 * o['fraction_below_600m'], '.0f'):>5}%  "
              f"{o['tiers']['off']:>5} {o['tiers']['dim']:>5} {o['tiers']['reduce']:>6} {o['tiers']['none']:>5}  {o['note']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\nFunctions")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cli", action="store_true", help="Print results instead of opening the dashboard")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 9, 10))
    parser.add_argument("--end", type=date.fromisoformat, help="Last night, inclusive (default: --start)")
    parser.add_argument("--area", choices=building_analysis.REPLAY_AREAS, default=building_analysis.DEFAULT_AREA)
    parser.add_argument("--weather", choices=["none", "dryad", "hrrr"], default="none")
    parser.add_argument("--minutes", type=int, default=60, help="Radar scan interval (30 = finer, slower)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Start the dashboard without opening a browser")
    args = parser.parse_args()

    if not args.cli:
        from src.dashboard.server import serve
        serve(args.port, open_browser=not args.no_browser)
        return
    end = args.end or args.start
    try:
        validate(args.start, end)
    except ValueError as error:
        parser.error(str(error))
    results = run_period(args.start, end, args.area, args.weather, args.minutes)
    _print_table(results)
    for result in results:
        if "error" not in result:
            path, _ = lighting_judgement.write(result["decisions"], result["rows"], f"{result['night']}_{args.area}")
            print(f"Saved {path}")


if __name__ == "__main__":
    main()
