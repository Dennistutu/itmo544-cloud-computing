"""The weather database: security group, DB subnet group, random password and the RDS instance."""
from __future__ import annotations

import logging
import secrets
import time

from botocore.exceptions import ClientError

from .clients import AwsClients
from .network import _group_rule, default_vpc_id, find_security_group, subnet_in_az
from .util import error_code, ignore_errors, retry_on
from .weather_config import WeatherSettings
from .weather_outputs import DatabaseOutputs

log = logging.getLogger("stack")


def ensure_db_security_group(aws: AwsClients, settings: WeatherSettings, vpc_id: str) -> str:
    """Security group allowing the database port only from the app security group."""
    group_id = find_security_group(aws, settings.db_sg_name, vpc_id)
    if group_id is None:
        group_id = aws.ec2.create_security_group(
            GroupName=settings.db_sg_name,
            Description="PostgreSQL access from the lab app instances only",
            VpcId=vpc_id,
        )["GroupId"]
        log.info("Created database security group %s", group_id)
    else:
        log.info("Database security group already exists, skipping.")
    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=group_id, IpPermissions=_group_rule(settings.db_port, settings.app_sg_id))
    return group_id


def db_subnet_group_exists(aws: AwsClients, name: str) -> bool:
    try:
        aws.rds.describe_db_subnet_groups(DBSubnetGroupName=name)
        return True
    except ClientError as err:
        if error_code(err) != "DBSubnetGroupNotFoundFault":
            raise
        return False


def ensure_db_subnet_group(aws: AwsClients, settings: WeatherSettings, subnets: list[str]) -> None:
    """The subnets (in two zones) that RDS may place the database in."""
    if db_subnet_group_exists(aws, settings.db_subnet_group_name):
        log.info("DB subnet group already exists, skipping.")
        return
    aws.rds.create_db_subnet_group(
        DBSubnetGroupName=settings.db_subnet_group_name,
        DBSubnetGroupDescription="Lab subnets for the weather database",
        SubnetIds=subnets,
    )
    log.info("Created DB subnet group %s", settings.db_subnet_group_name)


def parameter_exists(aws: AwsClients, name: str) -> bool:
    """Ask only whether the parameter exists. No decryption, so no secret is read."""
    try:
        aws.ssm.get_parameter(Name=name)
        return True
    except ClientError as err:
        if error_code(err) != "ParameterNotFound":
            raise
        return False


def ensure_db_password(aws: AwsClients, settings: WeatherSettings) -> None:
    """Generate a random password straight into SSM. It is never printed or written to a file."""
    if parameter_exists(aws, settings.db_password_param):
        log.info("Password parameter already exists, keeping it.")
        return
    aws.ssm.put_parameter(Name=settings.db_password_param, Type="SecureString",
                          Value=secrets.token_hex(16))
    log.info("Stored a new random database password in %s (value not shown).",
             settings.db_password_param)


def _read_db_password(aws: AwsClients, settings: WeatherSettings) -> str:
    return aws.ssm.get_parameter(Name=settings.db_password_param,
                                 WithDecryption=True)["Parameter"]["Value"]


def db_instance_request(settings: WeatherSettings, db_sg_id: str) -> dict:
    """Every create_db_instance setting except the password. Pure, so tests can check it."""
    return {
        "DBInstanceIdentifier": settings.db_instance_id,
        "DBInstanceClass": settings.db_instance_class,
        "Engine": "postgres",
        "AllocatedStorage": settings.db_allocated_storage,
        "StorageType": "gp2",
        "MasterUsername": settings.db_user,
        "DBName": settings.db_name,
        "Port": settings.db_port,
        "DBSubnetGroupName": settings.db_subnet_group_name,
        "VpcSecurityGroupIds": [db_sg_id],
        "AvailabilityZone": settings.az_a,
        "BackupRetentionPeriod": 0,      # no automated backups, no point-in-time recovery
        "MultiAZ": False,                # one copy in one zone: Multi-AZ is not free
        "PubliclyAccessible": False,     # no public address
        "AutoMinorVersionUpgrade": False,
        "DeletionProtection": False,     # so the cleanup script can delete it
        "StorageEncrypted": True,        # encrypted at rest with the default AWS managed key
    }


def db_delete_request(settings: WeatherSettings) -> dict:
    """Delete with no final snapshot and no kept automated backups: nothing is left behind."""
    return {
        "DBInstanceIdentifier": settings.db_instance_id,
        "SkipFinalSnapshot": True,
        "DeleteAutomatedBackups": True,
    }


def find_db_instance(aws: AwsClients, instance_id: str) -> dict | None:
    try:
        return aws.rds.describe_db_instances(DBInstanceIdentifier=instance_id)["DBInstances"][0]
    except ClientError as err:
        if error_code(err) != "DBInstanceNotFound":
            raise
        return None


def ensure_db_instance(aws: AwsClients, settings: WeatherSettings, db_sg_id: str) -> str:
    """Create the instance if needed, wait until it is available, and return its address."""
    if find_db_instance(aws, settings.db_instance_id):
        log.info("Database %s already exists, skipping.", settings.db_instance_id)
    else:
        request = db_instance_request(settings, db_sg_id)
        request["MasterUserPassword"] = _read_db_password(aws, settings)
        aws.rds.create_db_instance(**request)
        del request  # the password exists only for this one call
        log.info("Creating database %s (%s, %s GB, single-AZ, private, encrypted, no backups).",
                 settings.db_instance_id, settings.db_instance_class, settings.db_allocated_storage)
    log.info("Waiting for the database to become available (usually 5 to 10 minutes)...")
    aws.rds.get_waiter("db_instance_available").wait(
        DBInstanceIdentifier=settings.db_instance_id,
        WaiterConfig={"Delay": 30, "MaxAttempts": 60})
    return find_db_instance(aws, settings.db_instance_id)["Endpoint"]["Address"]


def create_database(settings: WeatherSettings, aws: AwsClients) -> DatabaseOutputs:
    """The Python version of create_database.sh."""
    log.info("== 1. Default VPC and subnets in both AZs ==")
    vpc_id = default_vpc_id(aws)
    subnets = [subnet_in_az(aws, vpc_id, settings.az_a), subnet_in_az(aws, vpc_id, settings.az_b)]

    log.info("== 2. Database security group (port %s, from the app security group only) ==",
             settings.db_port)
    db_sg_id = ensure_db_security_group(aws, settings, vpc_id)

    log.info("== 3. DB subnet group ==")
    ensure_db_subnet_group(aws, settings, subnets)

    log.info("== 4. Database password (random, stored as a SecureString, never printed) ==")
    ensure_db_password(aws, settings)

    log.info("== 5. The PostgreSQL instance (free tier size, no backups) ==")
    host = ensure_db_instance(aws, settings, db_sg_id)
    return DatabaseOutputs(db_host=host, db_security_group_id=db_sg_id)


def delete_database(settings: WeatherSettings, aws: AwsClients, sleep=time.sleep) -> None:
    """The Python version of delete_database.sh. Safe to run when things are already gone."""
    log.info("== Deleting the database (no final snapshot, no kept backups) ==")
    if find_db_instance(aws, settings.db_instance_id):
        with ignore_errors("DBInstanceNotFound", "InvalidDBInstanceState"):
            aws.rds.delete_db_instance(**db_delete_request(settings))
        log.info("Waiting for the database to be deleted (a few minutes)...")
        aws.rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=settings.db_instance_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60})
        log.info("Deleted database %s", settings.db_instance_id)
    else:
        log.info("Database %s not found, nothing to delete.", settings.db_instance_id)

    log.info("== Deleting the DB subnet group ==")
    if db_subnet_group_exists(aws, settings.db_subnet_group_name):
        with ignore_errors("DBSubnetGroupNotFoundFault"):
            retry_on({"InvalidDBSubnetGroupStateFault"},
                     lambda: aws.rds.delete_db_subnet_group(
                         DBSubnetGroupName=settings.db_subnet_group_name),
                     attempts=10, delay=15, sleep=sleep)
            log.info("Deleted DB subnet group %s", settings.db_subnet_group_name)

    log.info("== Deleting the database security group ==")
    db_sg_id = find_security_group(aws, settings.db_sg_name, default_vpc_id(aws))
    if db_sg_id:
        with ignore_errors("InvalidGroup.NotFound"):
            retry_on({"DependencyViolation"},
                     lambda: aws.ec2.delete_security_group(GroupId=db_sg_id),
                     attempts=10, delay=15, sleep=sleep)
            log.info("Deleted database security group %s", db_sg_id)

    log.info("== Deleting the database password parameter ==")
    with ignore_errors("ParameterNotFound"):
        aws.ssm.delete_parameter(Name=settings.db_password_param)
        log.info("Deleted %s", settings.db_password_param)

    DatabaseOutputs.remove()
    log.info("Database removed.")
