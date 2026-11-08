"""Estimate tonight's regional migration traffic when no issued forecast is supplied.

The collision forecast needs a regional migration forecast in KOKX units (birds crossing 1 km per
hour). When neither the daily input file nor the dashboard supplies one, this module estimates it
from data available at the cutoff:

    day of year + HRRR overnight weather at the NYC reference point
        -> gradient-boosted trees trained on the Dryad study's nightly KOKX traffic and LaGuardia weather

Training uses only nights from years before the forecast night, so a historical forecast replay
never learns from its own season. As with the collision model, observed LaGuardia weather stands in
for the forecast during training. On held-out nights this estimate ranks nights better than a
seasonal median of the same history (Spearman 0.57-0.69 vs 0.28-0.49, MODEL_RUNBOOK.md), but a
typical night is still off by a factor of about 3. Prefer an issued forecast such as BirdCast's.

Recalibration. Trees trained on log traffic predict a typical night for the conditions, which pulls
every estimate toward the middle: in forward-chained tests the raw model called 4 of 37 heavy
nights (>= 2,000 birds/km/h) high. The estimate is therefore stretched in log space so its spread
matches the observed spread:

    calibrated = mean(observed) + scale x (raw - mean(raw)),  scale = sd(observed) / sd(raw)

The means and scale come from leave-one-year-out predictions within the training years, never from
the model's own fit. This catches 17 of 37 heavy nights, at the cost of more false alarms (14 of 42
high calls were low nights). With a single training year there is nothing to hold out, so the raw
estimate is used.

Last night's radar traffic was tested as an extra input and left out: consecutive nights correlate
(0.33 in log traffic), but after date and weather the carry-over adds almost nothing (Spearman
0.65 -> 0.66) while costing a ~100 MB radar download per forecast night.

The Dryad nights cover March 31 to June 7 and August 30 to November 14. Outside those windows the
trees would only repeat the season's edge, so the estimate is left blank.
"""

from functools import lru_cache
from math import expm1
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from src.features.model_inputs import number
from src.ingest import dryad
from src.ingest.http import Cache
from src.models.forecast_ai import CLEAR_SKY_CEILING_M, STATION_VISIBILITY_CAP_M

FEATURES = ("day_of_year", "wind_u_m_s", "wind_v_m_s", "visibility_m", "cloud_ceiling_m")
SEASON_MARGIN_DAYS = 7
HYPERPARAMETERS = {"max_iter": 300, "learning_rate": 0.03, "max_leaf_nodes": 7, "min_samples_leaf": 20,
                   "random_state": 0}


def _day(night):
    return night.timetuple().tm_yday


def _fit(matrix, target):
    return HistGradientBoostingRegressor(**HYPERPARAMETERS).fit(matrix, target)


def _calibration(matrix, target, years):
    """(raw mean, observed mean, scale) from leave-one-year-out predictions, or None with one year."""
    if len(set(years)) < 2:
        return None
    held_out = np.empty(len(target))
    for year in set(years):
        test = years == year
        held_out[test] = _fit(matrix[~test], target[~test]).predict(matrix[test])
    return float(held_out.mean()), float(target.mean()), float(target.std() / held_out.std())


@lru_cache(maxsize=8)
def _model(before_year, cache_dir):
    """Fit on Dryad nights before `before_year`.

    Returns (estimator, calibration or None, covered days of year, years), or None without data.
    """
    nights = {}
    for row in dryad.surveys(Cache(Path(cache_dir))):
        if row["night_date"].year < before_year:
            nights[row["night_date"]] = row  # Every site shares the night's regional values.
    if len(nights) < 30:
        return None
    ordered = sorted(nights)
    matrix = np.array([[_day(n), nights[n]["observed_wind_u_m_s"], nights[n]["observed_wind_v_m_s"],
                        nights[n]["observed_visibility_m"], nights[n]["observed_cloud_ceiling_m"]] for n in ordered])
    target = np.log1p([nights[n]["observed_migration_birds_per_km_h"] for n in ordered])
    years = np.array([n.year for n in ordered])
    return (_fit(matrix, target), _calibration(matrix, target, years), frozenset(_day(n) for n in ordered),
            tuple(sorted(set(years.tolist()))))


def estimate(night, weather, decision_at, cache_dir="data/raw"):
    """Daily-input fields for an estimated regional migration forecast, or None with a reason.

    weather: the HRRR summary for the NYC reference point (hrrr.weather_forecast output).
    """
    fitted = _model(night.year, str(cache_dir))
    if fitted is None:
        return None, "no earlier Dryad migration seasons to learn from"
    estimator, calibration, days, years = fitted
    if not any(abs(_day(night) - day) <= SEASON_MARGIN_DAYS for day in days):
        return None, "night is outside the monitored migration seasons (Mar 31-Jun 7, Aug 30-Nov 14)"
    expected, covered = number(weather.get("hrrr_expected_hours"), 1), number(weather.get("hrrr_required_field_hours"), 0)
    visibility = number(weather.get("forecast_visibility_m"), 0)
    u, v = number(weather.get("forecast_wind_u_m_s")), number(weather.get("forecast_wind_v_m_s"))
    if expected is None or covered != expected or None in (visibility, u, v) or decision_at is None:
        return None, "HRRR weather at the NYC reference point is incomplete"
    ceiling = number(weather.get("forecast_cloud_ceiling_m"), 0)
    row = [_day(night), u, v, min(visibility, STATION_VISIBILITY_CAP_M),
           CLEAR_SKY_CEILING_M if ceiling is None else ceiling]
    log_traffic = float(estimator.predict(np.array([row]))[0])
    if calibration:
        raw_mean, observed_mean, scale = calibration
        log_traffic = observed_mean + scale * (log_traffic - raw_mean)
    label = "recalibrated " if calibration else ""
    return {
        "forecast_migration_birds_per_km_h": max(expm1(log_traffic), 0.0),
        # Built at the cutoff from the HRRR run selected for it and earlier seasons' records.
        "forecast_migration_issue_utc": decision_at,
        "forecast_migration_region": f"NYC {label}estimate from HRRR weather and {years[0]}-{years[-1]} KOKX seasons",
    }, None
