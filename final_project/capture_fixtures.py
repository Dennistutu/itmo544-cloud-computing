#!/usr/bin/env python3
"""Save real responses from the weather sources as JSON fixtures.

These files are the evidence for the source map (Section 5) and the input for the
API contract tests (Section 11.2). Run once from final_project/ with network access:

    python capture_fixtures.py

No AWS calls and no API keys are used.
"""
import json
import pathlib
import sys

import requests

from stack.config import load_dotenv

OUT = pathlib.Path("tests/fixtures")
TIMEOUT = 15
# Chicago (home), Duluth MN (snow likely in late November), Miami (high UV, no snow)
ZIPS = ["60616", "55802", "33101"]
BAD_ZIP = "00000"  # well formed, does not exist


def save(name: str, resp: requests.Response) -> dict | None:
    """Write status + body to tests/fixtures/<name>.json and return the body if JSON."""
    try:
        body = resp.json()
    except ValueError:
        body = {"_raw_text": resp.text[:2000]}
    path = OUT / f"{name}.json"
    path.write_text(json.dumps({"status": resp.status_code, "url": resp.url, "body": body}, indent=2))
    print(f"{resp.status_code}  {path}  ({path.stat().st_size // 1024} KB)")
    return body if resp.ok else None


def main() -> None:
    env = load_dotenv(".env")
    contact = env.get("CONTACT_EMAIL", "")
    if not contact or "example.com" in contact:
        sys.exit("Set CONTACT_EMAIL in .env first (NWS asks for a contact in the User-Agent).")
    nws = {"User-Agent": f"itmo544-weather-advisor ({contact})", "Accept": "application/geo+json"}
    OUT.mkdir(parents=True, exist_ok=True)

    get = lambda url, **kw: requests.get(url, timeout=TIMEOUT, **kw)

    save(f"zip_{BAD_ZIP}", get(f"https://api.zippopotam.us/us/{BAD_ZIP}"))

    for zip_code in ZIPS:
        place = save(f"zip_{zip_code}", get(f"https://api.zippopotam.us/us/{zip_code}"))
        if not place:
            continue
        lat = round(float(place["places"][0]["latitude"]), 4)
        lon = round(float(place["places"][0]["longitude"]), 4)

        point = save(f"nws_points_{zip_code}", get(f"https://api.weather.gov/points/{lat},{lon}", headers=nws))
        if not point:
            continue
        props = point["properties"]
        print(f"      time zone {props.get('timeZone')}, grid {props.get('gridId')}/{props.get('gridX')},{props.get('gridY')}")
        save(f"nws_forecast_{zip_code}", get(props["forecast"], headers=nws))
        save(f"nws_hourly_{zip_code}", get(props["forecastHourly"], headers=nws))
        save(f"nws_griddata_{zip_code}", get(props["forecastGridData"], headers=nws))

        # Candidate second source for UV (NWS does not publish a UV index in its forecast API)
        save(f"openmeteo_{zip_code}", get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "forecast_days": 7, "timezone": "auto",
                    "hourly": "uv_index,visibility,relative_humidity_2m,temperature_2m,"
                              "wind_speed_10m,precipitation,snowfall,precipitation_probability",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "precipitation_unit": "inch"}))

    print("\nDone. Next: python summarize_fixtures.py")


if __name__ == "__main__":
    main()
