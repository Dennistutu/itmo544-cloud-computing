#!/usr/bin/env python3
"""Print a short evidence summary of the saved fixtures: which fields each source has,
their units, how far ahead they reach, and one real example value.

    python summarize_fixtures.py > tests/fixtures/SUMMARY.txt
"""
import json
import pathlib

FIX = pathlib.Path("tests/fixtures")
GRID_FIELDS = ["temperature", "apparentTemperature", "relativeHumidity", "windSpeed", "windGust",
               "visibility", "probabilityOfPrecipitation", "quantitativePrecipitation",
               "snowfallAmount", "iceAccumulation", "skyCover", "weather", "hazards"]


def load(name):
    path = FIX / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def reach(values):
    """First and last validTime start of a gridded series."""
    if not values:
        return "no values"
    return f"{values[0]['validTime'][:16]} .. {values[-1]['validTime'][:16]}  ({len(values)} entries)"


def main():
    bad = load("zip_00000")
    print(f"ZIP 00000 -> HTTP {bad['status']}, body {json.dumps(bad['body'])[:80]}\n" if bad else "zip_00000 missing\n")

    for path in sorted(FIX.glob("zip_*.json")):
        z = path.stem.split("_")[1]
        if z == "00000":
            continue
        place = load(f"zip_{z}")["body"]["places"][0]
        print(f"===== {z}  {place['place name']}, {place['state abbreviation']}  "
              f"lat {place['latitude']} lon {place['longitude']}")

        point = load(f"nws_points_{z}")
        if point and point["status"] == 200:
            print(f"  points: timeZone={point['body']['properties']['timeZone']}")

        fc = load(f"nws_forecast_{z}")
        if fc and fc["status"] == 200:
            periods = fc["body"]["properties"]["periods"]
            p = periods[0]
            print(f"  forecast (text): {len(periods)} periods {periods[0]['startTime'][:10]} .. {periods[-1]['endTime'][:10]}")
            print(f"    keys: {sorted(p.keys())}")
            print(f"    e.g. {p['name']}: {p['temperature']}{p['temperatureUnit']}, wind {p['windSpeed']}, "
                  f"PoP {p.get('probabilityOfPrecipitation', {}).get('value')}, '{p['shortForecast']}'")

        hr = load(f"nws_hourly_{z}")
        if hr and hr["status"] == 200:
            periods = hr["body"]["properties"]["periods"]
            print(f"  hourly: {len(periods)} hours {periods[0]['startTime'][:16]} .. {periods[-1]['startTime'][:16]}")
            print(f"    keys: {sorted(periods[0].keys())}")

        grid = load(f"nws_griddata_{z}")
        if grid and grid["status"] == 200:
            props = grid["body"]["properties"]
            print("  griddata:")
            for f in GRID_FIELDS:
                s = props.get(f)
                if not isinstance(s, dict):
                    print(f"    {f:28s} MISSING")
                    continue
                vals = s.get("values", [])
                ex = vals[0]["value"] if vals else None
                print(f"    {f:28s} uom={s.get('uom', '-'):22s} e.g. {str(ex)[:40]:40s} {reach(vals)}")
            uv = [k for k in props if "uv" in k.lower() or "ultra" in k.lower()]
            print(f"    UV-like fields in griddata: {uv or 'NONE'}")

        om = load(f"openmeteo_{z}")
        if om and om["status"] == 200:
            h = om["body"]["hourly"]
            units = om["body"]["hourly_units"]
            print(f"  open-meteo: tz={om['body'].get('timezone')}  {h['time'][0]} .. {h['time'][-1]} ({len(h['time'])} hours)")
            for k in [k for k in h if k != "time"]:
                nonnull = [v for v in h[k] if v is not None]
                print(f"    {k:26s} unit={units.get(k):8s} max={max(nonnull) if nonnull else None}  nulls={len(h[k]) - len(nonnull)}")
        print()


if __name__ == "__main__":
    main()
