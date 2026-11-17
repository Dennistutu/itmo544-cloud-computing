"""Unit tests for weather_tier.facts: periods, time zones, DST, parsing, thresholds."""
from datetime import date, datetime, timedelta, timezone

import pytest

from weather_tier import facts as f

UTC = timezone.utc


# ------------------------------------------------------------------ periods and time zones
def test_morning_is_local_time_not_utc():
    hours = f.period_hours(date(2026, 11, 17), "morning", "America/Chicago")
    assert hours[0] == datetime(2026, 11, 17, 12, tzinfo=UTC)   # 06:00 CST = 12:00 UTC
    assert len(hours) == 6


def test_night_does_not_cross_midnight():
    hours = f.period_hours(date(2026, 11, 17), "night", "America/New_York")
    assert hours[0] == datetime(2026, 11, 17, 23, tzinfo=UTC)   # 18:00 EST
    assert hours[-1] == datetime(2026, 11, 18, 4, tzinfo=UTC)   # 23:00 EST


@pytest.mark.parametrize("day, first_utc_hour", [
    (date(2026, 10, 31), 11),   # day before DST ends: 06:00 CDT = 11:00 UTC
    (date(2026, 11, 1), 12),    # DST ended at 02:00 that morning: 06:00 CST = 12:00 UTC
    (date(2026, 3, 7), 12),     # day before DST starts: CST
    (date(2026, 3, 8), 11),     # DST started at 02:00: 06:00 CDT = 11:00 UTC
])
def test_morning_across_daylight_saving_changes(day, first_utc_hour):
    assert f.period_hours(day, "morning", "America/Chicago")[0].hour == first_utc_hour


def test_hawaii_has_no_dst():
    a = f.period_hours(date(2026, 1, 15), "morning", "Pacific/Honolulu")[0]
    b = f.period_hours(date(2026, 7, 15), "morning", "Pacific/Honolulu")[0]
    assert a.hour == b.hour == 16


def test_local_today_differs_from_utc_late_evening():
    now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)      # 21:00 on the 16th in Chicago
    assert f.local_today(now, "America/Chicago") == date(2026, 11, 16)


@pytest.mark.parametrize("offset, ok", [(-1, False), (0, True), (6, True), (7, False)])
def test_seven_day_window_boundaries(offset, ok):
    now = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
    today = f.local_today(now, "America/Chicago")
    assert f.date_in_window(today + timedelta(days=offset), now, "America/Chicago") is ok


# ---------------------------------------------------------------------- NWS parsing
@pytest.mark.parametrize("text, hours", [
    ("2026-11-19T18:00:00+00:00/PT6H", 6),
    ("2026-11-16T23:00:00+00:00/P1DT8H", 32),
    ("2026-11-16T18:00:00+00:00/P4DT6H", 102),
    ("2026-11-16T18:00:00+00:00/PT1H", 1),
])
def test_parse_valid_time(text, hours):
    start, length = f.parse_valid_time(text)
    assert start.tzinfo is not None and length == hours


def test_parse_valid_time_rejects_garbage():
    with pytest.raises(ValueError):
        f.parse_valid_time("2026-11-19T18:00:00+00:00/banana")


def test_accumulated_amount_is_spread_not_repeated():
    series = {"values": [{"validTime": "2026-11-19T18:00:00+00:00/PT6H", "value": 6.0}]}
    by_hour = f.grid_hourly(series, accumulate=True)
    assert len(by_hour) == 6 and sum(by_hour.values()) == pytest.approx(6.0)


def test_null_values_stay_missing_not_zero():
    series = {"values": [{"validTime": "2026-11-19T18:00:00+00:00/PT2H", "value": None}]}
    assert f.grid_hourly(series) == {}


def test_partial_coverage_falls_back_to_second_source():
    hours = [datetime(2026, 11, 19, 18, tzinfo=UTC) + timedelta(hours=i) for i in range(3)]
    grid = {"relativeHumidity": {"values": [
        {"validTime": "2026-11-19T18:00:00+00:00/PT2H", "value": 50}]}}  # covers 2 of 3 hours
    om = {"hourly": {"time": ["2026-11-19T12:00", "2026-11-19T13:00", "2026-11-19T14:00"],
                     "relative_humidity_2m": [60, 61, 62]}}
    values, source = f.pick_series("humidity", hours, grid, om, "America/Chicago")
    assert source == f.OPEN_METEO and values == [60, 61, 62]


def test_no_source_means_none():
    hours = [datetime(2026, 11, 19, 18, tzinfo=UTC)]
    assert f.pick_series("uv", hours, {}, None, "America/Chicago") == (None, None)


# ------------------------------------------------------------------------ thresholds
RAIN = (f.RAIN_MILD_IN, f.RAIN_HEAVY_IN)
SNOW = (f.SNOW_MILD_IN, f.SNOW_HEAVY_IN)


@pytest.mark.parametrize("amount, expected", [
    (0.0, "No"), (0.009, "No"), (0.01, "Mild"), (0.011, "Mild"),
    (0.299, "Mild"), (0.30, "Heavy"), (0.301, "Heavy"), (7.62 / 25.4, "Heavy"),
])
def test_rain_thresholds_on_and_around_each_boundary(amount, expected):
    assert f.categorize(amount, 80, *RAIN) == expected


@pytest.mark.parametrize("amount, expected", [
    (0.0, "No"), (0.099, "No"), (0.1, "Mild"), (0.101, "Mild"),
    (0.999, "Mild"), (1.0, "Heavy"), (1.001, "Heavy"),
])
def test_snow_thresholds_on_and_around_each_boundary(amount, expected):
    assert f.categorize(amount, 80, *SNOW) == expected


@pytest.mark.parametrize("chance, expected", [(19, "No"), (20, "Heavy"), (21, "Heavy")])
def test_chance_boundary(chance, expected):
    assert f.categorize(0.5, chance, *RAIN) == expected


def test_chance_only_is_mild_never_heavy():
    assert f.categorize(None, 90, *RAIN) == "Mild"


def test_amount_only_uses_amount():
    assert f.categorize(0.4, None, *RAIN) == "Heavy"


def test_nothing_known_is_not_available():
    assert f.categorize(None, None, *RAIN) is None


# ------------------------------------------------------------------------ conversions
def test_conversions():
    assert f.c_to_f(0) == 32 and f.c_to_f(100) == 212
    assert f.kmh_to_mph(1.609344) == pytest.approx(1)
    assert f.m_to_mi(16093.44) == pytest.approx(10)
    assert f.ft_to_mi(5280) == 1
    assert f.mm_to_in(25.4) == 1


@pytest.mark.parametrize("uv, level", [(0, "Low"), (2.9, "Low"), (3, "Moderate"),
                                       (6, "High"), (8, "Very high"), (11, "Extreme")])
def test_uv_levels(uv, level):
    assert f.uv_level(uv) == level


def test_period_already_passed():
    hours = f.period_hours(date(2026, 11, 16), "morning", "America/Chicago")
    items = f.aggregate_period(hours, datetime(2026, 11, 17, 1, tzinfo=UTC), {}, None,
                               "America/Chicago")
    assert all(i["value"] is None and "passed" in i["reason"] for i in items.values())
