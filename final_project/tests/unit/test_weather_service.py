"""Weather tier service: caching, failures and status codes, with recorded fixtures."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from weather_tier.service import FactsCache, create_app

FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)


def raw(name):
    d = json.loads((FIX / f"{name}.json").read_text())
    return d["status"], d["body"]


class FakeSources:
    """Replays the recorded responses; any source can be switched off to simulate an outage."""

    def __init__(self, down=()):
        self.down, self.calls = set(down), []

    def _r(self, stage, name):
        self.calls.append(stage)
        return (0, None) if stage in self.down else raw(name)

    def zip_lookup(self, z):
        return self._r("zip", f"zip_{z}")

    def nws_point(self, lat, lon):
        z = {41: "60616", 46: "55802", 25: "33101"}[int(lat)]
        return self._r("points", f"nws_points_{z}")

    def nws_grid(self, url):
        z = {"LOT": "60616", "DLH": "55802", "MFL": "33101"}[url.split("/")[4]]
        return self._r("grid", f"nws_griddata_{z}")

    def open_meteo(self, lat, lon):
        z = {41: "60616", 46: "55802", 25: "33101"}[int(lat)]
        return self._r("om", f"openmeteo_{z}")


class DictTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        return {"Item": self.items[Key["pk"]]} if Key["pk"] in self.items else {}

    def put_item(self, Item):
        self.items[Item["pk"]] = Item


@pytest.fixture
def setup():
    clock = {"now": NOW}
    sources, table = FakeSources(), DictTable()
    app = create_app(sources, FactsCache(table, 1800, 6 * 3600), clock=lambda: clock["now"])
    return app.test_client(), sources, table, clock


def test_health(setup):
    client, *_ = setup
    assert client.get("/health").json == {"ok": True, "tier": "weather"}


@pytest.mark.parametrize("bad", ["M5V3L9", "SW1A 1AA", "6061", "606161", "abcde", "", "1" * 500])
def test_bad_zip_refused_with_no_outside_call(setup, bad):
    client, sources, *_ = setup
    r = client.get("/facts", query_string={"zip": bad, "date": "2026-11-18"})
    assert r.status_code == 400 and r.json["error"] == "invalid_zip"
    assert sources.calls == []


@pytest.mark.parametrize("bad", ["2026-02-30", "18/11/2026", "tomorrow", ""])
def test_bad_date_refused(setup, bad):
    client, sources, *_ = setup
    r = client.get("/facts", query_string={"zip": "60616", "date": bad})
    assert r.status_code == 400 and sources.calls == []


def test_unknown_zip_is_404(setup):
    client, *_ = setup
    r = client.get("/facts", query_string={"zip": "00000", "date": "2026-11-18"})
    assert r.status_code == 404 and r.json["error"] == "zip_not_found"


@pytest.mark.parametrize("day", ["2026-11-15", "2026-11-23"])
def test_date_outside_local_window(setup, day):
    client, *_ = setup
    r = client.get("/facts", query_string={"zip": "60616", "date": day})
    assert r.status_code == 400 and r.json["error"] == "date_out_of_range"


def test_miss_then_hit(setup):
    client, sources, *_ = setup
    q = {"zip": "60616", "date": "2026-11-18"}
    first = client.get("/facts", query_string=q)
    calls_after_first = len(sources.calls)
    second = client.get("/facts", query_string=q)
    assert first.json["cache"] == "miss" and second.json["cache"] == "hit"
    assert len(sources.calls) == calls_after_first          # no outside call on a hit
    assert first.json["periods"] == second.json["periods"]


def test_stale_copy_used_when_sources_down(setup):
    client, sources, _, clock = setup
    q = {"zip": "60616", "date": "2026-11-18"}
    client.get("/facts", query_string=q)
    clock["now"] = NOW + timedelta(hours=1)                  # past the 30 minute freshness
    sources.down = {"zip"}
    r = client.get("/facts", query_string=q)
    assert r.status_code == 200 and r.json["cache"] == "stale" and r.json["age_minutes"] == 60


def test_unavailable_without_cache(setup):
    client, sources, *_ = setup
    sources.down = {"grid", "om"}
    r = client.get("/facts", query_string={"zip": "60616", "date": "2026-11-18"})
    assert r.status_code == 503 and r.json["error"] == "weather_unavailable"


def test_one_source_down_gives_degraded_report(setup):
    client, sources, *_ = setup
    sources.down = {"om"}
    r = client.get("/facts", query_string={"zip": "60616", "date": "2026-11-18"})
    items = r.json["periods"]["morning"]["items"]
    assert r.status_code == 200 and items["temperature"]["source"] == "NWS"
    assert items["uv"]["value"] is None and items["uv"]["reason"]


@mock_aws
def test_facts_cache_with_dynamodb_ttl():
    ddb = boto3.resource("dynamodb", region_name="us-east-2")
    table = ddb.create_table(TableName="t-facts", BillingMode="PAY_PER_REQUEST",
                             KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])
    cache = FactsCache(table, 1800, 3600)
    cache.put("60616#2026-11-18", {"a": 1}, 1000.0)
    assert cache.get("60616#2026-11-18", 1100.0) == ({"a": 1}, 100.0)
    assert cache.get("60616#2026-11-18", 1000.0 + 3600) == (None, None)   # expired
