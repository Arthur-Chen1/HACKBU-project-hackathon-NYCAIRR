"""Gradient-boosted (machine-learning) forecast of morning-survey collision counts.

This model replaces the forecast-mode Poisson regression. It produces the same outputs, an expected
collision count per building-night plus the features used and the main factors, from inputs that
are available one hour before sunset.

Training data. No labeled pre-sunset forecast history exists yet (HRRR runs and issued migration
forecasts joined to survey counts). The model therefore trains on the study's observed nights and
treats each observed value as a perfect forecast of itself:

    forecast feature at scoring          observed training proxy
    forecast_migration_birds_per_km_h    observed_migration_birds_per_km_h (Dryad KOKX)
    forecast_visibility_m                observed_visibility_m (LaGuardia)
    forecast_cloud_ceiling_m             observed_cloud_ceiling_m (LaGuardia, 22,000 m = unlimited)
    forecast_wind_u_m_s / _v_m_s         observed_wind_u_m_s / _v_m_s (LaGuardia)

Season, day of year, and building traits are known in advance. Temperature, cloud cover, and
precipitation are left out because one side of the mapping lacks them. Forecast errors are absent
from training, so held-out scores here are an upper bound on forecast skill.

Estimator. scikit-learn HistGradientBoostingRegressor with a Poisson loss. Trees learn interactions
such as migration x low ceiling and seasonal timing without hand-written terms, and handle the
nonlinear building effect. Expected counts rise monotonically with migration traffic and roof
height. Building fits model collisions per 100 m of facade, as the Poisson profiles do:

    expected count = (facade_m / 100) x tree-ensemble rate(features)

Training uses the surveyed facade length and scoring uses the footprint perimeter. A building
without traits or a perimeter falls back to the night-level fit.

Extrapolation. Trees predict a flat value beyond the training range, but most candidate buildings
are shorter and smaller than the 21 study towers. Outside the range of each feature, the model
evaluates the trees at the range edge and continues with the slope of a Poisson regression fit to
the same rows (linear tails), so a 15 m building scores below a 27 m study site as it does in the
regression, and migration heavier than any study night still raises the estimate.
"""

from collections import Counter
from dataclasses import dataclass, replace
from math import ceil, exp, isfinite, log, log1p
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from src.features.model_inputs import forecast_issues, local_night, number, season_for
from src.features.training import write_json
from src.models.count import (
    TEST_FRACTION, Example, _fit as fit_poisson, _log_rate as poisson_log_rate, _metrics, _summary, exposure,
)

DEFAULT_FORECAST_MODEL = Path("data/processed/models/forecast.joblib")
ARTIFACT_VERSION = 1
FEATURE_VERSION = 4  # 2: linear tails. 3: roof height is the only building feature. 4: migration smoothing.
BASIS = "forecast AI model (gradient-boosted trees)"
# Dryad codes an unlimited ceiling as 22,000 m. HRRR leaves the ceiling blank for a clear grid cell.
CLEAR_SKY_CEILING_M = 22_000.0
# LaGuardia's ASOS station reports visibility up to 10 statute miles; HRRR forecasts go past 20 km.
# Beyond 10 miles the training data cannot tell visibilities apart, so forecasts are capped there.
STATION_VISIBILITY_CAP_M = 16_093.44
PROXIES = {
    "forecast_migration_birds_per_km_h": "observed_migration_birds_per_km_h",
    "forecast_visibility_m": "observed_visibility_m",
    "forecast_cloud_ceiling_m": "observed_cloud_ceiling_m",
    "forecast_wind_u_m_s": "observed_wind_u_m_s",
    "forecast_wind_v_m_s": "observed_wind_v_m_s",
}
NIGHT_FEATURES = ("season_fall", "day_of_year", *PROXIES)
# Footprint area is left out: facade exposure already counts building size, the regression found its
# effect near zero, and with 21 study buildings, tree splits on area mostly pick out individual sites.
BUILDING_FEATURES = ("roof_height_m",)
SIGNED = {"forecast_wind_u_m_s", "forecast_wind_v_m_s"}
LOG = {"forecast_migration_birds_per_km_h", "roof_height_m"}
INCREASING = {"forecast_migration_birds_per_km_h", "roof_height_m"}
# Trees respond to migration in steps. Each estimate averages the rate over migration offsets in log
# space (0.75 = a factor of about 2 either way, narrower than
# the migration estimate's typical error of about 3x), which turns the steps into a smooth increase.
MIGRATION = "forecast_migration_birds_per_km_h"
MIGRATION_SMOOTHING_LOG = 0.75
MIGRATION_SMOOTHING_POINTS = 9
# Chosen on the held-out splits (MODEL_RUNBOOK.md): small trees with strong leaf limits, because
# 21 buildings and ~560 nights are easy to overfit.
HYPERPARAMETERS = {
    "loss": "poisson", "learning_rate": 0.02, "max_iter": 400, "max_leaf_nodes": 7,
    "min_samples_leaf": 80, "l2_regularization": 2.0, "early_stopping": False, "random_state": 0,
}
PROFILES = (("footprint", NIGHT_FEATURES + BUILDING_FEATURES, True), ("base", NIGHT_FEATURES, False))


def features(row, source="forecast"):
    """Model inputs for a row. source="observed" reads the training proxies for the forecast fields."""
    night = local_night(row.get("night_date"))
    season = season_for(row)
    values = {"season_fall": float(season == "fall") if season else None,
              "day_of_year": float(night.timetuple().tm_yday) if night else None}
    for name, proxy in PROXIES.items():
        values[name] = number(row.get(proxy if source == "observed" else name), None if name in SIGNED else 0)
    if source == "forecast":
        if values["forecast_cloud_ceiling_m"] is None:
            values["forecast_cloud_ceiling_m"] = CLEAR_SKY_CEILING_M
        if values["forecast_visibility_m"] is not None:
            values["forecast_visibility_m"] = min(values["forecast_visibility_m"], STATION_VISIBILITY_CAP_M)
    matched = row.get("feature_location") == "matched_footprint" and row.get("bin")
    for name in BUILDING_FEATURES:
        # NYC footprints record an unknown roof height as 0: missing, not a 0 m building.
        value = number(row.get(name), 0) if matched else None
        values[name] = value or None
    return {name: log1p(value) if value is not None and name in LOG else value for name, value in values.items()}


@dataclass(frozen=True)
class Fit:
    name: str
    feature_names: tuple
    exposure_offset: bool
    estimator: HistGradientBoostingRegressor
    reference: tuple  # Training medians; attributions compare each feature with this typical value.
    lower: tuple  # Training range of each transformed feature.
    upper: tuple
    slopes: tuple  # Poisson regression log-rate slope per transformed unit, used outside the range.
    smoothing_scale: float = 1.0  # Restores the training total after migration smoothing.

    def _rates(self, matrix):
        """Rate per exposure unit: trees inside the training range, log-linear tails outside it."""
        clipped = np.clip(matrix, self.lower, self.upper)
        return self.estimator.predict(clipped) * np.exp((matrix - clipped) @ np.asarray(self.slopes))

    def rates(self, matrix):
        """Rates averaged over nearby migration levels, so a small forecast change moves the estimate."""
        matrix = np.asarray(matrix, dtype=float)
        if MIGRATION not in self.feature_names or not MIGRATION_SMOOTHING_LOG or not len(matrix):
            return self._rates(matrix) * self.smoothing_scale
        column = self.feature_names.index(MIGRATION)
        offsets = np.linspace(-MIGRATION_SMOOTHING_LOG, MIGRATION_SMOOTHING_LOG, MIGRATION_SMOOTHING_POINTS)
        spread = np.repeat(matrix, len(offsets), axis=0)
        spread[:, column] = np.maximum(spread[:, column] + np.tile(offsets, len(matrix)), 0.0)
        return self._rates(spread).reshape(len(matrix), len(offsets)).mean(axis=1) * self.smoothing_scale


@dataclass(frozen=True)
class Score:
    expected: float | None
    features: tuple
    contributions: tuple  # (feature, log-rate effect), largest absolute effect first
    missing: tuple


class ForecastModel:
    """Scores forecast building-nights with the same interface as the Poisson `Model`."""

    mode = "forecast"
    basis = BASIS

    def __init__(self, fits, report):
        self.fits = tuple(fits)
        self.report = report

    def _missing(self, row, values):
        missing = [name for name in NIGHT_FEATURES if values[name] is None]
        return [*missing, *forecast_issues(row)]

    def score_rows(self, rows):
        """Score many rows with one ensemble call per fit, which keeps borough runs fast."""
        rows = list(rows)
        results = [None] * len(rows)
        batches = {fit.name: [] for fit in self.fits}
        for index, row in enumerate(rows):
            values = features(row)
            missing = self._missing(row, values)
            if missing:
                results[index] = Score(None, (), (), tuple(dict.fromkeys(missing)))
                continue
            facade = exposure(row)
            fit = next(fit for fit in self.fits if all(values[name] is not None for name in fit.feature_names)
                       and (facade or not fit.exposure_offset))
            batches[fit.name].append((index, [values[name] for name in fit.feature_names],
                                      facade if fit.exposure_offset else 1.0))
        for fit in self.fits:
            batch = batches[fit.name]
            if not batch:
                continue
            matrix = np.array([values for _, values, _ in batch], dtype=float)
            width = len(fit.feature_names)
            # Occlusion attribution: swap one feature at a time for its typical training value.
            swapped = np.repeat(matrix, width, axis=0)
            columns = np.tile(np.arange(width), len(batch))
            swapped[np.arange(len(swapped)), columns] = np.asarray(fit.reference)[columns]
            rates = np.maximum(fit.rates(np.vstack([matrix, swapped])), 1e-12)
            base, alternatives = np.log(rates[:len(batch)]), np.log(rates[len(batch):]).reshape(len(batch), width)
            for position, (index, _, facade) in enumerate(batch):
                log_rate = base[position] + log(facade)
                if not isfinite(log_rate) or log_rate > 700:
                    results[index] = Score(None, (), (), ("model output out of range",))
                    continue
                effects = list(zip(fit.feature_names, (base[position] - alternatives[position]).tolist()))
                if fit.exposure_offset:
                    effects.append(("facade_length_per_100m", log(facade)))
                results[index] = Score(exp(log_rate), fit.feature_names,
                                       tuple(sorted(effects, key=lambda pair: -abs(pair[1]))), ())
        return results

    def predict(self, row):
        return self.score_rows([row])[0].expected

    def missing_inputs(self, row):
        return list(self.score_rows([row])[0].missing)

    def features_for(self, row):
        return self.score_rows([row])[0].features

    def contributions(self, row):
        return list(self.score_rows([row])[0].contributions)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"artifact_version": ARTIFACT_VERSION, "feature_version": FEATURE_VERSION, "mode": self.mode,
                     "fits": [fit.__dict__ for fit in self.fits], "report": self.report}, path)
        # A readable copy of the report sits beside the binary model.
        write_json(path.with_suffix(".report.json"), self.report)


def load_forecast_model(path):
    """Load a trained artifact. joblib files can run code when loaded: only open models you trained."""
    data = joblib.load(Path(path))
    if (not isinstance(data, dict) or data.get("artifact_version") != ARTIFACT_VERSION
            or data.get("feature_version") != FEATURE_VERSION or data.get("mode") != "forecast"):
        raise ValueError("Unsupported forecast AI model version; retrain with python -m src.models train --mode forecast")
    fits = [Fit(**fit) for fit in data["fits"]]
    if not fits or fits[-1].feature_names != NIGHT_FEATURES or fits[-1].exposure_offset:
        raise ValueError("Forecast AI model needs a night-level fallback fit")
    return ForecastModel(fits, data["report"])


def _examples(rows):
    examples, seen = [], set()
    for row in rows:
        night = local_night(row.get("night_date"))
        site = str(row.get("study_building_id") or "").strip()
        if not night or not site:
            raise ValueError("Training rows require study_building_id and an ISO night_date")
        if (site, night) in seen:
            raise ValueError(f"Duplicate study building-night {site} on {night}")
        seen.add((site, night))
        label = row.get("observed_collision_count")
        if label in (None, ""):
            continue
        count = number(label, 0)
        if count is None or not count.is_integer():
            raise ValueError(f"Invalid collision count for {site} on {night}")
        examples.append(Example(night, site, season_for(row), int(count), features(row, "observed"), (), exposure(row)))
    return sorted(examples, key=lambda example: (example.night, example.site))


def _fit_trees(rows, feature_names, use_exposure):
    matrix = np.array([[row.features[name] for name in feature_names] for row in rows], dtype=float)
    weights = np.array([row.exposure if use_exposure else 1.0 for row in rows])
    counts = np.array([row.count for row in rows], dtype=float)
    estimator = HistGradientBoostingRegressor(
        **HYPERPARAMETERS, monotonic_cst=[int(name in INCREASING) for name in feature_names])
    # Poisson loss on count / exposure, weighted by exposure, equals a Poisson fit with a log offset.
    estimator.fit(matrix, counts / weights, sample_weight=weights)
    linear = fit_poisson(rows, feature_names, use_exposure)
    slopes = [coefficient / scale for coefficient, scale in zip(linear.coefficients, linear.scales)]
    # Keep the tails consistent with the monotonic constraints.
    slopes = [max(slope, 0.0) if name in INCREASING else slope for name, slope in zip(feature_names, slopes)]
    fit = Fit("", feature_names, use_exposure, estimator, tuple(np.median(matrix, axis=0).tolist()),
              tuple(matrix.min(axis=0).tolist()), tuple(matrix.max(axis=0).tolist()), tuple(slopes))
    # Averaging over a curve that flattens at the top lowers estimates; rescale so that, over the
    # training rows, smoothed expected collisions sum to the observed total (as the trees did).
    return replace(fit, smoothing_scale=float(counts.sum() / (fit.rates(matrix) * weights).sum()))


def _expected(fit, rows):
    matrix = np.array([[row.features[name] for name in fit.feature_names] for row in rows], dtype=float)
    return fit.rates(matrix) * np.array([row.exposure if fit.exposure_offset else 1.0 for row in rows])


def _evaluate(rows, feature_names, use_exposure):
    nights = sorted({row.night for row in rows})
    sites = sorted({row.site for row in rows})
    future = set(nights[-ceil(TEST_FRACTION * len(nights)):])
    unseen = set(sites[-ceil(TEST_FRACTION * len(sites)):])
    output = {}
    for name, held_out, attr in (("future_nights", future, "night"), ("unseen_buildings", unseen, "site")):
        test = [row for row in rows if getattr(row, attr) in held_out]
        train = [row for row in rows if getattr(row, attr) not in held_out]
        mean = float(np.mean([row.count for row in train]))
        seasonal = {season: float(np.mean([row.count for row in train if row.season == season]))
                    for season in {row.season for row in train}}
        poisson = fit_poisson(train, feature_names, use_exposure)
        output[name] = {
            "train": _summary(train), "test": _summary(test),
            "seasonal_mean": _metrics(test, [seasonal.get(row.season, mean) for row in test]),
            "poisson_same_features": _metrics(test, [exp(poisson_log_rate(poisson, row)) for row in test]),
            "gradient_boosting": _metrics(test, _expected(_fit_trees(train, feature_names, use_exposure), test)),
        }
    return output


def train_forecast(rows):
    """Fit and evaluate the forecast AI model on observed study rows used as perfect-forecast proxies."""
    examples = _examples(rows)
    report = {
        "model": BASIS,
        "estimator": "sklearn.ensemble.HistGradientBoostingRegressor",
        "hyperparameters": HYPERPARAMETERS,
        "monotonic_increasing": sorted(INCREASING),
        "migration_smoothing": {"log_half_width": MIGRATION_SMOOTHING_LOG, "points": MIGRATION_SMOOTHING_POINTS},
        "extrapolation": "Trees inside each feature's training range; Poisson-regression slopes beyond it",
        "training_proxies": PROXIES,
        "clear_sky_ceiling_m": CLEAR_SKY_CEILING_M,
        "forecast_visibility_cap_m": STATION_VISIBILITY_CAP_M,
        "feature_transforms": {name: "log1p" for name in sorted(LOG)},
        "labeled_input": _summary(examples),
        "count_distribution": dict(sorted(Counter(row.count for row in examples).items())),
        "holdout_fraction": TEST_FRACTION,
        "limitations": ("Trained on observed conditions as perfect forecasts; real forecast error is not in the "
                        "training data, so held-out skill is an upper bound for pre-sunset use."),
        "profiles": {},
    }
    fits = []
    for name, feature_names, use_exposure in PROFILES:
        eligible = [row for row in examples if all(row.features[f] is not None for f in feature_names)
                    and (row.exposure or not use_exposure)]
        profile = report["profiles"][name] = {"features": feature_names, "exposure_offset": use_exposure,
                                              "eligible": _summary(eligible)}
        if len({row.night for row in eligible}) < 2 or len({row.site for row in eligible}) < 2:
            reason = "Need complete labeled rows from at least two different nights and two study buildings"
            if name == "base":
                raise ValueError(f"Forecast AI model cannot be trained. {reason}; found {len(eligible)} eligible rows.")
            profile["skipped"] = reason
            continue
        profile["evaluation"] = _evaluate(eligible, feature_names, use_exposure)
        fit = replace(_fit_trees(eligible, feature_names, use_exposure), name=name)
        profile["smoothing_scale"] = fit.smoothing_scale
        fits.append(fit)
    return ForecastModel(fits, report)
