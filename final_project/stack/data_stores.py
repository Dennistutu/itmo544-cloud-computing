"""DynamoDB tables, the SNS topic and the salt parameter. Idempotent create, tolerant delete."""
from __future__ import annotations

import logging
import secrets

from .clients import AwsClients
from .project_config import ProjectSettings
from .util import error_code, ignore_errors

log = logging.getLogger("stack")
TTL_ATTRIBUTE = "expires_at"


def ensure_table(aws: AwsClients, name: str) -> str:
    """On-demand table keyed by 'pk', with TTL on expires_at. No backups (PITR stays off)."""
    try:
        arn = aws.dynamodb.describe_table(TableName=name)["Table"]["TableArn"]
        log.info("Table %s already exists, skipping.", name)
    except aws.dynamodb.exceptions.ResourceNotFoundException:
        arn = aws.dynamodb.create_table(
            TableName=name, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            Tags=[{"Key": "project", "Value": "itmo544-final"}],
        )["TableDescription"]["TableArn"]
        aws.dynamodb.get_waiter("table_exists").wait(TableName=name)
        log.info("Created table %s", name)
    ttl = aws.dynamodb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
    if ttl.get("TimeToLiveStatus") not in ("ENABLED", "ENABLING"):
        aws.dynamodb.update_time_to_live(TableName=name, TimeToLiveSpecification={
            "Enabled": True, "AttributeName": TTL_ATTRIBUTE})
    return arn


def delete_table(aws: AwsClients, name: str) -> None:
    with ignore_errors("ResourceNotFoundException"):
        aws.dynamodb.delete_table(TableName=name)
        aws.dynamodb.get_waiter("table_not_exists").wait(TableName=name)
        log.info("Deleted table %s", name)


def ensure_topic(aws: AwsClients, s: ProjectSettings) -> str:
    """create_topic is idempotent: the same name returns the same ARN."""
    arn = aws.sns.create_topic(Name=s.sns_topic_name,
                               Tags=[{"Key": "project", "Value": "itmo544-final"}])["TopicArn"]
    log.info("Email topic %s", s.sns_topic_name)
    return arn


def delete_topic(aws: AwsClients, topic_arn: str) -> None:
    """Deleting the topic also deletes every subscription, so no address is kept after teardown."""
    with ignore_errors("NotFound"):
        aws.sns.delete_topic(TopicArn=topic_arn)
        log.info("Deleted topic and all its subscriptions")


def ensure_salt(aws: AwsClients, s: ProjectSettings) -> None:
    """A random per-deployment salt for hashing visitor IPs and recipient tags.
    Created once and kept for the life of the stack, so subscriptions keep matching."""
    try:
        aws.ssm.get_parameter(Name=s.salt_param_name)
        log.info("Salt parameter exists, keeping it.")
    except aws.ssm.exceptions.ParameterNotFound:
        aws.ssm.put_parameter(Name=s.salt_param_name, Type="SecureString",
                              Value=secrets.token_hex(32))
        log.info("Created salt parameter %s (value not shown)", s.salt_param_name)


def delete_salt(aws: AwsClients, s: ProjectSettings) -> None:
    with ignore_errors("ParameterNotFound"):
        aws.ssm.delete_parameter(Name=s.salt_param_name)
        log.info("Deleted salt parameter")
