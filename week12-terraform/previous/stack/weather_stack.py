"""The weather advisor stack: roles, network, load balancer, launch templates and Auto Scaling Groups."""
from __future__ import annotations

import base64
import json
import logging
import time

from botocore.exceptions import ClientError

from . import balancer, compute, network, scaling, storage
from .clients import AwsClients
from .config import Tier
from .network import StackError
from .policies import trust_policy
from .secret_policies import read_one_parameter_policy, secret_arn_for
from .util import error_code, ignore_errors
from .weather_config import WeatherSettings
from .weather_outputs import DatabaseOutputs, WeatherStackOutputs

log = logging.getLogger("stack")

USER_DATA_LIMIT = 16 * 1024  # EC2 rejects user-data larger than 16 KB
RDS_CA_URL = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"


# ---- IAM ---------------------------------------------------------------------------------

def ensure_role_with_secret(aws: AwsClients, tier: Tier, secret_arn: str) -> None:
    """Role and instance profile for one tier, allowed to read exactly one secret."""
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_role(RoleName=tier.role_name,
                            AssumeRolePolicyDocument=json.dumps(trust_policy()))
    aws.iam.put_role_policy(RoleName=tier.role_name, PolicyName=tier.policy_name,
                            PolicyDocument=json.dumps(read_one_parameter_policy(secret_arn)))
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_instance_profile(InstanceProfileName=tier.profile_name)
    profile = aws.iam.get_instance_profile(InstanceProfileName=tier.profile_name)
    if tier.role_name not in [r["RoleName"] for r in profile["InstanceProfile"]["Roles"]]:
        aws.iam.add_role_to_instance_profile(InstanceProfileName=tier.profile_name,
                                             RoleName=tier.role_name)
    log.info("%s role may read only %s", tier.name, secret_arn.split(":parameter", 1)[1])


# ---- User-data ---------------------------------------------------------------------------

def _b64_file(path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def build_user_data(settings: WeatherSettings, tier: Tier, env_lines: list[str],
                    setup_lines: list[str] = ()) -> str:
    """The boot script: optional setup, the app files, Node, and a systemd service."""
    setup = "\n".join(setup_lines)
    env = "\n".join(env_lines)
    return f"""#!/bin/bash
dnf update -y
dnf install -y nodejs
mkdir -p /opt/app
{setup}
echo "{_b64_file(tier.app_dir / 'app.js')}" | base64 -d > /opt/app/app.js
echo "{_b64_file(tier.app_dir / 'package.json')}" | base64 -d > /opt/app/package.json
cd /opt/app
npm install --omit=dev
cat > /etc/systemd/system/{tier.service_name}.service << 'SERVICE'
[Unit]
Description={tier.name} app
After=network.target

[Service]
Environment=PORT={settings.app_port}
Environment=AWS_REGION={settings.region}
{env}
WorkingDirectory=/opt/app
ExecStart=/usr/bin/node /opt/app/app.js
Restart=always
User=ec2-user

[Install]
WantedBy=multi-user.target
SERVICE
chown -R ec2-user:ec2-user /opt/app
systemctl daemon-reload
systemctl enable {tier.service_name}.service
systemctl start {tier.service_name}.service
"""


def weather_user_data(settings: WeatherSettings, db_host: str) -> str:
    """Weather tier: gets the database address and the parameter NAME, never the password."""
    env = [
        f'Environment="NWS_USER_AGENT=(itmo-444-544 weather lab, {settings.contact_email})"',
        f"Environment=DB_HOST={db_host}",
        f"Environment=DB_PORT={settings.db_port}",
        f"Environment=DB_NAME={settings.db_name}",
        f"Environment=DB_USER={settings.db_user}",
        f"Environment=DB_PASSWORD_PARAM={settings.db_password_param}",
        f"Environment=CACHE_SECONDS={settings.cache_seconds}",
    ]
    # The RDS certificate bundle (so the app can verify the database) and psql for your own queries.
    setup = ["dnf install -y postgresql15", f"curl -sS -o /opt/app/rds-ca.pem {RDS_CA_URL}"]
    return build_user_data(settings, settings.weather, env, setup)


def advisor_user_data(settings: WeatherSettings, alb_dns: str) -> str:
    """Advisor tier: reaches the weather tier through the ALB, which is why the ALB comes first."""
    env = [
        f"Environment=WEATHER_URL=http://{alb_dns}:{settings.weather.listener_port}",
        f"Environment=GEMINI_KEY_PARAM={settings.gemini_param_name}",
        f"Environment=GEMINI_MODEL={settings.gemini_model}",
        f"Environment=KEY_CACHE_SECONDS={settings.key_cache_seconds}",
    ]
    return build_user_data(settings, settings.advisor, env)


def launch_template_data(settings: WeatherSettings, tier: Tier, user_data: str) -> dict:
    size = len(user_data.encode())
    if size > USER_DATA_LIMIT:
        raise StackError(f"{tier.name} user-data is {size} bytes, over the {USER_DATA_LIMIT} byte limit.")
    return {
        "ImageId": settings.ami_id,
        "InstanceType": settings.instance_type,
        "KeyName": settings.key_name,
        "SecurityGroupIds": [settings.app_sg_id],
        "IamInstanceProfile": {"Name": tier.profile_name},
        "UserData": base64.b64encode(user_data.encode()).decode(),
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}],
        }],
    }


def ensure_launch_template(aws: AwsClients, settings: WeatherSettings, tier: Tier,
                           user_data: str) -> int:
    """Create the template, or publish a new version if it exists. Returns the version number."""
    data = launch_template_data(settings, tier, user_data)
    if not compute.launch_template_exists(aws, tier):
        aws.ec2.create_launch_template(LaunchTemplateName=tier.lt_name, LaunchTemplateData=data)
        log.info("Created launch template %s", tier.lt_name)
        return 1
    version = aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name, SourceVersion="1",
        LaunchTemplateData=data)["LaunchTemplateVersion"]["VersionNumber"]
    log.info("Template %s exists, published version %s", tier.lt_name, version)
    return version


# ---- Create and delete -------------------------------------------------------------------

def require_gemini_key(aws: AwsClients, settings: WeatherSettings) -> None:
    """Stop early with a clear message if the Gemini key was never stored (existence check only)."""
    try:
        aws.ssm.get_parameter(Name=settings.gemini_param_name)
    except ClientError as err:
        if error_code(err) == "ParameterNotFound":
            raise StackError(f"Parameter {settings.gemini_param_name} was not found. "
                             "Run python3 store_gemini_key.py first (Part C).") from None
        raise


def create_weather_stack(settings: WeatherSettings, aws: AwsClients, db: DatabaseOutputs,
                         iam_wait: int = 15, sleep=time.sleep) -> WeatherStackOutputs:
    """The Python version of create_weather_stack.sh."""
    log.info("== 1. Checking the Gemini key and the database ==")
    require_gemini_key(aws, settings)
    if not db.db_host:
        raise StackError("No database address. Run python3 create_database.py first (Part E).")
    account_id = aws.sts.get_caller_identity()["Account"]

    log.info("== 2. IAM roles and instance profiles (one secret each) ==")
    for tier in settings.tiers:
        ensure_role_with_secret(aws, tier, secret_arn_for(settings, tier.name, account_id))
    log.info("Waiting %ss for IAM propagation...", iam_wait)
    sleep(iam_wait)

    log.info("== 3. Default VPC and subnets in both AZs ==")
    vpc_id = network.default_vpc_id(aws)
    subnets = [network.subnet_in_az(aws, vpc_id, settings.az_a),
               network.subnet_in_az(aws, vpc_id, settings.az_b)]

    log.info("== 4. Security groups ==")
    alb_sg_id = network.ensure_alb_security_group(aws, settings, vpc_id)
    network.restrict_app_security_group(aws, settings, alb_sg_id)

    log.info("== 5. Target groups and the load balancer (the ALB comes first this time) ==")
    tg_arns = {t.name: balancer.ensure_target_group(aws, settings, t, vpc_id) for t in settings.tiers}
    alb_arn, alb_dns = balancer.ensure_load_balancer(aws, settings, subnets, alb_sg_id)
    for tier in settings.tiers:
        balancer.ensure_listener(aws, alb_arn, tier.listener_port, tg_arns[tier.name])

    log.info("== 6. Launch templates (the advisor needs the ALB address to reach the weather tier) ==")
    ensure_launch_template(aws, settings, settings.weather, weather_user_data(settings, db.db_host))
    ensure_launch_template(aws, settings, settings.advisor, advisor_user_data(settings, alb_dns))

    log.info("== 7. Auto Scaling Groups (these launch the instances) ==")
    for tier in settings.tiers:
        scaling.ensure_asg(aws, tier, tg_arns[tier.name], subnets)

    return WeatherStackOutputs(
        vpc_id=vpc_id, subnet_a=subnets[0], subnet_b=subnets[1],
        alb_security_group_id=alb_sg_id, alb_arn=alb_arn, alb_dns_name=alb_dns,
        weather_tg_arn=tg_arns["weather"], advisor_tg_arn=tg_arns["advisor"],
    )


def delete_weather_stack(settings: WeatherSettings, aws: AwsClients, sleep=time.sleep) -> None:
    """The Python version of delete_weather_stack.sh. The Gemini key and the database are left alone."""
    log.info("== Deleting Auto Scaling Groups (this also terminates their instances) ==")
    for tier in settings.tiers:
        scaling.delete_asg(aws, tier.asg_name, sleep=sleep)

    log.info("== Deleting the load balancer and target groups ==")
    balancer.delete_load_balancer(aws, settings.alb_name)
    for tier in settings.tiers:
        balancer.delete_target_group(aws, tier.tg_name)

    log.info("== Restoring the app security group and deleting the ALB security group ==")
    vpc_id = network.default_vpc_id(aws)
    alb_sg_id = network.find_security_group(aws, settings.alb_sg_name, vpc_id)
    network.restore_app_security_group(aws, settings, alb_sg_id)
    if alb_sg_id:
        network.delete_alb_security_group(aws, alb_sg_id, sleep=sleep)

    log.info("== Deleting launch templates, roles and instance profiles ==")
    for tier in settings.tiers:
        compute.delete_launch_template(aws, tier)
        storage.delete_role_and_profile(aws, tier)

    WeatherStackOutputs.remove()
    log.info("Weather stack removed. The Gemini key parameter and the database were left in place.")
