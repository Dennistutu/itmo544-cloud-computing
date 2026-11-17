"""Per-tier IAM policies (Decision 11). Pure functions, so tests can prove there are no
wildcards. Each action is listed with the code that needs it.
"""
from __future__ import annotations

from .project_config import ProjectSettings


def weather_policy(s: ProjectSettings, account: str) -> dict:
    """Data tier: read and write its own facts cache. Nothing else, not even SSM."""
    facts = s.table_arn(account, s.facts_table)
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "FactsCache",
         # weather_tier/service.py FactsCache.get -> get_item, FactsCache.put -> put_item
         "Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
         "Resource": facts},
    ]}


def advisor_policy(s: ProjectSettings, account: str) -> dict:
    """Advisor tier: its table, the two parameters it reads, and the one email topic."""
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "ReportsCountersLimits",
         # advisor_tier/store.py: get_report -> GetItem, put_report -> PutItem,
         # _increment_below (daily cap, rate limits) -> UpdateItem, gemini_count -> GetItem
         "Effect": "Allow",
         "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
         "Resource": s.table_arn(account, s.advisor_table)},
        {"Sid": "ReadGeminiKeyAndSalt",
         # advisor_tier/service.py Deps.gemini_key and main() (salt) -> ssm.get_parameter
         "Effect": "Allow", "Action": ["ssm:GetParameter"],
         "Resource": [s.parameter_arn(account, s.gemini_param_name),
                      s.parameter_arn(account, s.salt_param_name)]},
        {"Sid": "EmailOnlyThroughOurTopic",
         # advisor_tier/emailer.py: find_subscription -> ListSubscriptionsByTopic,
         # send_report -> Subscribe (consent) and Publish (delivery)
         "Effect": "Allow",
         "Action": ["sns:ListSubscriptionsByTopic", "sns:Subscribe", "sns:Publish"],
         "Resource": s.topic_arn(account)},
    ]}


def policy_for(tier_name: str, s: ProjectSettings, account: str) -> dict:
    return {"weather": weather_policy, "advisor": advisor_policy}[tier_name](s, account)


def wildcards_in(policy: dict) -> list[str]:
    """Every Action or Resource string that contains '*'. Must be empty."""
    found = []
    for st in policy["Statement"]:
        for field in ("Action", "Resource"):
            values = st[field] if isinstance(st[field], list) else [st[field]]
            found += [x for x in values if "*" in x]
    return found
