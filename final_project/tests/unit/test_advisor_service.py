"""Advisor service with moto (DynamoDB, SNS, SSM): the full request flows offline."""
import json
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from advisor_tier import advice as A
from advisor_tier.emailer import filter_policy, recipient_tag, send_report
from advisor_tier.service import Deps, create_app
from advisor_tier.store import AdvisorStore
from weather_tier.facts import build_facts

REGION = "us-east-2"
FIX = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
FORM = {"date": "2026-11-18", "zip": "60616", "gender": "neutral", "email": "Alice@Example.com"}


def facts_doc():
    b = lambda n: json.loads((FIX / f"{n}.json").read_text())["body"]
    d = build_facts("60616", b("zip_60616")["places"][0], b("nws_points_60616")["properties"],
                    b("nws_griddata_60616")["properties"], b("openmeteo_60616"),
                    date(2026, 11, 18), datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
    return {**d, "cache": "miss", "age_minutes": 0}


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.status == 0:
            raise TimeoutError("weather tier down")
        return FakeResp(self.status, self.body if self.body is not None else facts_doc())


GOOD = json.dumps({p: {"road": "Roads look fine.", "dressing": "Wear a jacket.",
                       "accessories": "Carry sunglasses."} for p in ("morning", "night")})


def make_table(ddb):
    return ddb.create_table(TableName="t-advisor", BillingMode="PAY_PER_REQUEST",
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])


@pytest.fixture
def env():
    with mock_aws():
        table = make_table(boto3.resource("dynamodb", region_name=REGION))
        sns = boto3.client("sns", region_name=REGION)
        topic = sns.create_topic(Name="t-reports")["TopicArn"]
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name="/t/gemini", Type="SecureString", Value="test-key")
        settings = {"WEATHER_URL": "http://weather", "GEMINI_MODEL": "m", "GEMINI_PARAM_NAME": "/t/gemini",
                    "SNS_TOPIC_ARN": topic, "VISITOR_SALT": "salt", "GEMINI_DAILY_CAP": 5,
                    "REPORT_LIMIT": 20, "REPORT_WINDOW_SECONDS": 600, "EMAIL_LIMIT": 3,
                    "EMAIL_WINDOW_SECONDS": 3600, "KEY_CACHE_SECONDS": 60, "REPORT_SECONDS": 1800,
                    "WEATHER_TIMEOUT_SECONDS": 20.0, "GEMINI_TIMEOUT_SECONDS": 12.0}
        calls = []

        def gemini(prompt, key, model, timeout):
            calls.append(prompt)
            assert key == "test-key"
            return GOOD, "ok"

        http = FakeHttp()
        deps = Deps(AdvisorStore(table, 1800), sns, ssm, settings, http=http,
                    gemini_call=gemini, clock=lambda: NOW)
        yield {"client": create_app(deps).test_client(), "deps": deps, "http": http,
               "gemini_calls": calls, "sns": sns, "topic": topic, "table": table}


def test_form_page_and_privacy_notice(env):
    html = env["client"].get("/").get_data(as_text=True)
    assert "only to tailor the clothing suggestion" in html and "never sent to it" in html


def test_full_report_has_all_ten_rows_and_sources(env):
    r = env["client"].post("/report", data=FORM)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for label in ("Temperature", "Wind speed", "Visibility", "UV", "Humidity", "Rain", "Snow",
                  "Suggested road conditions", "Suggested dressing", "Suggested accessories"):
        assert label in html
    assert "Source: NWS" in html and "Source: Open-Meteo" in html and "Google Gemini" in html


def test_saved_report_answers_repeat_without_any_outside_call(env):
    env["client"].post("/report", data=FORM)
    env["client"].post("/report", data=FORM)
    assert env["http"].calls == 1 and len(env["gemini_calls"]) == 1


def test_non_us_code_refused_before_any_outside_call(env):
    r = env["client"].post("/report", data={**FORM, "zip": "M5V 3L9"})
    assert r.status_code == 400 and "US locations only" in r.get_data(as_text=True)
    assert env["http"].calls == 0 and env["gemini_calls"] == []


def test_input_is_escaped_when_echoed(env):
    r = env["client"].post("/report", data={**FORM, "zip": "<script>x</script>"})
    html = r.get_data(as_text=True)
    assert "<script>x" not in html and "&lt;script&gt;" in html


def test_zip_not_found_message_and_no_gemini(env):
    env["http"].status, env["http"].body = 404, {"error": "zip_not_found"}
    r = env["client"].post("/report", data={**FORM, "zip": "00000"})
    assert r.status_code == 404 and "ZIP code not found" in r.get_data(as_text=True)
    assert env["gemini_calls"] == []


def test_weather_tier_down_gives_clear_message(env):
    env["http"].status = 0
    r = env["client"].post("/report", data=FORM)
    assert r.status_code == 503 and "Weather data is unavailable" in r.get_data(as_text=True)


def test_gemini_cap_reached_uses_labeled_fallback(env):
    env["deps"].s["GEMINI_DAILY_CAP"] = 0
    html = env["client"].post("/report", data=FORM).get_data(as_text=True)
    assert "Rule-based advice" in html and "daily limit reached" in html
    assert env["gemini_calls"] == []


def test_gemini_bad_answer_uses_fallback(env):
    env["deps"].gemini_call = lambda *a: ('{"morning": "<b>hi</b>"}', "ok")
    html = env["client"].post("/report", data=FORM).get_data(as_text=True)
    assert "Rule-based advice" in html and "<b>hi" not in html


def test_gemini_quota_error_uses_fallback(env):
    env["deps"].gemini_call = lambda *a: (None, "quota")
    html = env["client"].post("/report", data=FORM).get_data(as_text=True)
    assert "Gemini quota reached" in html


def test_report_rate_limit(env):
    env["deps"].s["REPORT_LIMIT"] = 2
    codes = [env["client"].post("/report", data=FORM).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_no_email_or_gender_in_logs(env, caplog):
    caplog.set_level("INFO")
    env["client"].post("/report", data=FORM)
    env["client"].post("/email", data={"email": FORM["email"],
                                       "report_key": "report#60616#2026-11-18#neutral"})
    text = caplog.text.lower()
    assert "alice@example.com" not in text and "neutral" not in text and "gender" not in text


def test_saved_report_does_not_contain_email(env):
    env["client"].post("/report", data=FORM)
    items = env["table"].scan()["Items"]
    assert items and all("alice" not in json.dumps(i, default=str).lower() for i in items)


# --------------------------------------------------------------------------- email
def test_email_consent_then_send(env):
    env["client"].post("/report", data=FORM)
    data = {"email": FORM["email"], "report_key": "report#60616#2026-11-18#neutral"}
    first = env["client"].post("/email", data=data).get_data(as_text=True)
    assert "waiting for confirmation" in first          # subscribe -> confirmation email
    second = env["client"].post("/email", data=data).get_data(as_text=True)
    assert "sent" in second                             # moto treats the subscription as confirmed


def test_email_limit(env):
    env["client"].post("/report", data=FORM)
    data = {"email": FORM["email"], "report_key": "report#60616#2026-11-18#neutral"}
    codes = [env["client"].post("/email", data=data).status_code for _ in range(4)]
    assert codes[-1] == 429


def test_email_with_forged_report_key_refused(env):
    r = env["client"].post("/email", data={"email": FORM["email"], "report_key": "report#*"})
    assert r.status_code == 400


class PendingSns:
    def list_subscriptions_by_topic(self, **kw):
        return {"Subscriptions": [{"Protocol": "email", "Endpoint": "a@b.com",
                                   "SubscriptionArn": "PendingConfirmation"}]}

    def publish(self, **kw):
        raise AssertionError("must not publish before confirmation")


def test_unconfirmed_address_never_receives_report():
    out = send_report(PendingSns(), "arn", "a@b.com", "salt", "s", "t")
    assert out["status"] == "pending"


def test_service_error_is_reported_honestly():
    class Broken:
        def list_subscriptions_by_topic(self, **kw):
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "Throttled", "Message": "x"}}, "List")
    assert send_report(Broken(), "arn", "a@b.com", "salt", "s", "t")["status"] == "failed"


@mock_aws
def test_two_recipients_never_see_each_others_report():
    """The filter policy our code attaches lets through only that recipient's tag.
    Two SQS queues stand in for two inboxes, with exactly the policies send_report uses."""
    sns, sqs = boto3.client("sns", region_name=REGION), boto3.client("sqs", region_name=REGION)
    topic = sns.create_topic(Name="t-reports")["TopicArn"]
    inbox = {}
    for who in ("alice@example.com", "bob@example.com"):
        url = sqs.create_queue(QueueName=who.split("@")[0])["QueueUrl"]
        arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=arn,
                      Attributes={"FilterPolicy": filter_policy(recipient_tag(who, "salt")),
                                  "FilterPolicyScope": "MessageAttributes", "RawMessageDelivery": "true"})
        inbox[who] = url
    sns.publish(TopicArn=topic, Message="report for alice",
                MessageAttributes={"recipient": {"DataType": "String",
                                                 "StringValue": recipient_tag("alice@example.com", "salt")}})
    got = {w: [m["Body"] for m in sqs.receive_message(QueueUrl=u, MaxNumberOfMessages=10).get("Messages", [])]
           for w, u in inbox.items()}
    assert got == {"alice@example.com": ["report for alice"], "bob@example.com": []}


@mock_aws
def test_email_subscription_carries_its_own_filter_policy():
    sns = boto3.client("sns", region_name=REGION)
    topic = sns.create_topic(Name="t-reports")["TopicArn"]
    send_report(sns, topic, "alice@example.com", "salt", "s", "t")
    sub = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"][0]
    attrs = sns.get_subscription_attributes(SubscriptionArn=sub["SubscriptionArn"])["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == {"recipient": [recipient_tag("alice@example.com", "salt")]}


# ------------------------------------------------------------------- daily cap race
@mock_aws
def test_daily_cap_holds_when_requests_race():
    table = make_table(boto3.resource("dynamodb", region_name=REGION))
    store = AdvisorStore(table, 60)
    results = []
    lock = threading.Lock()

    def worker():
        ok = store.take_gemini_call("2026-11-17", cap=5)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 5 and store.gemini_count("2026-11-17") == 5
