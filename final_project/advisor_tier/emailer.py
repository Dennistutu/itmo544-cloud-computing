"""Email delivery with Amazon SNS (Decision 10).

One topic, created by the stack. Every subscription carries a filter policy that
matches only its own recipient tag, and every publish carries exactly one recipient
tag, so a report reaches only the address that asked for it. Using one topic (instead
of a topic per address) keeps the IAM policy on a single exact ARN, with no wildcard.

States the user can see:  pending (confirmation email sent / not yet clicked),
                          sent (SNS accepted the message), failed (SNS error).
"""
from __future__ import annotations

import hashlib
import json

from botocore.exceptions import BotoCoreError, ClientError

PENDING = "PendingConfirmation"


def recipient_tag(email: str, salt: str) -> str:
    """Stable, non-reversible tag for an address. Used in the filter policy and on publish."""
    return hashlib.sha256(f"{salt}:{email}".encode()).hexdigest()[:32]


def filter_policy(tag: str) -> str:
    return json.dumps({"recipient": [tag]})


def find_subscription(sns, topic_arn: str, email: str) -> dict | None:
    """The email subscription for this address on the topic, if any."""
    token = None
    while True:
        kwargs = {"TopicArn": topic_arn, **({"NextToken": token} if token else {})}
        page = sns.list_subscriptions_by_topic(**kwargs)
        for sub in page.get("Subscriptions", []):
            if sub.get("Protocol") == "email" and sub.get("Endpoint", "").lower() == email:
                return sub
        token = page.get("NextToken")
        if not token:
            return None


def send_report(sns, topic_arn: str, email: str, salt: str, subject: str, text: str) -> dict:
    """Consent first, then delivery. Never claims 'sent' before SNS accepted the message."""
    tag = recipient_tag(email, salt)
    try:
        sub = find_subscription(sns, topic_arn, email)
        if sub is None:
            sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email,
                          Attributes={"FilterPolicy": filter_policy(tag),
                                      "FilterPolicyScope": "MessageAttributes"})
            return {"status": "pending",
                    "message": "We sent a confirmation email from AWS Notifications. "
                               "Click 'Confirm subscription' in it, then press Email again."}
        if sub.get("SubscriptionArn") == PENDING:
            return {"status": "pending",
                    "message": "This address has not confirmed yet. Check your inbox (and spam) "
                               "for the AWS confirmation email, click the link, then try again."}
        resp = sns.publish(TopicArn=topic_arn, Subject=subject[:100], Message=text,
                           MessageAttributes={"recipient": {"DataType": "String",
                                                            "StringValue": tag}})
        return {"status": "sent", "message": "Report sent.", "message_id": resp["MessageId"]}
    except (ClientError, BotoCoreError):
        return {"status": "failed",
                "message": "The email service did not accept the request. Nothing was sent; "
                           "please try again later."}


def report_text(report: dict) -> str:
    """The report as plain text (SNS email is plain text only).

    Mail apps show plain text in a proportional font, so space-aligned columns fall
    apart. Each item gets its own block with one line per period instead.
    """
    facts = report["facts"]
    lines = [f"Weather Advisor report for {facts['place']}, {facts['state']} {facts['zip']}",
             f"Date: {facts['date']} (local time, {facts['timezone']})",
             "Morning = 06:00-11:59, Night = 18:00-23:59", ""]
    for r in report["rows"]:
        lines += [r["label"].upper(),
                  f"  Morning: {r['morning']}  [{r['morning_src']}]",
                  f"  Night:   {r['night']}  [{r['night_src']}]", ""]
    for key, label in (("road", "SUGGESTED ROAD CONDITIONS"), ("dressing", "SUGGESTED DRESSING"),
                       ("accessories", "SUGGESTED ACCESSORIES")):
        lines += [label,
                  f"  Morning: {report['advice']['morning'][key]}",
                  f"  Night:   {report['advice']['night'][key]}", ""]
    lines += [f"Advice source: {report['advice_source']}",
              "Weather data: National Weather Service; Open-Meteo.com (CC BY 4.0)."]
    return "\n".join(lines)
