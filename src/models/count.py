"""Fit regularized Poisson models to morning-survey collision counts."""

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from itertools import combinations
from math import ceil, exp, isfinite, log, log1p
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import xlogy

from src.features.model_inputs import (
    forecast_issues, local_night, natural_light_features, number, season_for, usable_radiance,
)
from src.features.training import write_json

DEFAULT_OBSERVED_MODEL = Path("data/processed/models/observed.json")
DEFAULT_FORECAST_MODEL = Path("data/processed/models/forecast.json")
ARTIFACT_VERSION = 1
FEATURE_VERSION = 1
L2 = 0.05
TEST_FRACTION = 0.2
ALERT_FRACTION = 0.1
MIN_RATE = 1e-9
NATURAL = ("night_hours", "potential_moonlit_hours")
FOOTPRINT = ("roof_height_m", "footprint_area_m2")
BASE = {
    "observed": (
        "season_fall", "observed_migration_birds_per_km_h", "observed_visibility_m",
        "observed_cloud_ceiling_m", "observed_wind_u_m_s", "observed_wind_v_m_s",
        "observed_temperature_c",
    ),
    "forecast": (
        "season_fall", "forecast_migration_birds_per_km_h", "forecast_visibility_m",
        "forecast_wind_u_m_s", "forecast_wind_v_m_s", "forecast_cloud_cover_pct",
        "forecast_precip_rate_mm_h", *NATURAL,
    ),
}
ENRICHMENTS = {
    "observed": {"natural_light": NATURAL, "footprint": FOOTPRINT,
                 "artificial_light": ("observed_neighborhood_radiance",)},
    "forecast": {"ceiling": ("forecast_cloud_ceiling_m",), "footprint": FOOTPRINT,
                 "artificial_light": ("prior_neighborhood_radiance",)},
}
LOG_FEATURES = {
    "observed_migration_birds_per_km_h", "forecast_migration_birds_per_km_h",
    "observed_neighborhood_radiance", "prior_neighborhood_radiance",
}
SIGNED_FEATURES = {
    "observed_wind_u_m_s", "observed_wind_v_m_s", "observed_temperature_c",
    "forecast_wind_u_m_s", "forecast_wind_v_m_s",
}


def _features(row, mode):
    season = season_for(row)
    features = {"season_fall": int(season == "fall") if season else None}
    features.update(natural_light_features(row))
    for feature in BASE[mode] + ("forecast_cloud_ceiling_m",):
        if feature not in features:
            features[feature] = number(row.get(feature), None if feature in SIGNED_FEATURES else 0,
                                       100 if feature == "forecast_cloud_cover_pct" else None)
    matched = row.get("feature_location") == "matched_footprint" and row.get("bin")
    for feature in FOOTPRINT:
        features[feature] = number(row.get(feature), 0) if matched else None
    radiance = "observed_neighborhood_radiance" if mode == "observed" else "prior_neighborhood_radiance"
    features[radiance] = usable_radiance(row, mode)
    return {name: log1p(value) if value is not None and name in LOG_FEATURES else value
            for name, value in features.items()}


@dataclass(frozen=True)
class PoissonFit:
    feature_names: tuple
    seasons: tuple
    means: tuple
    scales: tuple
    coefficients: tuple
    intercept: float

    def effects(self, features):
        return [(features[name] - mean) / scale * coef
                for name, mean, scale, coef in zip(self.feature_names, self.means, self.scales, self.coefficients)]


@dataclass(frozen=True)
class Model:
    mode: str
    fits: tuple
    report: dict

    @property
    def feature_names(self):
        return self.fits[0].feature_names

    def _inputs(self, row):
        features = _features(row, self.mode)
        missing = [name for name in BASE[self.mode] if features[name] is None]
        if self.mode == "forecast":
            missing.extend(forecast_issues(row))
        if not missing and season_for(row) not in self.fits[-1].seasons:
            missing.append("season")
        return features, missing

    def missing_inputs(self, row):
        return self._inputs(row)[1]

    def _fit_for(self, row):
        features, missing = self._inputs(row)
        if missing:
            return None, features
        season = season_for(row)
        fit = next(fit for fit in self.fits if season in fit.seasons
                   and all(features[name] is not None for name in fit.feature_names))
        return fit, features

    def features_for(self, row):
        fit, _ = self._fit_for(row)
        return fit.feature_names if fit else ()

    def contributions(self, row):
        fit, features = self._fit_for(row)
        if fit is None:
            return []
        return sorted(zip(fit.feature_names, fit.effects(features)), key=lambda pair: -abs(pair[1]))

    def predict(self, row):
        fit, features = self._fit_for(row)
        if fit is None:
            return None
        log_rate = fit.intercept + sum(fit.effects(features))
        if not isfinite(log_rate) or log_rate > 700:
            return None
        return exp(log_rate)

    def save(self, path):
        write_json(Path(path), {
            "artifact_version": ARTIFACT_VERSION, "feature_version": FEATURE_VERSION, **asdict(self),
        })


def load_model(path):
    with Path(path).open(encoding="utf-8") as file:
        data = json.load(file)
    if data.get("artifact_version") != ARTIFACT_VERSION or data.get("feature_version") != FEATURE_VERSION:
        raise ValueError("Unsupported collision model version")
    mode = data.get("mode")
    if mode not in BASE:
        raise ValueError("Unsupported collision model mode")
    fits = tuple(PoissonFit(
        tuple(fit["feature_names"]), tuple(fit["seasons"]), tuple(fit["means"]),
        tuple(fit["scales"]), tuple(fit["coefficients"]), fit["intercept"],
    ) for fit in data["fits"])
    allowed = {*BASE[mode], *(feature for group in ENRICHMENTS[mode].values() for feature in group)}
    if not fits or fits[-1].feature_names != BASE[mode] or any(
        not set(BASE[mode]) <= set(fit.feature_names) <= allowed
        or len(fit.feature_names) != len(fit.means)
        or len(fit.means) != len(fit.scales)
        or len(fit.scales) != len(fit.coefficients)
        or any(not isfinite(value) for value in (*fit.means, *fit.scales, *fit.coefficients, fit.intercept))
        or any(scale <= 0 for scale in fit.scales)
        for fit in fits
    ):
        raise ValueError("Invalid collision model coefficients or feature schema")
    return Model(mode, fits, data["report"])


@dataclass(frozen=True)
class Example:
    night: date
    site: str
    season: str | None
    count: int
    features: dict
    availability_issues: tuple


def _examples(rows, mode):
    examples, seen = [], set()
    for row in rows:
        night = local_night(row.get("night_date"))
        site = str(row.get("study_building_id") or "").strip()
        if not night or not site:
            raise ValueError("Training rows require study_building_id and an ISO night_date")
        key = (site, night)
        if key in seen:
            raise ValueError(f"Duplicate study building-night {site} on {night}")
        seen.add(key)
        label = row.get("observed_collision_count")
        if label in (None, ""):
            continue
        count = number(label, 0)
        if count is None or not count.is_integer():
            raise ValueError(f"Invalid collision count for {site} on {night}")
        issues = forecast_issues(row) if mode == "forecast" else ()
        examples.append(Example(night, site, season_for(row), int(count), _features(row, mode), tuple(issues)))
    return sorted(examples, key=lambda example: (example.night, example.site))


def _matrix(rows, feature_names):
    return np.array([[row.features[name] for name in feature_names] for row in rows], dtype=float)


def _fit(rows, feature_names):
    features = _matrix(rows, feature_names)
    counts = np.array([row.count for row in rows], dtype=float)
    means = features.mean(axis=0)
    scales = features.std(axis=0)
    constant = np.ptp(features, axis=0) == 0
    means[constant] = features[0, constant]
    scales[constant] = 1
    matrix = (features - means) / scales
    initial = np.zeros(len(feature_names) + 1)
    initial[0] = log(max(counts.mean(), MIN_RATE))

    def objective(params):
        coeffs = params[1:]
        linear = params[0] + matrix @ coeffs
        if linear.max() > 500:
            return np.inf, np.zeros_like(params)
        expected = np.exp(linear)
        errors = expected - counts
        loss = (expected - counts * linear).mean() + L2 * (coeffs @ coeffs) / 2
        gradient = np.r_[errors.mean(), matrix.T @ errors / len(rows) + L2 * coeffs]
        return loss, gradient

    if counts.any():
        result = minimize(objective, initial, jac=True, method="L-BFGS-B")
        if not result.success:
            raise ValueError(f"Poisson fit failed: {result.message}")
        intercept, *coefficients = result.x.tolist()
    else:
        intercept, *coefficients = initial.tolist()
    return PoissonFit(feature_names, tuple(sorted({row.season for row in rows})), tuple(means.tolist()),
                      tuple(scales.tolist()), tuple(coefficients), intercept)


def _metrics(rows, expected):
    counts = np.array([row.count for row in rows])
    expected = np.maximum(expected, MIN_RATE)
    by_night = defaultdict(list)
    for row, value in zip(rows, expected):
        by_night[row.night].append((value, row.site, row.count))
    alerted = [candidate for night in by_night.values()
               for candidate in sorted(night, key=lambda item: (-item[0], item[1]))[:ceil(ALERT_FRACTION * len(night))]]
    return {
        "mean_absolute_count_error": float(np.abs(counts - expected).mean()),
        "mean_poisson_deviance": float(2 * (xlogy(counts, counts / expected) - counts + expected).mean()),
        "observed_collisions_captured": sum(count for _, _, count in alerted) / int(counts.sum()) if counts.any() else None,
        "alerted_rows": len(alerted),
    }


def _summary(rows):
    nights = {row.night for row in rows}
    return {
        "rows": len(rows), "nights": len(nights), "sites": sorted({row.site for row in rows}),
        "first_night": min(nights).isoformat() if nights else None,
        "last_night": max(nights).isoformat() if nights else None,
        "seasons": dict(Counter(row.season for row in rows)),
    }


def _evaluate(rows, features, base):
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
        scores = {
            "train": _summary(train), "test": _summary(test),
            "seasonal_mean": _metrics(test, [seasonal.get(row.season, mean) for row in test]),
        }
        for label, columns in (("poisson", features), ("base_poisson_same_rows", base)):
            if label == "base_poisson_same_rows" and columns == features:
                continue
            fit = _fit(train, columns)
            scores[label] = _metrics(test, [exp(fit.intercept + sum(fit.effects(row.features))) for row in test])
        output[name] = scores
    return output


def train(rows, mode="observed"):
    """Fit and evaluate models, preserving a base fit for rows without enrichments."""
    if mode not in BASE:
        raise ValueError(f"Unknown model mode {mode!r}")
    examples = _examples(rows, mode)
    base = BASE[mode]
    enrichments = ENRICHMENTS[mode]
    all_features = base + tuple(feature for group in enrichments.values() for feature in group)
    report = {
        "labeled_input": _summary(examples),
        "count_distribution": dict(sorted(Counter(row.count for row in examples).items())),
        "missing_or_invalid_by_season": {},
        "l2_penalty": L2,
        "holdout_fraction": TEST_FRACTION,
        "alert_budget_fraction_per_night": ALERT_FRACTION,
        "feature_transforms": {name: "log1p" for name in all_features if name in LOG_FEATURES},
        "preprocessing": "Feature means and scales fit on training rows for each evaluation split",
        "profiles": {},
    }
    for season in sorted({row.season or "unknown" for row in examples}):
        subset = [row for row in examples if (row.season or "unknown") == season]
        missing = {name: sum(row.features[name] is None for row in subset) for name in all_features}
        missing.update(Counter(issue for row in subset for issue in row.availability_issues))
        report["missing_or_invalid_by_season"][season] = missing

    fits = []
    for size in range(len(enrichments) + 1):
        for groups in combinations(enrichments, size):
            features = base + tuple(feature for group in groups for feature in enrichments[group])
            eligible = [row for row in examples if not row.availability_issues
                        and all(row.features[name] is not None for name in features)]
            name = "+".join(groups) or "base"
            profile = report["profiles"][name] = {
                "features": features, "eligible": _summary(eligible),
                "unscorable_labeled_rows": len(examples) - len(eligible),
            }
            if len({row.night for row in eligible}) < 2 or len({row.site for row in eligible}) < 2:
                reason = "Need complete labeled rows from at least two different nights and two study buildings"
                if not groups:
                    raise ValueError(f"{mode} model cannot be trained. {reason}; found {len(eligible)} eligible rows.")
                profile["skipped"] = reason
                continue
            profile["evaluation"] = _evaluate(eligible, features, base)
            fits.append(_fit(eligible, features))
    fits.sort(key=lambda fit: -len(fit.feature_names))
    return Model(mode, tuple(fits), report)
