"""The stack with moto: least privilege, order, boot script, idempotency and full teardown."""
import base64
import gzip
import json

import pytest

from stack.boot import USER_DATA_LIMIT, build_boot_script, compress
from stack.config import ConfigError
from stack.network import StackError
from stack.project_config import ProjectSettings
from stack.project_policies import advisor_policy, weather_policy, wildcards_in
from stack.project_stack import advisor_env, create_stack, delete_stack, weather_env
from tests.conftest import fake_values

ACCOUNT = "123456789012"


@pytest.fixture
def built(fake_aws):
    settings, aws = fake_aws
    outputs = create_stack(settings, aws, iam_wait=0, sleep=lambda _: None)
    return settings, aws, outputs


def latest_user_data(aws, lt_name):
    versions = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=lt_name)["LaunchTemplateVersions"]
    latest = max(versions, key=lambda v: v["VersionNumber"])
    return gzip.decompress(base64.b64decode(latest["LaunchTemplateData"]["UserData"])).decode()


# ------------------------------------------------------------------ least privilege
def test_policies_have_no_wildcards(fake_settings):
    for doc in (weather_policy(fake_settings, ACCOUNT), advisor_policy(fake_settings, ACCOUNT)):
        assert wildcards_in(doc) == []


def test_wildcard_detector_catches_wildcards():
    assert wildcards_in({"Statement": [{"Action": "s3:*", "Resource": ["arn:x", "*"]}]}) == ["s3:*", "*"]


def test_weather_role_has_only_its_cache(fake_settings):
    doc = weather_policy(fake_settings, ACCOUNT)
    text = json.dumps(doc)
    assert [st["Action"] for st in doc["Statement"]] == [["dynamodb:GetItem", "dynamodb:PutItem"]]
    for forbidden in ("ssm:", "sns:", fake_settings.advisor_table, fake_settings.gemini_param_name):
        assert forbidden not in text


def test_only_advisor_may_read_the_gemini_key(fake_settings):
    key_arn = fake_settings.parameter_arn(ACCOUNT, fake_settings.gemini_param_name)
    assert key_arn in json.dumps(advisor_policy(fake_settings, ACCOUNT))
    assert key_arn not in json.dumps(weather_policy(fake_settings, ACCOUNT))


def test_advisor_email_rights_are_on_one_topic(fake_settings):
    st = [s for s in advisor_policy(fake_settings, ACCOUNT)["Statement"]
          if s["Sid"] == "EmailOnlyThroughOurTopic"][0]
    assert st["Resource"] == f"arn:aws:sns:us-east-2:{ACCOUNT}:{fake_settings.sns_topic_name}"
    assert "sns:CreateTopic" not in st["Action"] and "sns:DeleteTopic" not in st["Action"]


def test_deployed_policies_match_the_documents(built):
    settings, aws, _ = built
    account = aws.sts.get_caller_identity()["Account"]
    for tier, fn in ((settings.weather, weather_policy), (settings.advisor, advisor_policy)):
        doc = aws.iam.get_role_policy(RoleName=tier.role_name,
                                      PolicyName=tier.policy_name)["PolicyDocument"]
        assert doc == fn(settings, account)


# ------------------------------------------------------------------- boot script
def test_boot_script_is_python_not_bash(fake_settings):
    script = build_boot_script("weather", fake_settings.weather.app_dir, weather_env(fake_settings))
    assert script.startswith("#!/usr/bin/env python3")
    compile(script, "boot.py", "exec")                       # valid Python
    assert "shell=True" not in script and "#!/bin/bash" not in script


def test_boot_script_embeds_the_tier_code(fake_settings):
    script = build_boot_script("advisor", fake_settings.advisor.app_dir,
                               advisor_env(fake_settings, "alb.example", "arn:topic"))
    assert "advisor_tier/service.py" in script and "advisor_tier/emailer.py" in script
    assert "weather_tier/" not in script                     # each tier gets only its own code


@pytest.mark.parametrize("tier", ["weather", "advisor"])
def test_compressed_user_data_under_16_kb(fake_settings, tier):
    long_dns = "itmo-444-544-weather-alb-1234567890.us-east-2.elb.amazonaws.com"
    env = weather_env(fake_settings) if tier == "weather" else advisor_env(
        fake_settings, long_dns, f"arn:aws:sns:us-east-2:{ACCOUNT}:itmo-544-final-reports")
    payload = compress(build_boot_script(tier, fake_settings.tier(tier).app_dir, env))
    assert len(payload) < USER_DATA_LIMIT


def test_boot_settings_reject_newlines(fake_settings):
    with pytest.raises(ValueError):
        build_boot_script("weather", fake_settings.weather.app_dir, {"A": "x\nB=y"})


def test_no_secret_in_user_data(built):
    settings, aws, outputs = built
    salt = aws.ssm.get_parameter(Name=settings.salt_param_name, WithDecryption=True)["Parameter"]["Value"]
    for tier in settings.tiers:
        ud = latest_user_data(aws, tier.lt_name)
        assert "fake-gemini-key-for-tests" not in ud and salt not in ud
    advisor = latest_user_data(aws, settings.advisor.lt_name)
    assert f"http://{outputs.alb_dns_name}:{settings.weather.listener_port}" in advisor  # ALB first
    assert settings.gemini_param_name in advisor             # the NAME, not the value


def test_weather_tier_is_not_told_about_gemini_or_email(built):
    settings, aws, _ = built
    ud = latest_user_data(aws, settings.weather.lt_name)
    for word in ("GEMINI", "SNS_TOPIC", "SALT"):
        assert word not in ud


# --------------------------------------------------------------------- settings
def test_timeouts_must_fit_inside_alb_idle_timeout():
    v = {**fake_values(), "WEATHER_TIMEOUT_SECONDS": "40", "GEMINI_TIMEOUT_SECONDS": "25"}
    with pytest.raises(ConfigError, match="idle timeout"):
        ProjectSettings.from_values(v)


def test_missing_setting_is_named():
    v = fake_values()
    v.pop("FACTS_TABLE")
    with pytest.raises(ConfigError, match="FACTS_TABLE"):
        ProjectSettings.from_values(v)


# ------------------------------------------------------------ create: what exists
def test_tables_have_ttl_and_on_demand(built):
    settings, aws, _ = built
    for name in (settings.facts_table, settings.advisor_table):
        table = aws.dynamodb.describe_table(TableName=name)["Table"]
        assert table["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
        ttl = aws.dynamodb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
        assert ttl["AttributeName"] == "expires_at"
        backups = aws.dynamodb.describe_continuous_backups(TableName=name)
        assert backups["ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"][
            "PointInTimeRecoveryStatus"] == "DISABLED"


def test_app_port_open_only_to_the_alb(built):
    settings, aws, outputs = built
    group = aws.ec2.describe_security_groups(GroupIds=[settings.app_sg_id])["SecurityGroups"][0]
    rules = [p for p in group["IpPermissions"] if p["FromPort"] == 3000]
    assert len(rules) == 1 and rules[0]["IpRanges"] == []
    assert rules[0]["UserIdGroupPairs"][0]["GroupId"] == outputs.alb_security_group_id


def test_health_checks_use_health_route(built):
    _, aws, _ = built
    for tg in aws.elbv2.describe_target_groups()["TargetGroups"]:
        assert tg["HealthCheckPath"] == "/health"


def test_instances_require_imdsv2(built):
    settings, aws, _ = built
    for tier in settings.tiers:
        v = aws.ec2.describe_launch_template_versions(LaunchTemplateName=tier.lt_name)[
            "LaunchTemplateVersions"][-1]
        assert v["LaunchTemplateData"]["MetadataOptions"]["HttpTokens"] == "required"


def test_refuses_without_gemini_key(fake_aws):
    settings, aws = fake_aws
    aws.ssm.delete_parameter(Name=settings.gemini_param_name)
    with pytest.raises(StackError, match="store_gemini_key"):
        create_stack(settings, aws, iam_wait=0, sleep=lambda _: None)
    assert aws.dynamodb.list_tables()["TableNames"] == []    # stopped before creating anything


# ------------------------------------------------------------ idempotency, delete
def test_create_twice_creates_no_duplicates(built):
    settings, aws, first = built
    salt = aws.ssm.get_parameter(Name=settings.salt_param_name, WithDecryption=True)["Parameter"]["Value"]
    second = create_stack(settings, aws, iam_wait=0, sleep=lambda _: None)
    assert (first.alb_arn, first.topic_arn) == (second.alb_arn, second.topic_arn)
    assert len(aws.elbv2.describe_load_balancers()["LoadBalancers"]) == 1
    assert len(aws.elbv2.describe_target_groups()["TargetGroups"]) == 2
    assert len(aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"]) == 2
    assert len(aws.iam.list_roles()["Roles"]) == 2
    assert sorted(aws.dynamodb.list_tables()["TableNames"]) == sorted(
        [settings.facts_table, settings.advisor_table])
    assert len(aws.sns.list_topics()["Topics"]) == 1
    # the salt must survive a re-run, or existing email subscriptions would stop matching
    assert aws.ssm.get_parameter(Name=settings.salt_param_name,
                                 WithDecryption=True)["Parameter"]["Value"] == salt


def test_delete_removes_everything_but_the_key_and_is_safe_twice(built):
    settings, aws, outputs = built
    aws.sns.subscribe(TopicArn=outputs.topic_arn, Protocol="email", Endpoint="a@example.com")
    delete_stack(settings, aws, sleep=lambda _: None)
    delete_stack(settings, aws, sleep=lambda _: None)

    assert aws.elbv2.describe_load_balancers()["LoadBalancers"] == []
    assert aws.elbv2.describe_target_groups()["TargetGroups"] == []
    assert aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"] == []
    assert aws.ec2.describe_launch_templates()["LaunchTemplates"] == []
    assert aws.iam.list_roles()["Roles"] == []
    assert aws.dynamodb.list_tables()["TableNames"] == []
    assert aws.sns.list_topics()["Topics"] == []
    assert aws.sns.list_subscriptions()["Subscriptions"] == []   # no email address kept
    with pytest.raises(aws.ssm.exceptions.ParameterNotFound):
        aws.ssm.get_parameter(Name=settings.salt_param_name)
    aws.ssm.get_parameter(Name=settings.gemini_param_name)        # the key is kept
