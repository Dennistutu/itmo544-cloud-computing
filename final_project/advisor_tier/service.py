"""Advisor tier: form, validation, report, Gemini advice and email.

It never calls a weather API itself and holds no weather credentials; it asks the
data tier for facts over HTTP. Email and gender are never logged; email is never sent
to Gemini; gender is turned into a clothing category before anything else sees it.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import requests
from flask import Flask, render_template_string, request

from . import advice as A
from .emailer import recipient_tag, report_text, send_report
from .store import AdvisorStore, visitor_id
from .templates import FORM_HTML, REPORT_HTML, STATUS_HTML
from .validation import GENDER_CHOICES, clean_email, clothing_category, validate_form

log = logging.getLogger("advisor")
REPORT_KEY_RE = re.compile(r"^report#[0-9]{5}#[0-9]{4}-[0-9]{2}-[0-9]{2}#(womenswear|menswear|neutral)$")
ROW_LABELS = [("temperature", "Temperature"), ("wind", "Wind speed"),
              ("visibility", "Visibility"), ("uv", "UV"), ("humidity", "Humidity"),
              ("rain", "Rain"), ("snow", "Snow")]


def log_event(event: str, **fields) -> None:
    """JSON log line. Callers pass only non-personal fields (never email or gender)."""
    log.info(json.dumps({"event": event, **fields}))


# ------------------------------------------------------------------ display formatting
def format_item(name: str, item: dict) -> tuple[str, str]:
    """(display text, source label) for one fact. Missing -> 'Not available (reason)'."""
    v = item.get("value")
    if v is None:
        return f"Not available ({item.get('reason', 'no data')})", "-"
    src = item.get("source", "-")
    if name == "temperature":
        text = f"{v['low']}–{v['high']} °F"
        if v.get("feels_like_low") is not None:
            text += f" (feels like {v['feels_like_low']} °F)"
    elif name == "wind":
        text = f"up to {v['sustained_max']} mph"
        if v.get("gust_max") is not None:
            text += f", gusts {v['gust_max']} mph"
    elif name == "visibility":
        text = f"{v['min']} mi minimum" + (" (poor)" if v["poor"] else "")
    elif name == "uv":
        text = f"{v['max']} ({v['level']})"
    elif name == "humidity":
        text = f"{v['min']}–{v['max']} %"
    else:  # rain / snow
        text = v["category"]
        details = []
        if v.get("amount_in") is not None:
            details.append(f"{v['amount_in']:.2f} in")
        if v.get("chance_pct") is not None:
            details.append(f"{v['chance_pct']}% chance")
        if details:
            text += f" ({', '.join(details)})"
        if item.get("note"):
            text += f" — {item['note']}"
        if item.get("chance_source") and item.get("chance_source") != src:
            src = f"{src}; chance: {item['chance_source']}"
    return text, src


def build_rows(facts: dict) -> list[dict]:
    rows = []
    for name, label in ROW_LABELS:
        m_text, m_src = format_item(name, facts["periods"]["morning"]["items"][name])
        n_text, n_src = format_item(name, facts["periods"]["night"]["items"][name])
        rows.append({"label": label, "morning": m_text, "night": n_text,
                     "morning_src": m_src, "night_src": n_src})
    return rows


# ---------------------------------------------------------------------- the app
class Deps:
    """Everything the advisor needs from outside, injected so tests need no AWS."""

    def __init__(self, store: AdvisorStore, sns, ssm, settings: dict, http=requests,
                 gemini_call=A.call_gemini, clock=lambda: datetime.now(timezone.utc)):
        self.store, self.sns, self.ssm, self.s = store, sns, ssm, settings
        self.http, self.gemini_call, self.clock = http, gemini_call, clock
        self._key, self._key_at = None, 0.0

    def gemini_key(self) -> str:
        """Gemini key from SSM SecureString, cached briefly in memory. Never logged."""
        if self._key is None or time.time() - self._key_at > self.s["KEY_CACHE_SECONDS"]:
            self._key = self.ssm.get_parameter(Name=self.s["GEMINI_PARAM_NAME"],
                                               WithDecryption=True)["Parameter"]["Value"]
            self._key_at = time.time()
        return self._key


def get_advice(deps: Deps, facts: dict, clothing: str) -> tuple[dict, str]:
    """(advice for both periods, source label). Falls back to rules on any problem."""
    def fallback(reason):
        log_event("gemini_fallback", reason=reason)
        return ({p: A.fallback_advice(facts, p, clothing) for p in A.PERIOD_NAMES},
                "Rule-based advice (Gemini not used: " + {
                    "cap": "daily limit reached", "quota": "Gemini quota reached",
                    "rejected": "answer failed our checks"}.get(reason, "Gemini unavailable") + ")")

    today = deps.clock().strftime("%Y-%m-%d")
    if not deps.store.take_gemini_call(today, deps.s["GEMINI_DAILY_CAP"]):
        return fallback("cap")
    try:
        key = deps.gemini_key()
    except Exception:
        return fallback("key_unavailable")
    text, outcome = deps.gemini_call(A.build_prompt(facts, clothing), key,
                                     deps.s["GEMINI_MODEL"], deps.s["GEMINI_TIMEOUT_SECONDS"])
    if text is None:
        return fallback(outcome)
    checked = A.check_answer(text)
    if checked is None:
        return fallback("rejected")
    return checked, f"Google Gemini ({deps.s['GEMINI_MODEL']}), checked by our code"


def client_ip() -> str:
    """The visitor's address as seen by the ALB (rightmost X-Forwarded-For entry)."""
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[-1].strip() if xff else (request.remote_addr or "unknown")


def create_app(deps: Deps) -> Flask:
    app = Flask(__name__)
    s = deps.s
    genders = [(k, v[0]) for k, v in GENDER_CHOICES.items()]

    def form_page(errors=None, values=None, status=200, banner=None):
        today = deps.clock().date().isoformat()
        return render_template_string(FORM_HTML, errors=errors or {}, values=values or {},
                                      genders=genders, today=today, banner=banner), status

    @app.get("/health")
    def health():
        return {"ok": True, "tier": "advisor"}

    @app.get("/")
    def index():
        return form_page()

    @app.post("/report")
    def report():
        started = time.monotonic()
        visitor = visitor_id(client_ip(), s["VISITOR_SALT"])
        if not deps.store.allow("report", visitor, s["REPORT_LIMIT"], s["REPORT_WINDOW_SECONDS"]):
            log_event("rate_limited", kind="report")
            return form_page(status=429, banner="Too many requests from your connection. "
                                                "Please wait a few minutes and try again.")
        clean, errors = validate_form(request.form, deps.clock())
        if errors:
            log_event("report_rejected", fields=sorted(errors))
            values = {k: request.form.get(k, "") for k in ("date", "zip", "gender", "email")}
            return form_page(errors, values, 400)

        clothing = clothing_category(clean["gender"])
        day = clean["date"].isoformat()
        key = AdvisorStore.report_key(clean["zip"], day, clothing)
        saved = deps.store.get_report(key)
        if saved is not None:
            log_event("report", zip=clean["zip"], date=day, cache="saved",
                      ms=int((time.monotonic() - started) * 1000))
            return render_template_string(REPORT_HTML, r=saved, email=clean["email"],
                                          report_key=key, from_cache=True)

        try:
            resp = deps.http.get(f"{s['WEATHER_URL']}/facts",
                                 params={"zip": clean["zip"], "date": day},
                                 timeout=s["WEATHER_TIMEOUT_SECONDS"])
            body = resp.json()
            status = resp.status_code
        except Exception:
            status, body = 503, {}
        if status != 200:
            message = {
                404: "ZIP code not found. Check the five digits and try again.",
                400: body.get("message", "That date or ZIP code cannot be used."),
            }.get(status, "Weather data is unavailable right now. Please try again shortly.")
            log_event("report_failed", zip=clean["zip"], date=day, weather_status=status)
            field = "zip" if status == 404 else ("date" if body.get("error") == "date_out_of_range"
                                                 else None)
            values = {k: request.form.get(k, "") for k in ("date", "zip", "gender", "email")}
            return form_page({field: message} if field else {}, values,
                             404 if status == 404 else (400 if status == 400 else 503),
                             banner=None if field else message)

        advice, advice_source = get_advice(deps, body, clothing)
        r = {"facts": {k: body[k] for k in ("zip", "place", "state", "timezone", "date")},
             "partial": {p: body["periods"][p].get("partial_from") for p in A.PERIOD_NAMES},
             "rows": build_rows(body), "advice": advice, "advice_source": advice_source,
             "data_age_minutes": body.get("age_minutes", 0),
             "stale": body.get("cache") == "stale"}
        if not r["stale"] and not body.get("degraded"):
            deps.store.put_report(key, r)
        log_event("report", zip=clean["zip"], date=day, cache=body.get("cache"),
                  advice=("gemini" if advice_source.startswith("Google") else "fallback"),
                  ms=int((time.monotonic() - started) * 1000))
        return render_template_string(REPORT_HTML, r=r, email=clean["email"], report_key=key,
                                      from_cache=False)

    @app.post("/email")
    def email():
        visitor = visitor_id(client_ip(), s["VISITOR_SALT"])
        email_addr, _ = clean_email(request.form.get("email", ""))
        key = request.form.get("report_key", "")
        if not email_addr or not REPORT_KEY_RE.fullmatch(key):
            return render_template_string(STATUS_HTML, status="failed",
                                          message="That request was not valid."), 400
        # Two limits: per connection (many people may share one campus IP) and per
        # recipient address (stops one inbox being flooded, whoever asks).
        to_tag = recipient_tag(email_addr, s["VISITOR_SALT"])
        if not (deps.store.allow("email", visitor, s["EMAIL_IP_LIMIT"], s["EMAIL_WINDOW_SECONDS"])
                and deps.store.allow("email_to", to_tag, s["EMAIL_LIMIT"],
                                     s["EMAIL_WINDOW_SECONDS"])):
            log_event("rate_limited", kind="email")
            return render_template_string(STATUS_HTML, status="failed",
                                          message="Email limit reached. Please try again later."), 429
        saved = deps.store.get_report(key)
        if saved is None:
            return render_template_string(STATUS_HTML, status="failed",
                                          message="That report has expired. Please create it again."), 410
        f = saved["facts"]
        result = send_report(deps.sns, s["SNS_TOPIC_ARN"], email_addr, s["VISITOR_SALT"],
                             f"Weather Advisor: {f['place']}, {f['state']} on {f['date']}",
                             report_text(saved))
        log_event("email", status=result["status"])      # no address in the log
        return render_template_string(STATUS_HTML, status=result["status"],
                                      message=result["message"]), 200 if result["status"] != "failed" else 502

    return app


def settings_from_env(env) -> dict:
    """Typed settings from the environment the boot script writes from .env."""
    ints = ["GEMINI_DAILY_CAP", "REPORT_LIMIT", "REPORT_WINDOW_SECONDS", "EMAIL_LIMIT",
            "EMAIL_IP_LIMIT",
            "EMAIL_WINDOW_SECONDS", "KEY_CACHE_SECONDS", "REPORT_SECONDS"]
    floats = ["WEATHER_TIMEOUT_SECONDS", "GEMINI_TIMEOUT_SECONDS"]
    strs = ["WEATHER_URL", "GEMINI_MODEL", "GEMINI_PARAM_NAME", "SNS_TOPIC_ARN", "VISITOR_SALT"]
    out = {k: int(env[k]) for k in ints}
    out.update({k: float(env[k]) for k in floats})
    out.update({k: env[k] for k in strs})
    return out


def main() -> None:   # pragma: no cover - run on the instance by the boot script
    import os

    import boto3
    from waitress import serve

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    region = os.environ["AWS_REGION"]
    ssm = boto3.client("ssm", region_name=region)
    # The salt lives in SSM (SecureString), not in user-data. Read once at start-up.
    salt = ssm.get_parameter(Name=os.environ["SALT_PARAM_NAME"],
                             WithDecryption=True)["Parameter"]["Value"]
    s = settings_from_env({**os.environ, "VISITOR_SALT": salt})
    table = boto3.resource("dynamodb", region_name=region).Table(os.environ["ADVISOR_TABLE"])
    deps = Deps(AdvisorStore(table, s["REPORT_SECONDS"]), boto3.client("sns", region_name=region),
                ssm, s)
    serve(create_app(deps), host="0.0.0.0", port=int(os.environ["APP_PORT"]), threads=8)


if __name__ == "__main__":   # pragma: no cover
    main()
