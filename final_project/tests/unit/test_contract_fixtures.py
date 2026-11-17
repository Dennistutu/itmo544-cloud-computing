"""API contract tests: real recorded responses (tests/fixtures/) fed to the parsing code.

Recorded 2026-11-16 for Chicago 60616, Duluth 55802 and Miami 33101. No network.
"""
import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from weather_tier import facts as f

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)   # just after the recording
ITEMS = ("temperature", "wind", "visibility", "uv", "humidity", "rain", "snow")


def body(name):
    return json.loads((FIX / f"{name}.json").read_text())["body"]


def build(zip_code, day, grid=None, om=None):
    grid = body(f"nws_griddata_{zip_code}")["properties"] if grid is None else grid
    om = body(f"openmeteo_{zip_code}") if om is None else om
    return f.build_facts(zip_code, body(f"zip_{zip_code}")["places"][0],
                         body(f"nws_points_{zip_code}")["properties"], grid, om, day, NOW)


# --- Zippopotam.us
def test_zip_lookup_shape():
    place = body("zip_60616")["places"][0]
    assert {"place name", "state abbreviation", "latitude", "longitude"} <= place.keys()


def test_unknown_zip_is_404_with_empty_body():
    raw = json.loads((FIX / "zip_00000.json").read_text())
    assert raw["status"] == 404 and raw["body"] == {}


# --- NWS points
@pytest.mark.parametrize("zip_code, tz", [("60616", "America/Chicago"),
                                          ("55802", "America/Chicago"),
                                          ("33101", "America/New_York")])
def test_points_gives_time_zone_and_links(zip_code, tz):
    p = body(f"nws_points_{zip_code}")["properties"]
    assert p["timeZone"] == tz
    assert p["forecastGridData"].startswith("https://api.weather.gov/gridpoints/")


# --- NWS griddata + Open-Meteo -> facts
@pytest.mark.parametrize("zip_code", ["60616", "55802", "33101"])
def test_every_item_present_for_every_day_in_window(zip_code):
    for offset in range(1, 7):        # day 0 morning is already past at NOW
        day = date(2026, 11, 16 + offset)
        doc = build(zip_code, day)
        for period in ("morning", "night"):
            items = doc["periods"][period]["items"]
            for name in ITEMS:
                assert items[name]["value"] is not None, (zip_code, day, period, name)


def test_chicago_rain_amount_falls_back_after_nws_horizon():
    near = build("60616", date(2026, 11, 18))["periods"]["morning"]["items"]["rain"]
    far = build("60616", date(2026, 11, 21))["periods"]["morning"]["items"]["rain"]
    assert near["source"] == f.NWS
    assert far["source"] == f.OPEN_METEO          # LOT stops QPF after ~3 days
    assert far["chance_source"] == f.NWS


def test_uv_always_from_open_meteo():
    uv = build("33101", date(2026, 11, 18))["periods"]["morning"]["items"]["uv"]
    assert uv["source"] == f.OPEN_METEO and uv["value"]["max"] > 0


def test_values_are_converted_to_display_units():
    t = build("33101", date(2026, 11, 18))["periods"]["morning"]["items"]["temperature"]
    assert 60 <= t["value"]["low"] <= 95 and t["unit"] == "°F"   # Miami, so °F not °C


def test_missing_field_in_both_sources_is_not_available_not_zero():
    grid = copy.deepcopy(body("nws_griddata_60616")["properties"])
    om = copy.deepcopy(body("openmeteo_60616"))
    om["hourly"].pop("visibility")                 # NWS already has no visibility here
    vis = build("60616", date(2026, 11, 18), grid, om)["periods"]["night"]["items"]["visibility"]
    assert vis["value"] is None and vis["reason"]


def test_weather_source_down_still_builds_with_the_other():
    doc = build("60616", date(2026, 11, 18), om={})   # Open-Meteo unavailable
    items = doc["periods"]["morning"]["items"]
    assert items["temperature"]["source"] == f.NWS
    assert items["uv"]["value"] is None               # only Open-Meteo has UV


def test_timezone_in_document():
    assert build("33101", date(2026, 11, 18))["timezone"] == "America/New_York"
