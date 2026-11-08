"""Train a retrospective bird-collision risk model on the Dryad baseline."""

import argparse
import csv
from datetime import date
from pathlib import Path

# scikit-learn trains and evaluates the model. Joblib saves the fitted pipeline.
from joblib import dump
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


# These observed values describe the night that preceded the morning survey.
WEATHER_AND_MIGRATION = (
    "observed_migration_birds_per_km_h",
    "observed_visibility_m",
    "observed_cloud_ceiling_m",
    "observed_wind_u_m_s",
    "observed_wind_v_m_s",
    "observed_temperature_c",
)

# These columns are available in the enriched export when their sources were joined.
BUILDING_AND_LIGHT = (
    "roof_height_m",
    "footprint_area_m2",
    "moon_illum_fraction",
    "moon_above_horizon_minutes",
    "observed_neighborhood_radiance",
)


def features(row, numeric_columns):
    """Convert a CSV row to numeric inputs in the order saved with the model."""
    # Leave blanks as missing so the pipeline can fill them with training medians.
    values = [float(row[column]) if row[column].strip() else float("nan")
              for column in numeric_columns]
    return [*values, int(row["season"] == "fall")]


def targets(rows):
    """Mark surveys that found one or more collisions."""
    return [int(row["observed_collision_count"]) > 0 for row in rows]


def main():
    # Set defaults for the local input CSV, the first test night, and the saved model.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/processed/baseline.csv"))
    parser.add_argument("--test-start", type=date.fromisoformat, default=date(2021, 1, 1))
    parser.add_argument("--output", type=Path, default=Path("data/processed/collision_model.joblib"))
    args = parser.parse_args()

    train_rows, test_rows = [], []
    try:
        # Ingestion already created this file. Training reads it locally.
        with args.input.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                # A blank count is unknown. A recorded zero is a valid example.
                if not row["observed_collision_count"].strip():
                    continue
                night = date.fromisoformat(row["night_date"])
                # Keep every survey from the same night on one side of the split.
                if night < args.test_start:
                    train_rows.append(row)
                else:
                    test_rows.append(row)
        # Separate the survey outcomes (y) from the model inputs (x).
        y_train, y_test = targets(train_rows), targets(test_rows)
        # Use only optional features with values in training. Test data cannot pick features.
        optional_columns = tuple(column for column in BUILDING_AND_LIGHT
                                 if any(row.get(column, "").strip() for row in train_rows))
        numeric_columns = WEATHER_AND_MIGRATION + optional_columns
        x_train = [features(row, numeric_columns) for row in train_rows]
        x_test = [features(row, numeric_columns) for row in test_rows]
    except (OSError, KeyError, ValueError) as error:
        parser.exit(1, f"Could not read {args.input}: {error}\n")

    # Both periods need examples of surveys with and without a collision.
    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        parser.error("Training and test periods must both include positive and zero-collision surveys.")

    # Learn how to fill blanks and scale the inputs using the training period only.
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression())
    model.fit(x_train, y_train)
    # Take the probability of a positive survey from each test prediction.
    probabilities = model.predict_proba(x_test)[:, 1]

    # Compare with historical collision rates for each season, learned from training only.
    seasonal_rates = {}
    for season in {row["season"] for row in train_rows}:
        outcomes = [outcome for row, outcome in zip(train_rows, y_train) if row["season"] == season]
        seasonal_rates[season] = sum(outcomes) / len(outcomes)
    overall_rate = sum(y_train) / len(y_train)
    baseline = [seasonal_rates.get(row["season"], overall_rate) for row in test_rows]

    print(f"Training surveys: {len(y_train):,}. Test surveys from {args.test_start}: {len(y_test):,}.")
    print(f"Building and light inputs used: {', '.join(optional_columns) or 'none'}.")
    print(f"Positive test surveys: {sum(y_test):,} ({sum(y_test) / len(y_test):.1%}).")
    # Higher average precision means better ranking. Lower Brier score means less probability error.
    for name, scores in [("Logistic regression", probabilities), ("Seasonal baseline", baseline)]:
        print(f"{name}: average precision {average_precision_score(y_test, scores):.3f}, "
              f"Brier score {brier_score_loss(y_test, scores):.3f}")

    # Save preprocessing and model together, with the expected order of inputs.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    dump({"model": model, "feature_names": (*numeric_columns, "is_fall")}, args.output)
    print(f"Saved retrospective model to {args.output}")


if __name__ == "__main__":
    main()
