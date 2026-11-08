"""Turn NEXRAD volume scans into bird density, altitude, and flight direction over NYC.

This follows the published vol2bird / bioRad approach (Dokter et al. 2011, 2019; Kranstauber et
al. 2020) in simplified form:

1. Classify each radar gate. Dual-pol correlation coefficient (rho_hv) separates biology (< 0.95)
   from rain (>= 0.95). A gate with no echo counts as zero birds, not missing data.
2. Build a vertical profile from gates 5-35 km from the radar, where the beam still samples low
   altitudes: bird density per 200 m layer, plus flight speed and direction from a VAD fit to the
   radial velocities.
3. Correct for range. Over NYC (~95 km from KOKX) the lowest beam is ~1-1.6 km up and misses the
   lowest birds. For each NYC grid cell, compare what the beam observed with what it would observe
   if the profile applied there. That ratio scales the profile's birds/km^2 to the cell.

Outputs are estimates for one radar's view. The profile's altitude distribution and flight
direction are assumed to hold across NYC; only the overall amount varies by cell.
"""

from math import atan2, degrees, floor, hypot, pi, radians

import numpy as np
from scipy.special import ndtr

WAVELENGTH_CM = 10.7          # NEXRAD S-band
K2 = 0.93                     # |K|^2 for water, the radar's reflectivity calibration convention
BIRD_RCS_CM2 = 11.0           # vol2bird default radar cross-section of one bird
BEAM_WIDTH_DEG = 0.95
RHOHV_WEATHER = 0.95          # rho_hv at or above this is treated as precipitation
DBZ_MAX = 35.0                # stronger echoes are rain, hail, or clutter, not migrating birds
CFP_MAX_DB = 10.0             # gates where the clutter filter removed more power than this are dropped
PROFILE_RANGE_M = (5_000, 35_000)
LAYER_M = 200
TOP_M = 3_000
LAYER_BOTTOMS = np.arange(0, TOP_M, LAYER_M)
MIN_ETA_GATES = 30
MIN_VELOCITY_GATES = 100
MIN_SECTORS = 6               # of 8 azimuth sectors needed for a stable VAD fit
ALIAS_RESIDUAL_M_S = 10.0
MIN_SD_VVP_BIRDS = 2.0        # vol2bird: lower velocity spread suggests insects or rain, not birds
GRID_SWEEP_MAX_DEG = 1.5      # only the lowest tilts see birds over NYC
GRID_LAT_DEG = 0.01           # ~1.1 km
GRID_LON_DEG = 0.013          # ~1.1 km at NYC's latitude
MIN_CELL_GATES = 10
LOW_ALTITUDE_M = 600          # roughly the height of NYC's tallest buildings


def eta_from_dbz(dbz):
    """Reflectivity factor (dBZ) to reflectivity (cm^2 per km^3)."""
    return 10 ** (dbz / 10) * 1e3 * pi ** 5 * K2 / WAVELENGTH_CM ** 4


def _filled(radar, name, sweep):
    field = radar.fields.get(name)
    if field is None:
        return None
    data = np.ma.masked_invalid(field["data"][radar.get_slice(sweep)].astype(float))
    return None if data.mask.all() else data.filled(np.nan)


def _match_rays(az_from, az_to):
    """Index into az_from of the nearest azimuth for each azimuth in az_to."""
    order = np.argsort(az_from)
    ordered = az_from[order]
    after = np.searchsorted(ordered, az_to) % len(ordered)
    before = (after - 1) % len(ordered)
    gap = lambda i: np.abs((ordered[i] - az_to + 180) % 360 - 180)
    return order[np.where(gap(after) <= gap(before), after, before)]


def sweeps(radar):
    """Per-sweep gate arrays with a bird / weather / clutter classification."""
    result = []
    for i in range(radar.nsweeps):
        s = radar.get_slice(i)
        z = _filled(radar, "reflectivity", i)
        if z is None:
            continue
        lat, lon, height = radar.get_gate_lat_lon_alt(i)
        sweep = {
            "angle": float(radar.fixed_angle["data"][i]),
            "azimuth": radar.azimuth["data"][s].astype(float),
            "elevation": radar.elevation["data"][s].astype(float),
            "range": np.broadcast_to(radar.range["data"].astype(float), z.shape),
            "z": z, "lat": lat, "lon": lon, "height": height,
            "rhohv": _filled(radar, "cross_correlation_ratio", i),
            "cfp": _filled(radar, "clutter_filter_power_removed", i),
            "velocity": _filled(radar, "velocity", i),
        }
        sweep["dual_pol"] = sweep["rhohv"] is not None
        if not sweep["dual_pol"]:
            # Split-cut Doppler sweeps carry no rho_hv. Borrow it from the matching surveillance sweep.
            pair = next((p for p in reversed(result)
                         if p["dual_pol"] and abs(p["angle"] - sweep["angle"]) < 0.1), None)
            if pair is not None:
                rays = _match_rays(pair["azimuth"], sweep["azimuth"])
                sweep["rhohv"] = pair["rhohv"][rays]
                sweep["cfp"] = None if pair["cfp"] is None else pair["cfp"][rays]
        result.append(_classify(sweep))
    return result


def _classify(sweep):
    z = sweep["z"]
    rhohv = sweep["rhohv"] if sweep["rhohv"] is not None else np.full(z.shape, np.nan)
    cfp = sweep["cfp"] if sweep["cfp"] is not None else np.zeros(z.shape)
    echo = np.isfinite(z)
    weather = echo & ((rhohv >= RHOHV_WEATHER) | (z > DBZ_MAX))
    unclassified = echo & ~np.isfinite(rhohv)
    clutter = np.nan_to_num(cfp) > CFP_MAX_DB
    sweep["weather"] = weather
    sweep["valid"] = ~weather & ~unclassified & ~clutter
    sweep["eta"] = np.where(echo, eta_from_dbz(np.where(echo, z, 0.0)), 0.0)
    return sweep


def _vad(azimuth, elevation, velocity):
    """Fit horizontal wind (u, v) to radial velocities; return u, v, residual SD, gates used."""
    a, e = np.radians(azimuth), np.radians(elevation)
    design = np.column_stack([np.cos(e) * np.sin(a), np.cos(e) * np.cos(a)])
    keep = np.ones(len(velocity), bool)
    for _ in range(2):
        if keep.sum() < MIN_VELOCITY_GATES:
            return None
        coef, *_ = np.linalg.lstsq(design[keep], velocity[keep], rcond=None)
        residual = velocity - design @ coef
        # Aliased gates are off by about twice the Nyquist velocity; drop them and refit.
        keep &= np.abs(residual) < ALIAS_RESIDUAL_M_S
    if keep.sum() < MIN_VELOCITY_GATES or np.unique((azimuth[keep] // 45).astype(int)).size < MIN_SECTORS:
        return None
    return float(coef[0]), float(coef[1]), float(residual[keep].std()), int(keep.sum())


def profile(swept):
    """One row per 200 m layer: bird density, reflectivity, and flight speed and direction."""
    eta, weather, vel = ([[] for _ in LAYER_BOTTOMS] for _ in range(3))
    for sweep in swept:
        near = (sweep["range"] >= PROFILE_RANGE_M[0]) & (sweep["range"] <= PROFILE_RANGE_M[1])
        layer = np.floor(sweep["height"] / LAYER_M).astype(int)
        inside = near & (layer >= 0) & (layer < len(LAYER_BOTTOMS))
        moving = inside & sweep["valid"] & np.isfinite(sweep["z"])
        if sweep["velocity"] is not None:
            moving &= np.isfinite(sweep["velocity"])
            rays = np.broadcast_to(np.arange(len(sweep["azimuth"]))[:, None], sweep["z"].shape)
        for k in range(len(LAYER_BOTTOMS)):
            here = inside & (layer == k)
            if sweep["dual_pol"]:  # Doppler split cuts would duplicate their surveillance sweep.
                eta[k].append(sweep["eta"][here & sweep["valid"]])
                weather[k].append((here.sum(), (here & sweep["weather"]).sum()))
            if sweep["velocity"] is not None:
                m = moving & (layer == k)
                vel[k].append((sweep["azimuth"][rays[m]], sweep["elevation"][rays[m]], sweep["velocity"][m]))

    rows = []
    for k, bottom in enumerate(LAYER_BOTTOMS):
        values = np.concatenate(eta[k]) if eta[k] else np.array([])
        total = sum(n for n, _ in weather[k])
        rain = sum(w for _, w in weather[k]) / total if total else None
        known = values.size >= MIN_ETA_GATES and (rain is None or rain < 0.5)
        mean_eta = float(values.mean()) if known else None
        fit = None
        if vel[k]:
            fit = _vad(*(np.concatenate(parts) for parts in zip(*vel[k])))
        u, v, sd, n_vel = fit if fit else (None, None, None, 0)
        likely_birds = None if sd is None else sd >= MIN_SD_VVP_BIRDS
        density = None if mean_eta is None else mean_eta / BIRD_RCS_CM2
        if density is not None and likely_birds is False:
            density = 0.0  # vol2bird convention: uniform motion is insects or rain, not birds.
        rows.append({
            "height_bottom_m": int(bottom), "height_top_m": int(bottom + LAYER_M),
            "eta_cm2_per_km3": mean_eta,
            "birds_per_km3": density,
            "u_m_s": u, "v_m_s": v,
            "ground_speed_m_s": None if u is None else hypot(u, v),
            "direction_deg": None if u is None else (degrees(atan2(u, v)) + 360) % 360,
            "sd_vvp_m_s": sd, "likely_birds": likely_birds,
            "eta_gates": int(values.size), "velocity_gates": n_vel,
            "weather_fraction": rain,
        })
    return rows


def summarize(layers):
    """Column totals for one scan: birds/km^2, traffic, altitude, and mean flight direction."""
    depth_km = LAYER_M / 1000
    usable = [r for r in layers if r["birds_per_km3"] is not None]
    vid = sum(r["birds_per_km3"] * depth_km for r in usable)
    moving = [r for r in usable if r["u_m_s"] is not None and r["birds_per_km3"] > 0]
    weight = sum(r["birds_per_km3"] for r in moving)
    u = sum(r["birds_per_km3"] * r["u_m_s"] for r in moving) / weight if weight else None
    v = sum(r["birds_per_km3"] * r["v_m_s"] for r in moving) / weight if weight else None
    speed = sum(r["birds_per_km3"] * r["ground_speed_m_s"] for r in moving) / weight if weight else None
    low = sum(r["birds_per_km3"] for r in usable if r["height_top_m"] <= LOW_ALTITUDE_M)
    total = sum(r["birds_per_km3"] for r in usable)
    return {
        "profile_complete": all(r["eta_cm2_per_km3"] is not None for r in layers if r["height_bottom_m"] < 2000),
        "birds_per_km2": vid,
        # Birds crossing a 1 km line perpendicular to the flight direction, per hour.
        "migration_traffic_birds_per_km_h": None if speed is None else vid * speed * 3.6,
        "ground_speed_m_s": speed,
        "direction_deg": None if u is None else (degrees(atan2(u, v)) + 360) % 360,
        "mean_height_m": sum(r["birds_per_km3"] * (r["height_bottom_m"] + LAYER_M / 2) for r in usable) / total
        if total else None,
        "fraction_below_600m": low / total if total else None,
    }


def mean_direction(scans):
    """Traffic-weighted mean flight direction (degrees toward which birds fly) across scans."""
    weights = [s["migration_traffic_birds_per_km_h"] for s in scans]
    if not scans or not sum(weights):
        return None
    east = sum(w * np.sin(radians(s["direction_deg"])) for w, s in zip(weights, scans))
    north = sum(w * np.cos(radians(s["direction_deg"])) for w, s in zip(weights, scans))
    return (degrees(atan2(east, north)) + 360) % 360


def cell_index(lat, lon, bounds):
    west, south, _, _ = bounds
    return floor((lat - south) / GRID_LAT_DEG), floor((lon - west) / GRID_LON_DEG)


def cell_bounds(row, col, bounds):
    west, south, _, _ = bounds
    return (west + col * GRID_LON_DEG, south + row * GRID_LAT_DEG,
            west + (col + 1) * GRID_LON_DEG, south + (row + 1) * GRID_LAT_DEG)


def grid(swept, layers, scan, bounds):
    """Range-corrected birds/km^2 for each ~1 km cell inside bounds."""
    if not scan["profile_complete"]:
        return []
    west, south, east, north = bounds
    layer_eta = np.array([r["eta_cm2_per_km3"] or 0.0 for r in layers])
    bottoms, tops = LAYER_BOTTOMS.astype(float), LAYER_BOTTOMS + float(LAYER_M)
    sums = {}
    for sweep in swept:
        if not sweep["dual_pol"] or sweep["angle"] > GRID_SWEEP_MAX_DEG:
            continue
        inside = (sweep["lon"] >= west) & (sweep["lon"] < east) & (sweep["lat"] >= south) & (sweep["lat"] < north)
        if not inside.any():
            continue
        valid = inside & sweep["valid"]
        height, rng = sweep["height"][valid], sweep["range"][valid]
        sigma = rng * radians(BEAM_WIDTH_DEG) / 2.3548  # Gaussian beam; half-power width = beam width
        # Share of beam power in each layer. Power below ground or above TOP_M sees no birds.
        weights = ndtr((tops[None, :] - height[:, None]) / sigma[:, None]) \
            - ndtr((bottoms[None, :] - height[:, None]) / sigma[:, None])
        expected = weights @ layer_eta
        rows = np.floor((sweep["lat"] - south) / GRID_LAT_DEG).astype(int)
        cols = np.floor((sweep["lon"] - west) / GRID_LON_DEG).astype(int)
        for r, c, obs, exp, h in zip(rows[valid], cols[valid], sweep["eta"][valid], expected, height):
            cell = sums.setdefault((r, c), [0.0, 0.0, 0, 0.0, 0])
            cell[0] += obs
            cell[1] += exp
            cell[2] += 1
            cell[3] += h
        for r, c in zip(rows[inside & sweep["weather"]], cols[inside & sweep["weather"]]):
            sums.setdefault((r, c), [0.0, 0.0, 0, 0.0, 0])[4] += 1

    traffic_per_bird = (scan["migration_traffic_birds_per_km_h"] / scan["birds_per_km2"]
                        if scan["birds_per_km2"] and scan["migration_traffic_birds_per_km_h"] is not None else None)
    result = []
    for (r, c), (obs, exp, n, h, n_weather) in sorted(sums.items()):
        rain = n_weather / (n + n_weather)
        ratio = obs / exp if n >= MIN_CELL_GATES and exp > 0 and rain < 0.5 else None
        birds = None if ratio is None else ratio * scan["birds_per_km2"]
        w, s, e, nth = cell_bounds(r, c, bounds)
        result.append({
            "cell_row": int(r), "cell_col": int(c),
            "cell_lat": (s + nth) / 2, "cell_lon": (w + e) / 2,
            "birds_per_km2": birds,
            "migration_traffic_birds_per_km_h": None if birds is None or traffic_per_bird is None
            else birds * traffic_per_bird,
            "range_correction": ratio,
            "observed_eta_cm2_per_km3": obs / n if n else None,
            "beam_height_m": h / n if n else None,
            "gates": n, "weather_fraction": rain,
        })
    return result
