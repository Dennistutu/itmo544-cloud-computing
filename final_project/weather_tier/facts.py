"""Pure functions that turn raw NWS and Open-Meteo responses into normalized facts.

No network, no AWS, no clock: every function takes its inputs as arguments, so all of
it is unit-tested against the recorded fixtures in tests/fixtures/.

Normalized units: degrees F, mph, miles, inches, percent, WHO UV index.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------------- periods
# Decision 4: local clock hours on the selected date. Night stops at 23:59 so a period
# never crosses midnight.
PERIODS = {"morning": (6, 12), "night": (18, 24)}   # [start hour, end hour)
PERIOD_LABELS = {"morning": "Morning (06:00-11:59)", "night": "Night (18:00-23:59)"}


def period_hours(day: date, period: str, tz_name: str) -> list[datetime]:
    """UTC start of each local hour of `period` on `day` in time zone `tz_name`.

    Built from local wall-clock hours, so a daylight-saving change on that day is
    handled by zoneinfo, not by fixed offsets.
    """
    tz = ZoneInfo(tz_name)
    start, end = PERIODS[period]
    hours = []
    for h in range(start, end):
        local = datetime(day.year, day.month, day.day, h, tzinfo=tz)
        hours.append(local.astimezone(timezone.utc))
    return hours


def local_today(now_utc: datetime, tz_name: str) -> date:
    """Today's date at the location, not at the server."""
    return now_utc.astimezone(ZoneInfo(tz_name)).date()


def date_in_window(day: date, now_utc: datetime, tz_name: str, days: int = 7) -> bool:
    """True when `day` is today .. today+days-1 in the location's time zone."""
    today = local_today(now_utc, tz_name)
    return today <= day <= today + timedelta(days=days - 1)


# --------------------------------------------------------------------------- NWS grid
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_valid_time(valid_time: str) -> tuple[datetime, int]:
    """'2026-11-19T18:00:00+00:00/PT6H' -> (UTC start, length in whole hours)."""
    start_text, _, duration = valid_time.partition("/")
    match = _DURATION.match(duration)
    if not match:
        raise ValueError(f"unsupported duration {duration!r}")
    days, hours, minutes = (int(x) if x else 0 for x in match.groups())
    length = days * 24 + hours + (1 if minutes else 0)
    start = datetime.fromisoformat(start_text).astimezone(timezone.utc)
    return start, max(length, 1)


def grid_hourly(series: dict | None, accumulate: bool = False) -> dict[datetime, float]:
    """Expand an NWS griddata series into {UTC hour: value}.

    Instantaneous fields keep their value for every hour of the interval. Accumulated
    fields (rain, snow) spread their amount evenly over the hours of the interval.
    Null values are skipped, so a missing hour stays missing, never zero.
    """
    out: dict[datetime, float] = {}
    if not isinstance(series, dict):
        return out
    for entry in series.get("values") or []:
        value = entry.get("value")
        if value is None:
            continue
        start, length = parse_valid_time(entry["validTime"])
        share = float(value) / length if accumulate else float(value)
        for i in range(length):
            out[start + timedelta(hours=i)] = share
    return out


def openmeteo_hourly(body: dict | None, field: str, tz_name: str) -> dict[datetime, float]:
    """Open-Meteo hourly arrays (local times, timezone=auto) -> {UTC hour: value}."""
    out: dict[datetime, float] = {}
    if not body:
        return out
    hourly = body.get("hourly") or {}
    times, values = hourly.get("time") or [], hourly.get(field) or []
    tz = ZoneInfo(tz_name)
    for t, v in zip(times, values):
        if v is None:
            continue
        local = datetime.fromisoformat(t).replace(tzinfo=tz)
        out[local.astimezone(timezone.utc)] = float(v)
    return out


# ------------------------------------------------------------------------- conversion
def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def kmh_to_mph(k: float) -> float:
    return k / 1.609344


def m_to_mi(m: float) -> float:
    return m / 1609.344


def ft_to_mi(ft: float) -> float:
    return ft / 5280


def mm_to_in(mm: float) -> float:
    return mm / 25.4


# ------------------------------------------------------------------------- sources
NWS, OPEN_METEO = "NWS", "Open-Meteo"

# field: (NWS griddata name, NWS converter, accumulate?, Open-Meteo name, OM converter)
FIELDS = {
    "temp":       ("temperature",                c_to_f,    False, "temperature_2m",            None),
    "feels":      ("apparentTemperature",        c_to_f,    False, None,                        None),
    "wind":       ("windSpeed",                  kmh_to_mph, False, "wind_speed_10m",           None),
    "gust":       ("windGust",                   kmh_to_mph, False, None,                       None),
    "visibility": ("visibility",                 m_to_mi,   False, "visibility",                ft_to_mi),
    "uv":         (None,                         None,      False, "uv_index",                  None),
    "humidity":   ("relativeHumidity",           None,      False, "relative_humidity_2m",      None),
    "pop":        ("probabilityOfPrecipitation", None,      False, "precipitation_probability", None),
    "rain":       ("quantitativePrecipitation",  mm_to_in,  True,  "precipitation",             None),
    "snow":       ("snowfallAmount",             mm_to_in,  True,  "snowfall",                  None),
}


def pick_series(field: str, hours: list[datetime], grid: dict | None, om: dict | None,
                tz_name: str) -> tuple[list[float] | None, str | None]:
    """Values for every hour in `hours`, from the first source that covers all of them.

    Order: NWS, then Open-Meteo. A source that covers only some hours is not used,
    because an aggregate over part of the period would look complete but is not.
    Returns (values, source) or (None, None) when no source covers the period.
    """
    nws_name, nws_conv, acc, om_name, om_conv = FIELDS[field]
    candidates = []
    if nws_name and grid is not None:
        candidates.append((NWS, grid_hourly(grid.get(nws_name), acc), nws_conv))
    if om_name and om is not None:
        candidates.append((OPEN_METEO, openmeteo_hourly(om, om_name, tz_name), om_conv))
    for source, by_hour, conv in candidates:
        if hours and all(h in by_hour for h in hours):
            values = [by_hour[h] for h in hours]
            return ([conv(v) for v in values] if conv else values), source
    return None, None


# ----------------------------------------------------------------- derived categories
# Decision 6. Amount thresholds, applied to the period total in inches.
# Rain: 0.01 in is the smallest measurable amount (NWS); 0.30 in is the American
# Meteorological Society boundary for heavy rain (7.6 mm per hour). Forecast amounts
# arrive in 6-hour blocks, so the hourly distribution is unknown; we apply the AMS
# one-hour heavy boundary to the period total, which is the conservative reading.
# Snow: 0.1 in is the smallest measurable snowfall (NWS); 1.0 in matches the AMS
# boundary for heavy snowfall rate (1 inch per hour).
# Chance: below 20 % NWS forecasts do not mention precipitation, so we report "No".
RAIN_MILD_IN, RAIN_HEAVY_IN = 0.01, 0.30
SNOW_MILD_IN, SNOW_HEAVY_IN = 0.1, 1.0
MIN_CHANCE_PCT = 20


def categorize(amount_in: float | None, chance_pct: float | None,
               mild: float, heavy: float) -> str | None:
    """Heavy / Mild / No from a period total and the period's highest chance.

    - chance below 20 %                -> No (even if a small amount is forecast)
    - amount known: >= heavy -> Heavy, >= mild -> Mild, else No
    - amount unknown, chance >= 20 %   -> Mild (we never claim Heavy without an amount)
    - both unknown                     -> None (shown as "not available")
    Values are rounded to 3 decimals first, so 7.62 mm is exactly 0.300 in.
    """
    if chance_pct is not None and chance_pct < MIN_CHANCE_PCT:
        return "No"
    if amount_in is not None:
        a = round(amount_in, 3)
        if a >= heavy:
            return "Heavy"
        if a >= mild:
            return "Mild"
        return "No"
    if chance_pct is not None:
        return "Mild"
    return None


# ---------------------------------------------------------------------- aggregation
def _r(x: float | None, nd: int = 0):
    if x is None:
        return None
    v = round(x, nd)
    return int(v) if nd == 0 else v


def missing(reason: str) -> dict:
    """A report item that has no value. Shown as 'not available' with the reason."""
    return {"value": None, "reason": reason}


def aggregate_period(hours: list[datetime], now_utc: datetime, grid: dict | None,
                     om: dict | None, tz_name: str) -> dict:
    """The seven measured/derived items for one period (Decision 5).

    Hours that are already in the past are dropped. If the whole period is past,
    every item is 'not available' with that reason.
    """
    current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
    future = [h for h in hours if h >= current_hour]
    if not future:
        reason = "this period has already passed"
        return {k: missing(reason) for k in
                ("temperature", "wind", "visibility", "uv", "humidity", "rain", "snow")}

    def series(field):
        return pick_series(field, future, grid, om, tz_name)

    nodata = "no forecast source covers this period"
    items: dict[str, dict] = {}

    temp, src = series("temp")
    feels, _ = series("feels")
    items["temperature"] = missing(nodata) if temp is None else {
        "value": {"low": _r(min(temp)), "high": _r(max(temp)),
                  "feels_like_low": _r(min(feels)) if feels else None},
        "unit": "°F", "source": src}

    wind, src = series("wind")
    gust, _ = series("gust")
    items["wind"] = missing(nodata) if wind is None else {
        "value": {"sustained_max": _r(max(wind)), "gust_max": _r(max(gust)) if gust else None},
        "unit": "mph", "source": src}

    vis, src = series("visibility")
    items["visibility"] = missing(nodata) if vis is None else {
        "value": {"min": _r(min(vis), 1), "poor": min(vis) < POOR_VISIBILITY_MI},
        "unit": "miles", "source": src}

    uv, src = series("uv")
    items["uv"] = missing(nodata) if uv is None else {
        "value": {"max": _r(max(uv), 1), "level": uv_level(max(uv))},
        "unit": "UV index", "source": src}

    hum, src = series("humidity")
    items["humidity"] = missing(nodata) if hum is None else {
        "value": {"min": _r(min(hum)), "max": _r(max(hum))}, "unit": "%", "source": src}

    pop, pop_src = series("pop")
    chance = max(pop) if pop else None
    for name, mild, heavy in (("rain", RAIN_MILD_IN, RAIN_HEAVY_IN),
                              ("snow", SNOW_MILD_IN, SNOW_HEAVY_IN)):
        amounts, src = series(name)
        total = sum(amounts) if amounts is not None else None
        cat = categorize(total, chance, mild, heavy)
        if cat is None:
            items[name] = missing(nodata)
            continue
        items[name] = {"value": {"category": cat, "amount_in": _r(total, 2),
                                 "chance_pct": _r(chance)},
                       "unit": "in", "source": src or pop_src, "chance_source": pop_src,
                       "note": None if total is not None else "chance only; amount not forecast"}
    return items


# Below 3 miles is IFR/MVFR boundary territory in the FAA flight categories and the
# point where drivers are told to slow down; we flag it as "poor".
POOR_VISIBILITY_MI = 3.0


def uv_level(index: float) -> str:
    """WHO UV index bands."""
    if index < 3:
        return "Low"
    if index < 6:
        return "Moderate"
    if index < 8:
        return "High"
    if index < 11:
        return "Very high"
    return "Extreme"


def build_facts(zip_code: str, place: dict, point_props: dict, grid: dict | None,
                om: dict | None, day: date, now_utc: datetime) -> dict:
    """The complete normalized facts document for one ZIP and date."""
    tz_name = point_props["timeZone"]
    periods = {}
    for name in PERIODS:
        hours = period_hours(day, name, tz_name)
        current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
        remaining = [h for h in hours if h >= current_hour]
        partial_from = None
        if remaining and len(remaining) < len(hours):
            partial_from = remaining[0].astimezone(ZoneInfo(tz_name)).strftime("%H:%M")
        periods[name] = {"label": PERIOD_LABELS[name], "partial_from": partial_from,
                         "items": aggregate_period(hours, now_utc, grid, om, tz_name)}
    used = sorted({i.get("source") for p in periods.values() for i in p["items"].values()
                   if i.get("source")})
    return {
        "zip": zip_code,
        "place": place["place name"],
        "state": place["state abbreviation"],
        "timezone": tz_name,
        "date": day.isoformat(),
        "periods": periods,
        "sources": used,
        "generated_at": now_utc.isoformat(timespec="seconds"),
    }
