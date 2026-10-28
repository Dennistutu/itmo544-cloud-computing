"""Break things on purpose and check the system degrades gracefully and recovers.

Slow (several minutes). Run with:  pytest -m chaos -s -v
"""
import subprocess
import time

import pytest
import requests

from stack.network import _group_rule
from stack.util import ignore_errors
from tests.conftest import ROOT
from tests.helpers import instance_ip, probe, tier_url, wait_until

pytestmark = pytest.mark.chaos


def _ssh(settings, ip: str, command: str) -> None:
    """Run one command on an instance over SSH with the lab key pair."""
    key = ROOT / f"{settings.key_name}.pem"
    subprocess.run(
        ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR", f"ec2-user@{ip}", command],
        check=True, timeout=90)


def test_database_cut_off_does_not_become_a_weather_outage(aws, settings, outputs, db):
    weather = tier_url(outputs, settings, "weather")
    rule = _group_rule(settings.db_port, settings.app_sg_id)

    aws.ec2.revoke_security_group_ingress(GroupId=db.db_security_group_id, IpPermissions=rule)
    try:
        # Open connections survive a rule change, so restart the service to force a new one.
        _ssh(settings, instance_ip(aws, "weather-app"), "sudo systemctl restart weather")
        time.sleep(5)
        response = requests.get(f"{weather}weather?zip=60616", timeout=40)
        body = response.json()
        print(f"\nwhile the database is cut off: status={response.status_code} "
              f"city={body.get('city')} cache={body.get('cache')}")
        assert response.status_code == 200
        assert body["cache"] == "unavailable"
        history = requests.get(f"{weather}history", timeout=20)
        print(f"/history while cut off: {history.status_code}")
        assert history.status_code == 503
    finally:
        with ignore_errors("InvalidPermission.Duplicate"):
            aws.ec2.authorize_security_group_ingress(GroupId=db.db_security_group_id, IpPermissions=rule)

    time.sleep(35)  # let the app's 30 second database pause run out
    cache = requests.get(f"{weather}weather?zip=60616", timeout=40).json()["cache"]
    print(f"after restoring the rule: cache={cache}")
    assert cache in ("hit", "miss")


def test_weather_outage_gives_a_fast_502_and_recovers_within_300s(aws, settings, outputs):
    weather = tier_url(outputs, settings, "weather")
    advisor = tier_url(outputs, settings, "advisor")
    assert probe(f"{weather}weather?zip=60616", timeout=10), "weather tier must be healthy first"

    group = aws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[settings.weather.asg_name])["AutoScalingGroups"][0]
    victim = next(i["InstanceId"] for i in group["Instances"] if i["LifecycleState"] == "InService")
    aws.autoscaling.terminate_instance_in_auto_scaling_group(
        InstanceId=victim, ShouldDecrementDesiredCapacity=False)
    started = time.time()
    print(f"\nterminated weather instance {victim}")

    # During the outage the advisor must answer 502 quickly: not hang, not crash.
    body, slowest = None, 0.0
    while time.time() - started < 150:
        t0 = time.time()
        response = requests.get(f"{advisor}advice?zip=33101", timeout=30)
        slowest = max(slowest, time.time() - t0)
        if response.status_code == 502:
            body = response.json()
            break
        time.sleep(3)
    print(f"advisor during the outage: {body} (slowest answer {slowest:.1f}s)")
    assert body == {"error": "weather_service_unavailable"}
    assert slowest < 25, "the advisor hung instead of failing fast"
    assert requests.get(advisor, timeout=30).status_code == 200, "advisor home page must stay up"

    recovered = wait_until(lambda: probe(f"{weather}weather?zip=60616", timeout=5),
                           timeout=300 - (time.time() - started), interval=5)
    elapsed = time.time() - started
    print(f"weather service recovered after {elapsed:.0f} seconds")
    assert recovered and elapsed <= 300

    cache = requests.get(f"{weather}weather?zip=60616", timeout=40).json()["cache"]
    print(f"first answer from the new instance for 60616: cache={cache} "
          "(a hit means the new instance used data the old one saved)")
