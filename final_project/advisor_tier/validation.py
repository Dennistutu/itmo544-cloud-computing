"""Server-side validation of the four form fields (Section 7.1). Pure functions.

The browser checks are a convenience; these decide.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

ZIP_RE = re.compile(r"^[0-9]{5}$")   # ASCII digits only (\d would accept other scripts)
# Deliberately simple and strict: one @, a dot in the domain, no spaces or quotes.
EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
                      r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
EMAIL_MAX = 254

# Decision 14. A short, inclusive list. The value sent to Gemini is the clothing
# category on the right, never an identity.
GENDER_CHOICES = {
    "woman": ("Woman", "womenswear"),
    "man": ("Man", "menswear"),
    "neutral": ("Non-binary / prefer not to say", "neutral"),
}

# US ZIP codes span UTC-11 (American Samoa) to UTC+10 (Guam). Before we know the
# ZIP's time zone, accept a date that is "today" somewhere in that range; the data
# tier then checks the exact window in the ZIP's own time zone.
EARLIEST_OFFSET, LATEST_OFFSET = timedelta(hours=-11), timedelta(hours=10)

US_ONLY = ("This service covers US locations only. Enter a five digit US ZIP code, "
           "for example 60616.")


def clean_zip(raw: str) -> tuple[str | None, str | None]:
    zip_code = (raw or "").strip()
    if not ZIP_RE.fullmatch(zip_code):
        return None, US_ONLY
    return zip_code, None


def clean_date(raw: str, now_utc: datetime) -> tuple[date | None, str | None]:
    try:
        day = date.fromisoformat((raw or "").strip())
    except ValueError:
        return None, "Enter a real calendar date (YYYY-MM-DD)."
    earliest = (now_utc + EARLIEST_OFFSET).date()
    latest = (now_utc + LATEST_OFFSET).date() + timedelta(days=6)
    if not earliest <= day <= latest:
        return None, "Choose a date from today to 6 days ahead."
    return day, None


def clean_gender(raw: str) -> tuple[str | None, str | None]:
    key = (raw or "").strip().lower()
    if key not in GENDER_CHOICES:
        return None, "Choose one of the listed options."
    return key, None


def clean_email(raw: str) -> tuple[str | None, str | None]:
    email = (raw or "").strip().lower()
    if not email or len(email) > EMAIL_MAX or not EMAIL_RE.fullmatch(email):
        return None, "Enter a valid email address."
    local = email.split("@", 1)[0]
    if len(local) > 64 or ".." in email:
        return None, "Enter a valid email address."
    return email, None


def validate_form(form: dict, now_utc: datetime | None = None) -> tuple[dict, dict]:
    """Returns (clean values, errors by field). Empty errors means every field passed."""
    now_utc = now_utc or datetime.now(timezone.utc)
    clean, errors = {}, {}
    for field, fn in (("zip", clean_zip), ("gender", clean_gender), ("email", clean_email)):
        value, err = fn(form.get(field, ""))
        clean[field], errors[field] = value, err
    clean["date"], errors["date"] = clean_date(form.get("date", ""), now_utc)
    return clean, {k: v for k, v in errors.items() if v}


def clothing_category(gender_key: str) -> str:
    return GENDER_CHOICES[gender_key][1]
