"""The weather stack: least privilege, ALB-first ordering, user-data and idempotency, against moto."""
import base64
import json

import pytest

from stack.database import create_database
from stack.network import StackError
from stack.secret_policies import read_one_parameter_policy, secret_arn_for
from stack.weather_stack import (USER_DATA_LIMIT, advisor_user_data, create_weather_stack,
                                 delete_weather_stack, launch_template_data, weather_user_data)

ACCOUNT = "123456789012"


@pytest.fixture
def built(fake_aws):
    """Database first, then the stack, exactly as the scripts do it."""
    settings, aws = fake_aws
    db = create_database(settings, aws)
    db.save()
    outputs = create_weather_stack(settings, aws, db, iam_wait=0)
    return settings, aws, db, outputs


def _latest_user_data(aws, lt_name: str) -> str:
    versions = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=lt_name)["LaunchTemplateVersions"]
    latest = max(versions, key=lambda v: v["VersionNumber"])
    return base64.b64decode(latest["LaunchTemplateData"]["UserData"]).decode()


# ---- least privilege -------------------------------------------------------------------

def test_each_role_may_read_exactly_its_own_secret(fake_settings):
    db_arn = fake_settings.parameter_arn(ACCOUNT, fake_settings.db_password_param)
    gemini_arn = fake_settings.parameter_arn(ACCOUNT, fake_settings.gemini_param_name)
    weather = read_one_parameter_policy(secret_arn_for(fake_settings, "weather", ACCOUNT))
    advisor = read_one_parameter_policy(secret_arn_for(fake_settings, "advisor", ACCOUNT))

    assert weather["Statement"] == [{"Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": db_arn}]
    assert advisor["Statement"] == [{"Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": gemini_arn}]
    assert fake_settings.gemini_param_name not in json.dumps(weather)
    assert fake_settings.db_password_param not in json.dumps(advisor)


def test_deployed_role_policies_name_exactly_one_secret_each(built):
    settings, aws, _, _ = built
    account = aws.sts.get_caller_identity()["Account"]
    db_arn = settings.parameter_arn(account, settings.db_password_param)
    gemini_arn = settings.parameter_arn(account, settings.gemini_param_name)
    for tier, own, other in ((settings.weather, db_arn, gemini_arn),
                             (settings.advisor, gemini_arn, db_arn)):
        doc = aws.iam.get_role_policy(RoleName=tier.role_name,
                                      PolicyName=tier.policy_name)["PolicyDocument"]
        assert doc["Statement"] == [{"Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": own}]
        assert other not in json.dumps(doc)


# ---- ordering and user-data -------------------------------------------------------------

def test_alb_first_advisor_user_data_contains_the_alb_dns_name(built):
    settings, aws, _, outputs = built
    user_data = _latest_user_data(aws, settings.advisor.lt_name)
    assert f"Environment=WEATHER_URL=http://{outputs.alb_dns_name}:8080" in user_data


def test_weather_user_data_has_db_host_but_never_the_password(built):
    settings, aws, db, _ = built
    password = aws.ssm.get_parameter(Name=settings.db_password_param,
                                     WithDecryption=True)["Parameter"]["Value"]
    weather = _latest_user_data(aws, settings.weather.lt_name)
    advisor = _latest_user_data(aws, settings.advisor.lt_name)
    assert f"Environment=DB_HOST={db.db_host}" in weather
    assert f"Environment=DB_PASSWORD_PARAM={settings.db_password_param}" in weather
    for user_data in (weather, advisor):
        assert password not in user_data, "the database password must never be in user-data"
        assert "fake-gemini-key-for-tests" not in user_data, "the Gemini key must never be in user-data"


def test_user_data_for_each_tier_is_under_16_kb(fake_settings):
    long_host = "itmo-444-544-weather-db.abcdefghijkl.us-east-2.rds.amazonaws.com"
    long_dns = "itmo-444-544-weather-alb-1234567890.us-east-2.elb.amazonaws.com"
    for user_data in (weather_user_data(fake_settings, long_host),
                      advisor_user_data(fake_settings, long_dns)):
        assert len(user_data.encode()) < USER_DATA_LIMIT


def test_oversized_user_data_is_refused(fake_settings):
    with pytest.raises(StackError, match="over the 16384 byte limit"):
        launch_template_data(fake_settings, fake_settings.weather, "x" * (USER_DATA_LIMIT + 1))


# ---- idempotency and cleanup -------------------------------------------------------------

def test_create_weather_stack_twice_creates_no_duplicates(built):
    settings, aws, db, first = built
    second = create_weather_stack(settings, aws, db, iam_wait=0)
    assert first == second
    assert len(aws.elbv2.describe_load_balancers()["LoadBalancers"]) == 1
    assert len(aws.elbv2.describe_target_groups()["TargetGroups"]) == 2
    assert len(aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"]) == 2
    assert len(aws.iam.list_roles()["Roles"]) == 2
    assert len(aws.iam.list_instance_profiles()["InstanceProfiles"]) == 2
    group = aws.ec2.describe_security_groups(GroupIds=[settings.app_sg_id])["SecurityGroups"][0]
    assert len([p for p in group["IpPermissions"] if p["FromPort"] == 3000]) == 1
    for tier in settings.tiers:  # a second run publishes a new template version instead of failing
        versions = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name)["LaunchTemplateVersions"]
        assert len(versions) == 2


def test_stack_refuses_to_build_without_the_gemini_key(fake_aws):
    settings, aws = fake_aws
    db = create_database(settings, aws)
    aws.ssm.delete_parameter(Name=settings.gemini_param_name)
    with pytest.raises(StackError, match="store_gemini_key"):
        create_weather_stack(settings, aws, db, iam_wait=0)


def test_delete_removes_the_stack_but_keeps_database_and_key(built):
    settings, aws, _, _ = built
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    delete_weather_stack(settings, aws, sleep=lambda _: None)  # safe to run twice

    assert aws.elbv2.describe_load_balancers()["LoadBalancers"] == []
    assert aws.elbv2.describe_target_groups()["TargetGroups"] == []
    assert aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"] == []
    assert aws.ec2.describe_launch_templates()["LaunchTemplates"] == []
    assert aws.iam.list_roles()["Roles"] == []
    group = aws.ec2.describe_security_groups(GroupIds=[settings.app_sg_id])["SecurityGroups"][0]
    rule = [p for p in group["IpPermissions"] if p["FromPort"] == 3000][0]
    assert rule["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]
    assert len(aws.rds.describe_db_instances()["DBInstances"]) == 1   # database is untouched
    aws.ssm.get_parameter(Name=settings.gemini_param_name)            # the key is kept
