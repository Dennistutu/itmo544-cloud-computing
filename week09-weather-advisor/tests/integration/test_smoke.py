"""The smoke test from Part G3, as pytest: both tiers, through the load balancer.

Run after the targets are healthy:  pytest -m integration -v
"""
import pytest
import requests

from tests.helpers import tier_url

pytestmark = pytest.mark.integration

ZIP = "60616"


@pytest.fixture(scope="module")
def weather_url(settings, outputs):
    return tier_url(outputs, settings, "weather")


@pytest.fixture(scope="module")
def advisor_url(settings, outputs):
    return tier_url(outputs, settings, "advisor")


def _get(url: str, timeout: float = 30):
    response = requests.get(url, timeout=timeout)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


CANDIDATE_ZIPS = [ZIP, "10001", "98101", "80202", "94103", "30303", "02108", "85004", "55401", "75201"]


def test_weather_called_twice_is_a_miss_then_a_hit(weather_url):
    """Starts with 60616. On a re-run within CACHE_SECONDS it moves on to a zip not asked for recently."""
    for zip_code in CANDIDATE_ZIPS:
        _, first = _get(f"{weather_url}weather?zip={zip_code}")
        if first.get("cache") != "miss":
            continue
        _, second = _get(f"{weather_url}weather?zip={zip_code}")
        print(f"\nzip {zip_code}: first call cache={first['cache']}, second call cache={second.get('cache')}")
        assert second["cache"] == "hit"
        return
    pytest.fail("every candidate zip is already cached. Wait CACHE_SECONDS and run again.")


def test_weather_tier_returns_a_forecast(weather_url):
    status, body = _get(f"{weather_url}weather?zip={ZIP}")
    assert status == 200 and "forecast" in body


def test_history_lists_the_saved_lookup(weather_url):
    status, body = _get(f"{weather_url}history")
    assert status == 200 and body["count"] >= 1
    assert ZIP in [row["zip"] for row in body["lookups"]]


def test_weather_rejects_a_bad_zip_with_400(weather_url):
    status, _ = _get(f"{weather_url}weather?zip=abc")
    assert status == 400


def test_weather_returns_404_for_an_unknown_zip(weather_url):
    status, _ = _get(f"{weather_url}weather?zip=00000")
    assert status == 404


def test_advisor_returns_clothing_advice(advisor_url):
    status, body = _get(f"{advisor_url}advice?zip={ZIP}", timeout=45)
    assert status == 200 and body.get("advice")
    print(f"\nsource={body.get('source')} model={body.get('model')}\nadvice: {body['advice']}")


def test_advice_came_from_gemini_not_the_fallback(advisor_url):
    _, body = _get(f"{advisor_url}advice?zip={ZIP}", timeout=45)
    assert body.get("source") == "gemini", "fallback means the key, quota or model name needs a look"


def test_advisor_rejects_a_bad_zip_with_400(advisor_url):
    status, _ = _get(f"{advisor_url}advice?zip=abc")
    assert status == 400


def test_advisor_home_page_loads(advisor_url):
    response = requests.get(advisor_url, timeout=30)
    assert response.status_code == 200 and "<form" in response.text
