"""Train and evaluate a collision count model from building-night CSV files."""

import argparse
import csv
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path

from src.features.model_inputs import local_night
from src.models.count import DEFAULT_FORECAST_MODEL, DEFAULT_OBSERVED_MODEL, train

DEFAULT_INPUT = Path("data/processed/baseline.csv")


def _read_training(paths):
    merged = {}
    sources = []
    for path in paths:
        data = path.read_bytes()
        reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""))
        if not {"study_building_id", "night_date"} <= set(reader.fieldnames or ()):
            raise ValueError(f"{path} needs study_building_id and night_date columns")
        seen = set()
        for row in reader:
            if None in row or None in row.values():
                raise ValueError(f"Malformed CSV row in {path} at line {reader.line_num}")
            key = (row["study_building_id"].strip(), local_night(row["night_date"]))
            if not all(key):
                raise ValueError(f"Invalid building-night key in {path} at line {reader.line_num}")
            if key in seen:
                raise ValueError(f"Duplicate study building-night {key[0]} on {key[1]} in {path}")
            seen.add(key)
            saved = merged.setdefault(key, {"study_building_id": key[0], "night_date": key[1].isoformat()})
            for field, value in row.items():
                if field in ("study_building_id", "night_date") or value == "":
                    continue
                if field in saved and saved[field] != value:
                    raise ValueError(f"Conflicting {field} for {key[0]} on {key[1]} in {path}")
                saved[field] = value
        source = {"path": str(path), "sha256": sha256(data).hexdigest(), "rows": len(seen)}
        coverage_path = path.with_name("baseline_coverage.json" if path.name == "baseline.csv" else "coverage.json")
        if coverage_path.is_file():
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            for field in ("dryad_version_id", "weather_mode", "satellite_skipped"):
                if field in coverage:
                    source[field] = coverage[field]
        sources.append(source)
    return list(merged.values()), sources


def _join_sites(rows, path):
    """Attach static building traits (from `python -m src.ingest sites`) to every night of each study site."""
    data = path.read_bytes()
    traits = {}
    for row in csv.DictReader(StringIO(data.decode("utf-8-sig"), newline="")):
        site = (row.get("study_building_id") or "").strip()
        if not site or site in traits:
            raise ValueError(f"{path} needs one row per study_building_id")
        traits[site] = {key: value for key, value in row.items() if key != "study_building_id" and value != ""}
    for row in rows:
        if row["study_building_id"] in traits and row.get("feature_location") == "nyc_reference_point":
            # Replay rows built without a crosswalk used a shared NYC point; the verified match replaces it.
            del row["feature_location"]
        for field, value in traits.get(row["study_building_id"], {}).items():
            if row.get(field, value) != value:
                raise ValueError(f"Conflicting {field} for site {row['study_building_id']} in {path}")
            row[field] = value
    matched = sum(row["study_building_id"] in traits for row in rows)
    return {"path": str(path), "sha256": sha256(data).hexdigest(), "sites": len(traits), "rows_matched": matched}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("train", help="Fit a model and save held-out evaluation")
    command.add_argument("--mode", choices=("observed", "forecast"), default="observed")
    command.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Labeled building-night CSV")
    command.add_argument("--augment", type=Path, action="append", default=[],
                         help="Additional CSV to join by study building and night (repeatable)")
    command.add_argument("--sites", type=Path,
                         help="Study-site building traits from `python -m src.ingest sites` (enables per-building fits)")
    command.add_argument("--output", type=Path, help="Model and evaluation JSON path")
    args = parser.parse_args()
    output = args.output or (DEFAULT_OBSERVED_MODEL if args.mode == "observed" else DEFAULT_FORECAST_MODEL)
    try:
        rows, sources = _read_training([args.input, *args.augment])
        if args.sites:
            sources.append(_join_sites(rows, args.sites))
        model = train(rows, mode=args.mode)
        model.report["sources"] = sources
        model.save(output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Training failed: {error}\n")

    coverage = model.report["profiles"]["base"]["eligible"]
    print(f"Trained {args.mode} model on {coverage['rows']:,} building-nights across {coverage['nights']:,} nights.")
    print(f"Saved model and held-out evaluation to {output}")
    building = model.report["profiles"].get("footprint", {})
    if "evaluation" in building:
        held_out = building["evaluation"]["unseen_buildings"]
        print(f"Building-level fit: {building['eligible']['rows']:,} rows from {len(building['eligible']['sites'])} "
              f"sites. Unseen-building error {held_out['poisson']['mean_absolute_count_error']:.3f} vs "
              f"{held_out['base_poisson_same_rows']['mean_absolute_count_error']:.3f} without building traits.")
    else:
        print("No building-level fit: add --sites with a verified crosswalk to learn per-building differences.")


if __name__ == "__main__":
    main()
