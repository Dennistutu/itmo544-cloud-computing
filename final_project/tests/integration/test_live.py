"""Integration tests against the deployed stack (Section 11.2).

    python create_stack.py      # then wait for healthy targets
    pytest -m integration -v
"""
import base64
import gzip
import socket

import pytest
import requests

from stack.config import load_dotenv
from tests.integration.live import form, post, save, timed

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def urls(settings, outputs):
    dns = outputs.alb_dns_name
    return {"advisor": f"http://{dns}:{settings.advisor.listener_port}",
            "weather": f"http://{dns}:{settings.weather.listener_port}"}


@pytest.fixture(scope="module")
def slo():
    env = load_dotenv(".env")
    return {k: int(env[k]) for k in ("SLO_HEALTH_MS", "SLO_CACHED_MS", "SLO_NEW_MS")}


# ------------------------------------------------------------------- availability
def test_both_target_groups_healthy(aws, outputs):
    for tier in ("weather", "advisor"):
        states = [t["TargetHealth"]["State"] for t in aws.elbv2.describe_target_health(
            TargetGroupArn=outputs.tg_arn(tier))["TargetHealthDescriptions"]]
        assert states and all(s == "healthy" for s in states), (tier, states)


@pytest.mark.parametrize("tier", ["weather", "advisor"])
def test_health_through_alb(urls, tier):
    r = requests.get(urls[tier] + "/health", timeout=10)
    assert r.status_code == 200 and r.json()["tier"] == tier


# ------------------------------------------------------------------ real reports
def test_new_report_for_real_zip_and_date(urls, slo):
    r, ms = timed(post, urls["advisor"] + "/report", form("60601"))
    html = r.text
    assert r.status_code == 200, html[:300]
    for label in ("Temperature", "Wind speed", "Visibility", "UV", "Humidity", "Rain", "Snow",
                  "Suggested road conditions", "Suggested dressing", "Suggested accessories"):
        assert label in html
    assert "Source: NWS" in html and "Source: Open-Meteo" in html
    assert ms < slo["SLO_NEW_MS"], f"new report took {ms:.0f} ms"
    save("integration_new_report", {"zip": "60601", "ms": round(ms)})


def test_repeat_is_served_from_saved_report(urls, slo):
    post(urls["advisor"] + "/report", form("60602"))
    r, ms = timed(post, urls["advisor"] + "/report", form("60602"))
    assert r.status_code == 200 and "saved report" in r.text
    assert ms < slo["SLO_CACHED_MS"], f"cached report took {ms:.0f} ms"
    save("integration_cached_report", {"zip": "60602", "ms": round(ms)})


# --------------------------------------------------------------------- bad input
@pytest.mark.parametrize("bad", ["M5V 3L9", "SW1A 1AA", "6061", "606161", "abcde", ""])
def test_non_us_codes_refused_by_server(urls, bad):
    r = post(urls["advisor"] + "/report", form(bad))
    assert r.status_code == 400 and "US locations only" in r.text


def test_unknown_zip_not_found(urls):
    r = post(urls["advisor"] + "/report", form("00000"))
    assert r.status_code == 404 and "ZIP code not found" in r.text


def test_hostile_input_is_escaped(urls):
    r = post(urls["advisor"] + "/report", form("<script>alert(1)</script>"))
    assert "<script>alert(1)" not in r.text


def test_date_outside_window_refused(urls):
    r = post(urls["advisor"] + "/report", form("60616", day="2030-01-01"))
    assert r.status_code == 400


# ------------------------------------------------------------------ network rules
def running_ips(aws):
    res = aws.ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["weather-app", "advisor-app"]},
        {"Name": "instance-state-name", "Values": ["running"]}])["Reservations"]
    return [i["PublicIpAddress"] for r in res for i in r["Instances"] if i.get("PublicIpAddress")]


def test_app_port_not_reachable_directly(aws, settings):
    ips = running_ips(aws)
    assert ips
    for ip in ips:
        with pytest.raises(OSError):
            socket.create_connection((ip, settings.app_port), timeout=5).close()


def test_app_security_group_allows_only_the_alb(aws, settings, outputs):
    group = aws.ec2.describe_security_groups(GroupIds=[settings.app_sg_id])["SecurityGroups"][0]
    rules = [p for p in group["IpPermissions"] if p.get("FromPort") == settings.app_port]
    assert len(rules) == 1 and rules[0]["IpRanges"] == []
    assert [g["GroupId"] for g in rules[0]["UserIdGroupPairs"]] == [outputs.alb_security_group_id]


# ---------------------------------------------------------- roles: allowed and denied
def decisions(aws, role_name, checks):
    arn = aws.iam.get_role(RoleName=role_name)["Role"]["Arn"]
    out = {}
    for action, resource in checks:
        r = aws.iam.simulate_principal_policy(PolicySourceArn=arn, ActionNames=[action],
                                              ResourceArns=[resource])
        out[(action, resource)] = r["EvaluationResults"][0]["EvalDecision"]
    return out


def test_roles_allowed_and_denied(aws, settings):
    acct = aws.sts.get_caller_identity()["Account"]
    facts = settings.table_arn(acct, settings.facts_table)
    adv_t = settings.table_arn(acct, settings.advisor_table)
    key = settings.parameter_arn(acct, settings.gemini_param_name)
    topic = settings.topic_arn(acct)
    other_topic = f"arn:aws:sns:{settings.region}:{acct}:someone-elses-topic"
    expected = {
        settings.weather.role_name: [
            ("dynamodb:GetItem", facts, "allowed"), ("dynamodb:PutItem", facts, "allowed"),
            ("dynamodb:DeleteTable", facts, "implicitDeny"), ("dynamodb:Scan", facts, "implicitDeny"),
            ("dynamodb:GetItem", adv_t, "implicitDeny"), ("ssm:GetParameter", key, "implicitDeny"),
            ("sns:Publish", topic, "implicitDeny")],
        settings.advisor.role_name: [
            ("dynamodb:UpdateItem", adv_t, "allowed"), ("ssm:GetParameter", key, "allowed"),
            ("sns:Publish", topic, "allowed"), ("sns:Subscribe", topic, "allowed"),
            ("sns:Publish", other_topic, "implicitDeny"), ("sns:DeleteTopic", topic, "implicitDeny"),
            ("dynamodb:GetItem", facts, "implicitDeny"), ("dynamodb:Scan", adv_t, "implicitDeny"),
            ("ssm:PutParameter", key, "implicitDeny")],
    }
    results = {}
    for role, checks in expected.items():
        got = decisions(aws, role, [(a, r) for a, r, _ in checks])
        for a, r, want in checks:
            results[f"{role} {a} {r.split(':')[-1]}"] = got[(a, r)]
            assert got[(a, r)] == want, (role, a, r, got[(a, r)])
    save("integration_iam_decisions", results)


# ------------------------------------------------------------------ secrets, data
def test_no_secret_in_live_user_data(aws, settings):
    key = aws.ssm.get_parameter(Name=settings.gemini_param_name, WithDecryption=True)["Parameter"]["Value"]
    for tier in settings.tiers:
        v = aws.ec2.describe_launch_template_versions(LaunchTemplateName=tier.lt_name,
                                                      Versions=["$Latest"])["LaunchTemplateVersions"][0]
        text = gzip.decompress(base64.b64decode(v["LaunchTemplateData"]["UserData"])).decode()
        assert key not in text and text.startswith("#!/usr/bin/env python3")


def test_tables_have_ttl_and_no_backups(aws, settings):
    for name in (settings.facts_table, settings.advisor_table):
        ttl = aws.dynamodb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
        assert ttl["TimeToLiveStatus"] in ("ENABLED", "ENABLING")
        pitr = aws.dynamodb.describe_continuous_backups(TableName=name)[
            "ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"]
        assert pitr == "DISABLED"
