"""The database layer, checked as pure functions and against moto."""
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from stack.database import (create_database, db_delete_request, db_instance_request,
                            delete_database)
from stack.network import find_security_group
from stack.util import error_code


def test_database_request_is_free_tier_private_encrypted_and_unbacked(fake_settings):
    request = db_instance_request(fake_settings, "sg-db")
    assert request["DBInstanceClass"] == "db.t4g.micro"
    assert request["AllocatedStorage"] == 20
    assert request["BackupRetentionPeriod"] == 0
    assert request["MultiAZ"] is False
    assert request["PubliclyAccessible"] is False
    assert request["StorageEncrypted"] is True
    assert "MasterUserPassword" not in request  # the password is added only at call time


def test_delete_request_leaves_no_snapshot_and_no_backups(fake_settings):
    assert db_delete_request(fake_settings) == {
        "DBInstanceIdentifier": "t-weather-db",
        "SkipFinalSnapshot": True,
        "DeleteAutomatedBackups": True,
    }


def test_created_database_has_the_required_settings(fake_aws):
    settings, aws = fake_aws
    create_database(settings, aws)
    instance = aws.rds.describe_db_instances(
        DBInstanceIdentifier=settings.db_instance_id)["DBInstances"][0]
    assert instance["DBInstanceClass"] == "db.t4g.micro"
    assert instance["AllocatedStorage"] == 20
    assert instance["BackupRetentionPeriod"] == 0
    assert instance["MultiAZ"] is False
    assert instance["PubliclyAccessible"] is False
    assert instance["StorageEncrypted"] is True


def test_db_security_group_allows_only_the_app_security_group(fake_aws):
    settings, aws = fake_aws
    outputs = create_database(settings, aws)
    group = aws.ec2.describe_security_groups(
        GroupIds=[outputs.db_security_group_id])["SecurityGroups"][0]
    rules = group["IpPermissions"]
    assert len(rules) == 1
    rule = rules[0]
    assert (rule["FromPort"], rule["ToPort"]) == (5432, 5432)
    assert rule.get("IpRanges", []) == [], "no CIDR range may reach the database port"
    assert rule.get("Ipv6Ranges", []) == []
    assert [p["GroupId"] for p in rule["UserIdGroupPairs"]] == [settings.app_sg_id]


def test_create_then_delete_leaves_nothing(fake_aws):
    settings, aws = fake_aws
    create_database(settings, aws).save()
    delete_database(settings, aws, sleep=lambda _: None)

    assert aws.rds.describe_db_instances()["DBInstances"] == []
    assert aws.rds.describe_db_snapshots()["DBSnapshots"] == []
    with pytest.raises(ClientError) as err:
        aws.ssm.get_parameter(Name=settings.db_password_param)
    assert error_code(err.value) == "ParameterNotFound"
    groups = [g["DBSubnetGroupName"] for g in aws.rds.describe_db_subnet_groups()["DBSubnetGroups"]]
    assert settings.db_subnet_group_name not in groups
    vpc_id = aws.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    assert find_security_group(aws, settings.db_sg_name, vpc_id) is None
    assert not Path("db_outputs.json").exists()


def test_delete_database_is_safe_to_run_twice(fake_aws):
    settings, aws = fake_aws
    create_database(settings, aws)
    delete_database(settings, aws, sleep=lambda _: None)
    delete_database(settings, aws, sleep=lambda _: None)  # nothing left, must not raise


def test_create_database_twice_creates_no_duplicates(fake_aws):
    settings, aws = fake_aws
    first = create_database(settings, aws)
    password_before = aws.ssm.get_parameter(
        Name=settings.db_password_param, WithDecryption=True)["Parameter"]["Value"]
    second = create_database(settings, aws)
    password_after = aws.ssm.get_parameter(
        Name=settings.db_password_param, WithDecryption=True)["Parameter"]["Value"]

    assert first == second
    assert len(aws.rds.describe_db_instances()["DBInstances"]) == 1
    assert password_before == password_after, "a second run must not change the password"
    group = aws.ec2.describe_security_groups(
        GroupIds=[first.db_security_group_id])["SecurityGroups"][0]
    assert len(group["IpPermissions"]) == 1
