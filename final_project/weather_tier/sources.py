"""HTTP calls to the outside weather sources. Every call has a time limit.

Each function returns (status, body). status is the HTTP status, or 0 when the call
failed before a response (timeout, DNS, connection refused). Nothing here raises for
network problems, so the caller decides what the user sees.
"""
from __future__ import annotations

import requests

ZIP_API = "https://api.zippopotam.us/us/{zip}"
NWS_POINTS = "https://api.weather.gov/points/{lat},{lon}"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

OM_HOURLY = ("uv_index,visibility,relative_humidity_2m,temperature_2m,wind_speed_10m,"
             "precipitation,snowfall,precipitation_probability")


class Sources:
    """The four outside lookups, with a shared session, timeout and User-Agent."""

    def __init__(self, contact: str, timeout: float = 8.0, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        # NWS asks for an identifying User-Agent with a contact address.
        self.nws_headers = {"User-Agent": f"itmo544-weather-advisor ({contact})",
                            "Accept": "application/geo+json"}

    def _get(self, url, **kwargs) -> tuple[int, dict | None]:
        try:
            resp = self.session.get(url, timeout=self.timeout, **kwargs)
        except requests.RequestException:
            return 0, None
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None

    def zip_lookup(self, zip_code: str):
        return self._get(ZIP_API.format(zip=zip_code))

    def nws_point(self, lat: float, lon: float):
        return self._get(NWS_POINTS.format(lat=round(lat, 4), lon=round(lon, 4)),
                         headers=self.nws_headers)

    def nws_grid(self, url: str):
        if not url.startswith("https://api.weather.gov/"):     # never follow a foreign link
            return 0, None
        return self._get(url, headers=self.nws_headers)

    def open_meteo(self, lat: float, lon: float):
        return self._get(OPEN_METEO, params={
            "latitude": round(lat, 4), "longitude": round(lon, 4), "forecast_days": 7,
            "timezone": "auto", "hourly": OM_HOURLY, "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph", "precipitation_unit": "inch"})
