"""SLI measurement against the deployed stack, compared with the SLOs in .env.

    pytest -m sli -v -s

Each request type is measured separately (p50, p95, share under the target), because a
new report calls NWS, Open-Meteo and Gemini and can never meet a 120 ms target, while a
health check or a saved report can.
"""
import pytest
import requests

from stack.config import load_dotenv
from tests.integration.live import form, post, save, summary, timed

pytestmark = pytest.mark.sli
NEW_ZIPS = ["98109", "85004", "02108", "75201", "33101"]


@pytest.fixture(scope="module")
def env():
    return load_dotenv(".env")


@pytest.fixture(scope="module")
def results():
    out = []
    yield out
    lines = ["| SLI | n | p50 (ms) | p95 (ms) | max (ms) | SLO (ms) | within SLO | result |",
             "|---|---|---|---|---|---|---|---|"]
    for r in out:
        lines.append(f"| {r['sli']} | {r['n']} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} | "
                     f"{r['slo_ms']} | {r['within_slo_pct']}% | {'PASS' if r['pass'] else 'FAIL'} |")
    table = "\n".join(lines)
    print("\n" + table)
    save("sli_results", out).with_suffix(".md").write_text(table + "\n")


@pytest.fixture(scope="module")
def base(settings, outputs):
    return outputs.alb_dns_name


@pytest.mark.parametrize("tier", ["weather", "advisor"])
def test_health_latency(base, settings, env, results, tier):
    url = f"http://{base}:{settings.tier(tier).listener_port}/health"
    requests.get(url, timeout=10)                         # warm the connection path once
    ms = [timed(requests.get, url, timeout=10)[1] for _ in range(50)]
    s = summary(f"{tier} /health", ms, int(env["SLO_HEALTH_MS"]))
    results.append(s)
    assert s["pass"]


def test_saved_report_latency(base, settings, env, results):
    url = f"http://{base}:{settings.advisor.listener_port}/report"
    post(url, form("60610"))                              # make it, then measure the repeats
    ms = [timed(post, url, form("60610"))[1] for _ in range(20)]
    results.append(summary("saved report (cache)", ms, int(env["SLO_HEALTH_MS"])))
    s = summary("saved report (cache)", ms, int(env["SLO_CACHED_MS"]))
    results.append(s)
    assert s["pass"]


def test_new_report_latency(base, settings, env, results):
    url = f"http://{base}:{settings.advisor.listener_port}/report"
    ms = []
    for z in NEW_ZIPS:
        r, t = timed(post, url, form(z))
        assert r.status_code == 200
        ms.append(t)
    s = summary("new report (NWS + Open-Meteo + Gemini)", ms, int(env["SLO_NEW_MS"]))
    results.append(s)
    assert s["pass"]
