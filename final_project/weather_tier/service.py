"""Weather data tier: ZIP + date -> normalized facts for Morning and Night.

Knows nothing about the user: it never sees gender or email, never calls Gemini.

    GET /health                      -> 200 {"ok": true}
    GET /facts?zip=60616&date=YYYY-MM-DD
        200 facts (with "cache": "hit" | "miss" | "stale", "age_minutes")
        400 invalid_zip | invalid_date | date_out_of_range
        404 zip_not_found
        503 weather_unavailable
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from flask import Flask, jsonify, request

from . import facts as F

log = logging.getLogger("weather")
ZIP_RE = re.compile(r"^[0-9]{5}$")   # ASCII digits only (\d would accept other scripts)


def log_event(event: str, **fields) -> None:
    """One JSON line per event. Only ZIP and date are logged; nothing personal exists here."""
    log.info(json.dumps({"event": event, **fields}))


class FactsCache:
    """DynamoDB cache of facts, keyed by ZIP#date. Every item has a TTL (expires_at)."""

    def __init__(self, table, fresh_seconds: int, keep_seconds: int):
        self.table = table
        self.fresh_seconds = fresh_seconds       # served without calling the sources
        self.keep_seconds = keep_seconds         # kept as a stale copy for outages

    def get(self, key: str, now: float):
        try:
            item = self.table.get_item(Key={"pk": key}).get("Item")
        except Exception:                        # cache trouble must not break the request
            log_event("cache_error", op="get")
            return None, None
        if not item or int(item.get("expires_at", 0)) <= now:
            return None, None
        return json.loads(item["facts"]), now - float(item["fetched_at"])

    def put(self, key: str, doc: dict, now: float) -> None:
        try:
            self.table.put_item(Item={"pk": key, "facts": json.dumps(doc),
                                      "fetched_at": str(now),
                                      "expires_at": int(now + self.keep_seconds)})
        except Exception:
            log_event("cache_error", op="put")


def get_facts(zip_code: str, day: date, sources, cache: FactsCache, now_utc: datetime):
    """The data tier's whole decision tree. Returns (http_status, json_body)."""
    now = now_utc.timestamp()
    key = f"{zip_code}#{day.isoformat()}"

    cached, age = cache.get(key, now)
    if cached is not None and age < cache.fresh_seconds:
        return 200, {**cached, "cache": "hit", "age_minutes": int(age // 60)}

    def stale_or_unavailable(stage: str):
        log_event("source_failed", stage=stage, zip=zip_code)
        if cached is not None:
            return 200, {**cached, "cache": "stale", "age_minutes": int(age // 60),
                         "stale_reason": f"{stage} unavailable"}
        return 503, {"error": "weather_unavailable",
                     "message": "Weather data is unavailable right now. Please try again shortly."}

    status, place_doc = sources.zip_lookup(zip_code)
    if status == 404:
        return 404, {"error": "zip_not_found", "message": "ZIP code not found."}
    if status != 200 or not place_doc or not place_doc.get("places"):
        return stale_or_unavailable("zip_lookup")
    place = place_doc["places"][0]
    lat, lon = float(place["latitude"]), float(place["longitude"])

    status, point = sources.nws_point(lat, lon)
    if status == 404:
        return 404, {"error": "zip_not_found", "message": "No NWS forecast covers this ZIP code."}
    if status != 200 or not point:
        return stale_or_unavailable("nws_points")
    props = point["properties"]

    if not F.date_in_window(day, now_utc, props["timeZone"]):
        return 400, {"error": "date_out_of_range",
                     "message": "Choose a date from today to 6 days ahead (local time at the ZIP)."}

    # The two forecast sources are independent, so ask them at the same time. This keeps
    # the worst case at three timeouts in a row (ZIP, points, forecasts), not four.
    with ThreadPoolExecutor(max_workers=2) as pool:
        grid_job = pool.submit(sources.nws_grid, props.get("forecastGridData", ""))
        om_job = pool.submit(sources.open_meteo, lat, lon)
        g_status, grid = grid_job.result()
        o_status, om = om_job.result()
    grid_props = grid["properties"] if g_status == 200 and grid else None
    om = om if o_status == 200 else None
    if grid_props is None and om is None:
        return stale_or_unavailable("forecast")
    if grid_props is None:
        log_event("source_failed", stage="nws_grid", zip=zip_code)
    if om is None:
        log_event("source_failed", stage="open_meteo", zip=zip_code)

    doc = F.build_facts(zip_code, place, props, grid_props, om, day, now_utc)
    cache.put(key, doc, now)
    return 200, {**doc, "cache": "miss", "age_minutes": 0}


def create_app(sources, cache: FactsCache, clock=lambda: datetime.now(timezone.utc)) -> Flask:
    """Flask app factory. sources, cache and clock are injected so tests need no AWS."""
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(ok=True, tier="weather")

    @app.get("/facts")
    def facts_route():
        started = time.monotonic()
        zip_code = request.args.get("zip", "")
        if not ZIP_RE.fullmatch(zip_code):
            return jsonify(error="invalid_zip", message="Give a five digit US ZIP code."), 400
        try:
            day = date.fromisoformat(request.args.get("date", ""))
        except ValueError:
            return jsonify(error="invalid_date", message="Give a date as YYYY-MM-DD."), 400
        status, body = get_facts(zip_code, day, sources, cache, clock())
        log_event("facts", zip=zip_code, date=day.isoformat(), status=status,
                  cache=body.get("cache"), ms=int((time.monotonic() - started) * 1000))
        return jsonify(body), status

    return app


def main() -> None:   # pragma: no cover - run on the instance by the boot script
    """Entry point on the instance: settings from the environment the boot script wrote."""
    import os

    import boto3

    from .sources import Sources

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"]).Table(
        os.environ["FACTS_TABLE"])
    cache = FactsCache(table, int(os.environ["FACTS_FRESH_SECONDS"]),
                       int(os.environ["FACTS_KEEP_SECONDS"]))
    app = create_app(Sources(os.environ["CONTACT_EMAIL"],
                             float(os.environ["OUTSIDE_TIMEOUT_SECONDS"])), cache)
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ["APP_PORT"]), threads=8)


if __name__ == "__main__":   # pragma: no cover
    main()
