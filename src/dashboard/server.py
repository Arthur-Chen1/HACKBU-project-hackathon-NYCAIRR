"""Local dashboard server: standard library only, serves index.html and a small JSON API.

    GET  /                       the dashboard page
    GET  /api/options            areas, weather sources, preset periods
    POST /api/run                {start, end, area, mode, weather, minutes,
                                  migration_forecast, migration_issued} -> {job}
                                 area: "midtown" | "pilot" | {"cells": [[row, col], ...]}
                                 migration_forecast: optional regional birds/km/h for forecast mode,
                                 issued at migration_issued (NYC local "YYYY-MM-DDTHH:MM")
    GET  /api/job?id=            {state, log, nights: [overview...], error}
    GET  /api/night?id=&night=   buildings and radar cells GeoJSON, scan timeline, overview
"""

import json
import threading
import uuid
import webbrowser
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import building_analysis
import main as pipeline
import migration_analysis

PAGE = Path(__file__).with_name("index.html")
PRESETS = [
    {"label": "Sep 10, 2021: very heavy migration", "start": "2021-09-10", "end": "2021-09-10"},
    {"label": "Oct 1, 2021: light migration", "start": "2021-10-01", "end": "2021-10-01"},
    {"label": "Oct 10, 2021: rained out", "start": "2021-10-10", "end": "2021-10-10"},
    {"label": "Oct 13-14, 2021: medium then heavy", "start": "2021-10-13", "end": "2021-10-14"},
    {"label": "Sep 28-Oct 4, 2021: one week", "start": "2021-09-28", "end": "2021-10-04"},
    # Forecast demos. The migration values are the study's measured KOKX traffic, used as test data
    # (a perfect forecast) with an issue time before the cutoff. HRRR supplies the weather.
    {"label": "Forecast demo: Sep 10, 2021 heavy night (test migration 4,945)", "start": "2021-09-10",
     "end": "2021-09-10", "mode": "forecast", "migration_forecast": 4945, "migration_issued": "2021-09-10T12:00"},
    {"label": "Forecast demo: Oct 1, 2021 light night (test migration 298)", "start": "2021-10-01",
     "end": "2021-10-01", "mode": "forecast", "migration_forecast": 298, "migration_issued": "2021-10-01T12:00"},
    {"label": "Forecast demo: Sep 10, 2021 with estimated migration", "start": "2021-09-10",
     "end": "2021-09-10", "mode": "forecast"},
]
_jobs = {}
_run_lock = threading.Lock()  # One pipeline at a time; radar downloads share a cache.


def _json(value):
    return json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, (date, datetime)) else str(v))


def _run(job, params):
    def log(message):
        job["log"].append(message)

    try:
        log("Waiting for the analysis worker; another run may be using the shared data cache")
        with _run_lock:
            log("Analysis worker ready")
            for night in pipeline.nights(params["start"], params["end"]):
                try:
                    result = pipeline.run_night(night, params["area"], params["weather"], params["minutes"], log,
                                                forecast=params["forecast"], daily_inputs=params["daily_inputs"],
                                                operator_settings=params["operator_settings"])
                except Exception as error:
                    log(f"{night}: failed ({error})")
                    job["nights"].append({"night": night, "error": str(error)})
                    continue
                job["results"][night.isoformat()] = result
                job["nights"].append(result["overview"])
        job["state"] = "done"
    except Exception as error:
        job["state"], job["error"] = "error", str(error)


def _manual_migration(body, start, end, daily_inputs):
    """Add a regional migration forecast entered in the dashboard, logged with its issue time."""
    value = body.get("migration_forecast")
    if value in (None, ""):
        return daily_inputs
    traffic = float(value)
    if not traffic >= 0:
        raise ValueError("Migration forecast must be a nonnegative number of birds/km/h")
    if not body.get("migration_issued"):
        raise ValueError("Enter when the migration forecast was issued")
    issued = datetime.fromisoformat(body["migration_issued"])
    issued = (issued if issued.tzinfo else issued.replace(tzinfo=pipeline.NYC)).astimezone(UTC)
    entry = {"forecast_migration_birds_per_km_h": traffic, "forecast_migration_issue_utc": issued,
             "forecast_migration_region": "NYC (entered in dashboard)"}
    return {**daily_inputs, **{(night, ""): entry for night in pipeline.nights(start, end)}}


def _night_payload(result):
    rows = {row["bin"]: row for row in result["rows"]}
    buildings = []
    for d in result["decisions"]:
        row = rows[d["bin"]]
        buildings.append({"type": "Feature", "geometry": row["geometry"], "properties": {
            **{key: d.get(key) for key in ("bin", "bbl", "name", "night_date", "expected_collision_count",
                                          "risk_priority", "risk_basis", "risk_features", "decision_at_utc",
                                          "priority_rank", "priority_scored_buildings", "priority_basis")},
            # The website presents model explanations, without the CLI's lighting-budget reasons.
            "reasons": row["risk_reasons"],
            "height_m": row["roof_height_m"], "land_use": row["land_use_label"],
            "year": row["construction_year"], "relative_density": row.get("neighborhood_relative_density"),
        }})
    cells = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [cell["polygon"]]},
              "properties": {"mean_birds_per_km2": cell["mean_birds_per_km2"],
                             "neighborhood": cell["neighborhood_relative_density"]}}
             for cell in result["analysis"]["cells"].values()]
    folder = migration_analysis.RADAR_DIR / result["night"].isoformat()
    scans = []
    if result["overview"]["mode"] == "observed" and (folder / "scans.csv").is_file():
        local = pipeline.lighting_judgement._local
        scans = [{"time_local": local(s["scan_time_utc"]), "birds_per_km2": s["birds_per_km2"],
                  "traffic": s["migration_traffic_birds_per_km_h"], "direction_deg": s["direction_deg"],
                  "complete": s["profile_complete"]} for s in migration_analysis._scans(folder)]
    return {"overview": result["overview"], "scans": scans,
            "buildings": {"type": "FeatureCollection", "features": buildings},
            "cells": {"type": "FeatureCollection", "features": cells}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, kind="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        self._send(status, _json({"error": message}))

    def log_message(self, *args):  # Keep the terminal for pipeline progress.
        pass

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/options":
            return self._send(200, _json({"areas": building_analysis.DASHBOARD_AREAS, "presets": PRESETS,
                                          "default_area": building_analysis.DEFAULT_AREA,
                                          "weather": ["none", "dryad", "hrrr"], "max_nights": pipeline.MAX_NIGHTS,
                                          "grid": pipeline.grid_info(),
                                          "forecast_model_available": building_analysis.DEFAULT_FORECAST_MODEL.is_file(),
                                          "today": datetime.now(pipeline.NYC).date(),
                                          "now_local": datetime.now(pipeline.NYC).strftime("%Y-%m-%dT%H:%M")}))
        job = _jobs.get(query.get("id"))
        if url.path == "/api/job":
            if job is None:
                return self._error(404, "Unknown job")
            return self._send(200, _json({k: job[k] for k in ("state", "log", "nights", "error")}))
        if url.path == "/api/night":
            result = job and job["results"].get(query.get("night", ""))
            if result is None:
                return self._error(404, "That night has not finished processing")
            return self._send(200, _json(_night_payload(result)))
        self._error(404, "Not found")

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            return self._error(404, "Not found")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            start = date.fromisoformat(body["start"])
            end = date.fromisoformat(body.get("end") or body["start"])
            mode = body.get("mode", "observed")
            forecast = mode == "forecast"
            pipeline.validate(start, end, forecast=forecast)
            area = body.get("area", building_analysis.DEFAULT_AREA)
            weather, minutes = body.get("weather", "dryad"), int(body.get("minutes", 60))
            if isinstance(area, dict):
                area = area.get("cells") or []
            elif area not in building_analysis.DASHBOARD_AREAS:  # Citywide is CLI-only (too large to map).
                raise ValueError(f"Unsupported area {area!r}")
            pipeline.resolve_area(area)  # Raises ValueError for bad grid cells.
            if (mode not in ("observed", "forecast")
                    or weather not in ("none", "dryad", "hrrr") or minutes not in (30, 60)):
                raise ValueError("Unsupported mode, weather source, or scan interval")
            daily_inputs = pipeline.load_daily_inputs(self.server.daily_inputs) if forecast else None
            if forecast:
                daily_inputs = _manual_migration(body, start, end, daily_inputs)
        except (KeyError, TypeError, OSError, ValueError) as error:
            return self._error(400, str(error))
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"state": "running", "log": [], "nights": [], "results": {}, "error": None}
        params = {"start": start, "end": end, "area": area, "weather": weather, "minutes": minutes,
                  "forecast": forecast, "daily_inputs": daily_inputs, "operator_settings": self.server.operator_settings}
        threading.Thread(target=_run, args=(_jobs[job_id], params), daemon=True).start()
        self._send(200, _json({"job": job_id}))


def serve(port=8765, open_browser=True, *, daily_inputs=None, operator_settings=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daily_inputs = daily_inputs
    server.operator_settings = operator_settings
    url = f"http://127.0.0.1:{port}/"
    print(f"DarkSky dashboard running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
