"""Is the deployed infrastructure correct and as private as the design says?

Run after create_weather_stack.py:  pytest -m integration -v
"""
import pytest
from botocore.exceptions import ClientError

from stack.util import error_code
from tests.helpers import instance_ip, port_open, probe, tier_url, wait_until

pytestmark = pytest.mark.integration


def test_both_target_groups_become_healthy(aws, settings, outputs):
    """Runs first, so the app tests that follow find healthy targets."""
    def all_healthy():
        for tier in settings.tiers:
            targets = aws.elbv2.describe_target_health(
                TargetGroupArn=outputs.tg_arn(tier.name))["TargetHealthDescriptions"]
            if not targets or any(t["TargetHealth"]["State"] != "healthy" for t in targets):
                return False
        return True

    assert wait_until(all_healthy, timeout=420, interval=15), "targets never became healthy"


def _decision(aws, role_name, parameter_arn):
    role_arn = aws.iam.get_role(RoleName=role_name)["Role"]["Arn"]
    result = aws.iam.simulate_principal_policy(
        PolicySourceArn=role_arn, ActionNames=["ssm:GetParameter"], ResourceArns=[parameter_arn])
    return result["EvaluationResults"][0]["EvalDecision"]


def test_each_role_can_read_only_its_own_secret(aws, settings):
    account = aws.sts.get_caller_identity()["Account"]
    db_arn = settings.parameter_arn(account, settings.db_password_param)
    gemini_arn = settings.parameter_arn(account, settings.gemini_param_name)
    decisions = {
        ("weather", "db-password"): _decision(aws, settings.weather.role_name, db_arn),
        ("weather", "gemini-key"): _decision(aws, settings.weather.role_name, gemini_arn),
        ("advisor", "db-password"): _decision(aws, settings.advisor.role_name, db_arn),
        ("advisor", "gemini-key"): _decision(aws, settings.advisor.role_name, gemini_arn),
    }
    print("\nIAM policy simulator:", decisions)
    assert decisions[("weather", "db-password")] == "allowed"
    assert decisions[("weather", "gemini-key")] != "allowed"
    assert decisions[("advisor", "gemini-key")] == "allowed"
    assert decisions[("advisor", "db-password")] != "allowed"


def test_app_instances_are_reachable_only_through_the_alb(aws, settings, outputs):
    for tier in settings.tiers:
        ip = instance_ip(aws, f"{tier.name}-app")
        assert ip, f"no running {tier.name}-app instance"
        assert not port_open(ip, settings.app_port), f"{tier.name} port {settings.app_port} answered directly"
        assert probe(tier_url(outputs, settings, tier.name), timeout=10), f"{tier.name} not reachable via the ALB"


def test_database_port_is_not_reachable_from_this_computer(settings, db):
    assert not port_open(db.db_host, settings.db_port), "the database answered from outside the VPC"


def test_database_is_private_single_az_encrypted_and_unbacked(aws, settings):
    """What moto cannot prove: the real RDS instance really has these settings."""
    instance = aws.rds.describe_db_instances(
        DBInstanceIdentifier=settings.db_instance_id)["DBInstances"][0]
    assert instance["DBInstanceClass"] == settings.db_instance_class
    assert instance["BackupRetentionPeriod"] == 0
    assert instance["PubliclyAccessible"] is False
    assert instance["MultiAZ"] is False
    assert instance["StorageEncrypted"] is True
    assert aws.rds.describe_db_snapshots(
        DBInstanceIdentifier=settings.db_instance_id)["DBSnapshots"] == []
    assert _automated_backups(aws, settings.db_instance_id) == []


def _automated_backups(aws, instance_id):
    """AWS answers 'not found' (not an empty list) when an instance has no automated backups."""
    try:
        return aws.rds.describe_db_instance_automated_backups(
            DBInstanceIdentifier=instance_id)["DBInstanceAutomatedBackups"]
    except ClientError as err:
        if error_code(err) != "DBInstanceAutomatedBackupNotFound":
            raise
        return []
