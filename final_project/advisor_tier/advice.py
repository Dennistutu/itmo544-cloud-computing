"""Advice for the three advice rows: Gemini first, rule-based fallback in our own code.

Decision 8: Gemini gets only fixed, validated values (the facts our code computed and a
clothing category). No user text, no email, no ZIP, no place name reach the model, so
there is nothing to inject. The answer must be JSON with exactly three short strings,
and it is checked before use. Gemini never supplies a number shown on the page.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

ADVICE_KEYS = ("road", "dressing", "accessories")
MAX_ADVICE_CHARS = 280
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# A measured-looking number in the advice means the model is restating or inventing data.
MEASUREMENT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:°|degrees?|deg\b|f\b|c\b|mph|km/?h|%|percent|in\b|inch|inches|"
    r"mm|cm|miles?|mi\b|ft\b|feet)", re.IGNORECASE)
# Markup, links and code in model text are never acceptable on this page.
HOSTILE_RE = re.compile(r"[<>{}`]|https?://|www\.|javascript:", re.IGNORECASE)

PERIOD_NAMES = ("morning", "night")
_PERIOD_SCHEMA = {
    "type": "OBJECT",
    "properties": {k: {"type": "STRING"} for k in ADVICE_KEYS},
    "required": list(ADVICE_KEYS),
}
# One call answers both periods, which halves the use of the free tier.
ANSWER_SCHEMA = {"type": "OBJECT", "properties": {p: _PERIOD_SCHEMA for p in PERIOD_NAMES},
                 "required": list(PERIOD_NAMES)}


# ------------------------------------------------------------------------ prompt
def compact_period(items: dict) -> dict:
    """The facts of one period in a small fixed shape. Missing items stay missing."""
    def v(name, *path):
        node = items.get(name, {}).get("value")
        for p in path:
            node = node.get(p) if isinstance(node, dict) else None
        return node
    return {
        "temp_low_f": v("temperature", "low"), "temp_high_f": v("temperature", "high"),
        "feels_like_low_f": v("temperature", "feels_like_low"),
        "wind_max_mph": v("wind", "sustained_max"), "gust_max_mph": v("wind", "gust_max"),
        "visibility_min_miles": v("visibility", "min"),
        "visibility_poor": v("visibility", "poor"),
        "uv_max": v("uv", "max"), "uv_level": v("uv", "level"),
        "humidity_min_pct": v("humidity", "min"), "humidity_max_pct": v("humidity", "max"),
        "rain": v("rain", "category"), "snow": v("snow", "category"),
    }


def build_prompt(facts: dict, clothing: str) -> str:
    """Prompt for both periods. Only computed facts and one of three clothing categories."""
    if clothing not in ("womenswear", "menswear", "neutral"):
        raise ValueError("unknown clothing category")
    data = {p: compact_period(facts["periods"][p]["items"]) for p in PERIOD_NAMES}
    return (
        "You write short practical weather advice. Use only the facts given. "
        "A null value means the fact is not available: do not guess it and do not rely on it. "
        "Do not repeat or invent any numbers or measurements. "
        "Reply as JSON with keys morning and night; each holds keys road, dressing, "
        "accessories, and each of those is one sentence of plain text, "
        f"at most {MAX_ADVICE_CHARS // 2} characters. "
        "dressing must suit the clothing category.\n"
        f"clothing_category: {clothing}\n"
        f"facts: {json.dumps(data, sort_keys=True)}"
    )


# ---------------------------------------------------------------------- checking
def check_answer(text: str | None) -> dict | None:
    """Return {morning: {...}, night: {...}} if the model's whole answer is usable, else None."""
    if not text or len(text) > 4000:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != set(PERIOD_NAMES):
        return None
    out = {}
    for period in PERIOD_NAMES:
        checked = check_period(data[period])
        if checked is None:
            return None
        out[period] = checked
    return out


def check_period(data) -> dict | None:
    """The three advice strings of one period, or None if any one is unusable."""
    if not isinstance(data, dict) or set(data) != set(ADVICE_KEYS):
        return None
    out = {}
    for key in ADVICE_KEYS:
        value = data[key]
        if not isinstance(value, str):
            return None
        value = " ".join(value.split())
        if not value or len(value) > MAX_ADVICE_CHARS:
            return None
        if HOSTILE_RE.search(value) or MEASUREMENT_RE.search(value):
            return None
        out[key] = value
    return out


def call_gemini(prompt: str, key: str, model: str, timeout: float) -> tuple[str | None, str]:
    """One generateContent call. Returns (answer text or None, outcome label)."""
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": ANSWER_SCHEMA,
                             "maxOutputTokens": 600, "temperature": 0.4},
    }).encode()
    req = urllib.request.Request(GEMINI_URL.format(model=model), data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data["candidates"][0]["content"]["parts"][0]["text"], "ok"
    except urllib.error.HTTPError as err:
        return None, "quota" if err.code == 429 else f"http_{err.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "unreachable"
    except (KeyError, IndexError, ValueError):
        return None, "malformed"


# ---------------------------------------------------------------------- fallback
def fallback_advice(facts: dict, period: str, clothing: str) -> dict:
    """Decision 9: rule-based advice written in our own code. Uses only facts present."""
    d = compact_period(facts["periods"][period]["items"])
    low, feels = d["temp_low_f"], d["feels_like_low_f"]
    cold = feels if feels is not None else low
    wind, gust = d["wind_max_mph"], d["gust_max_mph"]
    rain, snow = d["rain"], d["snow"]

    road = []
    if snow in ("Mild", "Heavy"):
        road.append("Expect slippery, snow-covered roads; slow down and leave extra distance")
    elif rain == "Heavy":
        road.append("Heavy rain may cause standing water; slow down and avoid flooded roads")
    elif rain == "Mild":
        road.append("Roads may be wet; allow extra braking distance")
    if cold is not None and cold <= 34 and (rain in ("Mild", "Heavy") or snow != "No"):
        road.append("watch for ice on bridges and shaded spots")
    if d["visibility_poor"]:
        road.append("visibility is reduced, so use low-beam headlights")
    if (gust or wind or 0) >= 30:
        road.append("strong gusts may push high-sided vehicles")
    if not road:
        road.append("No weather hazards expected for driving" if any(
            x is not None for x in (rain, snow, wind)) else
            "Road advice is limited because some weather data is not available")

    if cold is None:
        dressing = "Temperature data is not available; dress in layers you can adjust"
    elif cold < 32:
        dressing = "Wear a heavy insulated coat, warm layers, a hat and gloves"
    elif cold < 50:
        dressing = "Wear a warm jacket over a sweater or long sleeves"
    elif cold < 65:
        dressing = "A light jacket or long sleeves should be comfortable"
    else:
        dressing = "Light, breathable clothing is suitable"
    style = {"womenswear": " (for example a coat or jacket with trousers or a long skirt and tights)",
             "menswear": " (for example a coat or jacket with trousers)",
             "neutral": ""}[clothing] if cold is not None and cold < 65 else ""
    dressing += style
    if rain in ("Mild", "Heavy") or snow in ("Mild", "Heavy"):
        dressing += ", with a waterproof outer layer and water-resistant shoes"

    acc = []
    if rain in ("Mild", "Heavy"):
        acc.append("an umbrella")
    if snow in ("Mild", "Heavy") or (cold is not None and cold < 32):
        acc.extend(["gloves", "a scarf"])
    if d["uv_max"] is not None and d["uv_max"] >= 3:
        acc.extend(["sunscreen", "sunglasses"])
    if period == "night":
        acc.append("a phone light or reflective item if walking")
    accessories = ("Carry " + ", ".join(dict.fromkeys(acc))) if acc else "No special accessories needed"

    return {"road": "; ".join(road) + ".", "dressing": dressing + ".",
            "accessories": accessories + "."}
