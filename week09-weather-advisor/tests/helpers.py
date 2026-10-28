"""Small helpers shared by the integration and chaos tests."""
import socket
import time

import requests


def probe(url: str, timeout: float = 3.0) -> bool:
    """True if the URL answers 200 within the timeout."""
    try:
        return requests.get(url, timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def wait_until(check, timeout: float = 300, interval: float = 10):
    """Call check() until it returns something truthy, or give up after `timeout` seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check()
        if result:
            return result
        time.sleep(interval)
    return None


def tier_url(outputs, settings, tier_name: str) -> str:
    port = settings.tier(tier_name).listener_port
    return f"http://{outputs.alb_dns_name}:{port}/"


def port_open(host: str, port: int, timeout: float = 5.0) -> bool:
    """True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def instance_ip(aws, name_tag: str) -> str | None:
    """Public IP of the running instance with this Name tag (weather-app or advisor-app)."""
    reservations = aws.ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [name_tag]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ])["Reservations"]
    for reservation in reservations:
        for instance in reservation["Instances"]:
            if instance.get("PublicIpAddress"):
                return instance["PublicIpAddress"]
    return None
