"""Helpers for tests against the deployed stack. Results are also written to evidence/."""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence"


def tomorrow(tz="America/Chicago") -> str:
    return (datetime.now(ZoneInfo(tz)) + timedelta(days=1)).date().isoformat()


def form(zip_code, day=None, gender="neutral", email="integration-test@example.com"):
    return {"date": day or tomorrow(), "zip": zip_code, "gender": gender, "email": email}


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    resp = fn(*args, **kwargs)
    return resp, (time.perf_counter() - start) * 1000


def percentile(values, p):
    """Nearest-rank percentile (p in 0..100)."""
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return ordered[k]


def summary(name, ms, slo_ms):
    return {"sli": name, "n": len(ms), "p50_ms": round(statistics.median(ms), 1),
            "p95_ms": round(percentile(ms, 95), 1), "max_ms": round(max(ms), 1),
            "slo_ms": slo_ms, "within_slo_pct": round(100 * sum(m <= slo_ms for m in ms) / len(ms), 1),
            "pass": percentile(ms, 95) <= slo_ms}


def save(name: str, data) -> Path:
    EVIDENCE.mkdir(exist_ok=True)
    path = EVIDENCE / f"{name}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def post(url, data, timeout=45):
    return requests.post(url, data=data, timeout=timeout)
