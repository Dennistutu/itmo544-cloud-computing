"""Chaos tests against the deployed stack (Section 11.2). Slow; they change live state.

    pytest -m chaos -v -s
"""
import time

import boto3
import pytest
import requests

from tests.integration.live import form, post, save

pytestmark = pytest.mark.chaos
FAULT_KEY = "chaos#sources"


@pytest.fixture(scope="module")
def urls(settings, outputs):
    dns = outputs.alb_dns_name
    return {"advisor": f"http://{dns}:{settings.advisor.listener_port}",
            "weather": f"http://{dns}:{settings.weather.listener_port}"}


@pytest.fixture(scope="module")
def tables(settings):
    ddb = boto3.resource("dynamodb", region_name=settings.region)
    return ddb.Table(settings.facts_table), ddb.Table(settings.advisor_table)


@pytest.fixture
def fault(tables):
    """Switch weather sources off for a few minutes, and always switch them back on."""
    facts, _ = tables

    def set_down(*stages):
        facts.put_item(Item={"pk": FAULT_KEY, "down": list(stages),
                             "expires_at": int(time.time()) + 300})
    yield set_down
    facts.delete_item(Key={"pk": FAULT_KEY})


# ------------------------------------------------------------------ Gemini cap
def test_gemini_cap_reached_gives_labeled_fallback(urls, tables, settings):
    _, advisor = tables
    key = {"pk": "gemini#" + time.strftime("%Y-%m-%d", time.gmtime())}
    before = advisor.get_item(Key=key).get("Item")
    advisor.put_item(Item={**key, "count": settings.gemini_daily_cap,
                           "expires_at": int(time.time()) + 2 * 86400})
    try:
        r = post(urls["advisor"] + "/report", form("10001"))
        assert r.status_code == 200
        assert "Rule-based advice" in r.text and "daily limit reached" in r.text
        save("chaos_gemini_cap", {"cap": settings.gemini_daily_cap, "advice": "rule-based fallback"})
    finally:
        if before:
            advisor.put_item(Item=before)
        else:
            advisor.delete_item(Key=key)


# --------------------------------------------------------------- data sources
def test_one_source_down_gives_degraded_report(urls, fault):
    fault("open_meteo")
    r = post(urls["advisor"] + "/report", form("98101"))
    assert r.status_code == 200
    assert "Not available (no forecast source covers this period)" in r.text   # UV and visibility
    assert "Source: NWS" in r.text                                             # the rest still works
    save("chaos_source_down", {"down": ["open_meteo"], "result": "degraded report, UV not available"})


def test_all_forecasts_down_without_cache_says_unavailable(urls, fault):
    fault("nws_grid", "open_meteo")
    r = post(urls["advisor"] + "/report", form("80202"))
    assert r.status_code == 503 and "Weather data is unavailable" in r.text


def test_all_forecasts_down_with_old_cache_shows_its_age(urls, fault, tables):
    facts, _ = tables
    day = form("30303")["date"]
    requests.get(urls["weather"] + "/facts", params={"zip": "30303", "date": day}, timeout=30)
    facts.update_item(Key={"pk": f"30303#{day}"},          # make the cached copy an hour old
                      UpdateExpression="SET fetched_at = :t",
                      ExpressionAttributeValues={":t": str(time.time() - 3600)})
    fault("zip_lookup", "nws_points", "nws_grid", "open_meteo")
    body = requests.get(urls["weather"] + "/facts", params={"zip": "30303", "date": day},
                        timeout=30).json()
    assert body["cache"] == "stale" and body["age_minutes"] >= 59
    page = post(urls["advisor"] + "/report", form("30303"))
    assert "minutes old" in page.text
    save("chaos_stale_cache", {"age_minutes": body["age_minutes"], "cache": body["cache"]})


# -------------------------------------------------------------- instance loss
@pytest.mark.parametrize("tier", ["advisor", "weather"])
def test_terminated_instance_recovers_within_slo(aws, settings, outputs, urls, tier):
    tg = outputs.tg_arn(tier)
    victims = [t["Target"]["Id"] for t in aws.elbv2.describe_target_health(
        TargetGroupArn=tg)["TargetHealthDescriptions"] if t["TargetHealth"]["State"] == "healthy"]
    assert victims, "no healthy instance to terminate"
    aws.ec2.terminate_instances(InstanceIds=victims[:1])
    start = time.time()
    first_fail = None
    while time.time() - start < settings.recovery_slo_seconds + 300:
        healthy = [t["Target"]["Id"] for t in aws.elbv2.describe_target_health(
            TargetGroupArn=tg)["TargetHealthDescriptions"] if t["TargetHealth"]["State"] == "healthy"]
        try:
            ok = requests.get(urls[tier] + "/health", timeout=5).status_code == 200
        except requests.RequestException:
            ok = False
        if not ok and first_fail is None:
            first_fail = time.time() - start
        if ok and healthy and victims[0] not in healthy:
            break
        time.sleep(5)
    seconds = round(time.time() - start)
    print(f"\n{tier}: replaced instance {victims[0]}, healthy again after {seconds} s "
          f"(SLO {settings.recovery_slo_seconds} s)")
    save(f"chaos_recovery_{tier}", {"terminated": victims[0], "recovery_seconds": seconds,
                                    "first_failed_probe_s": first_fail,
                                    "slo_seconds": settings.recovery_slo_seconds})
    assert seconds <= settings.recovery_slo_seconds
