"""The final project stack: data stores, per-tier roles, ALB, Python boot scripts, ASGs.

Order of creation (each step needs the one before):
  1. Gemini key exists          (stored by store_gemini_key.py; never created here)
  2. Tables, topic, salt        (their ARNs go into the role policies)
  3. Roles and profiles         (least privilege, exact ARNs)
  4. Network and security groups
  5. Target groups, ALB, listeners  (the advisor needs the ALB address)
  6. Launch templates           (Python boot scripts with the settings)
  7. Auto Scaling Groups        (these start the instances)
Every ensure_* step is idempotent; every delete_* step tolerates things already gone.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from . import balancer, compute, data_stores, network, scaling, storage
from .boot import USER_DATA_LIMIT, build_boot_script, compress
from .clients import AwsClients
from .config import Tier
from .network import StackError
from .policies import trust_policy
from .project_config import ProjectSettings
from .project_policies import policy_for, wildcards_in
from .util import error_code, ignore_errors

log = logging.getLogger("stack")
OUTPUTS_FILE = "stack_outputs.json"


@dataclass
class StackOutputs:
    alb_dns_name: str
    alb_arn: str
    weather_tg_arn: str
    advisor_tg_arn: str
    alb_security_group_id: str
    topic_arn: str
    facts_table_arn: str
    advisor_table_arn: str
    created_at: float

    def tg_arn(self, tier_name: str) -> str:
        return {"weather": self.weather_tg_arn, "advisor": self.advisor_tg_arn}[tier_name]

    def save(self, directory: str | Path = ".") -> None:
        (Path(directory) / OUTPUTS_FILE).write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, directory: str | Path = ".") -> "StackOutputs":
        return cls(**json.loads((Path(directory) / OUTPUTS_FILE).read_text()))

    @classmethod
    def remove(cls, directory: str | Path = ".") -> None:
        (Path(directory) / OUTPUTS_FILE).unlink(missing_ok=True)


# ---- IAM ---------------------------------------------------------------------------------
def ensure_role(aws: AwsClients, tier: Tier, policy: dict) -> None:
    """Role + instance profile for one tier, with exactly the given inline policy."""
    if wildcards_in(policy):
        raise StackError(f"{tier.name} policy contains a wildcard: {wildcards_in(policy)}")
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_role(RoleName=tier.role_name,
                            AssumeRolePolicyDocument=json.dumps(trust_policy()))
    aws.iam.put_role_policy(RoleName=tier.role_name, PolicyName=tier.policy_name,
                            PolicyDocument=json.dumps(policy))
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_instance_profile(InstanceProfileName=tier.profile_name)
    profile = aws.iam.get_instance_profile(InstanceProfileName=tier.profile_name)
    if tier.role_name not in [r["RoleName"] for r in profile["InstanceProfile"]["Roles"]]:
        aws.iam.add_role_to_instance_profile(InstanceProfileName=tier.profile_name,
                                             RoleName=tier.role_name)
    log.info("%s role: %s", tier.name,
             ", ".join(a for st in policy["Statement"] for a in st["Action"]))


# ---- boot settings (non-secret only) -------------------------------------------------------
def weather_env(s: ProjectSettings) -> dict:
    return {"AWS_REGION": s.region, "APP_PORT": s.app_port, "FACTS_TABLE": s.facts_table,
            "FACTS_FRESH_SECONDS": s.facts_fresh_seconds,
            "FACTS_KEEP_SECONDS": s.facts_keep_seconds, "CONTACT_EMAIL": s.contact_email,
            "OUTSIDE_TIMEOUT_SECONDS": s.outside_timeout_seconds}


def advisor_env(s: ProjectSettings, alb_dns: str, topic_arn: str) -> dict:
    """The advisor gets parameter NAMES, never the Gemini key or the salt themselves."""
    return {"AWS_REGION": s.region, "APP_PORT": s.app_port, "ADVISOR_TABLE": s.advisor_table,
            "WEATHER_URL": f"http://{alb_dns}:{s.weather.listener_port}",
            "GEMINI_MODEL": s.gemini_model, "GEMINI_PARAM_NAME": s.gemini_param_name,
            "SALT_PARAM_NAME": s.salt_param_name, "SNS_TOPIC_ARN": topic_arn,
            "GEMINI_DAILY_CAP": s.gemini_daily_cap, "REPORT_LIMIT": s.report_limit,
            "REPORT_WINDOW_SECONDS": s.report_window_seconds, "EMAIL_LIMIT": s.email_limit,
            "EMAIL_WINDOW_SECONDS": s.email_window_seconds,
            "KEY_CACHE_SECONDS": s.key_cache_seconds, "REPORT_SECONDS": s.report_seconds,
            "WEATHER_TIMEOUT_SECONDS": s.weather_timeout_seconds,
            "GEMINI_TIMEOUT_SECONDS": s.gemini_timeout_seconds}


def launch_template_data(s: ProjectSettings, tier: Tier, script: str) -> dict:
    payload = compress(script)
    if len(payload) > USER_DATA_LIMIT:
        raise StackError(f"{tier.name} user-data is {len(payload)} bytes compressed, "
                         f"over the {USER_DATA_LIMIT} byte limit.")
    return {
        "ImageId": s.ami_id, "InstanceType": s.instance_type, "KeyName": s.key_name,
        "SecurityGroupIds": [s.app_sg_id],
        "IamInstanceProfile": {"Name": tier.profile_name},
        "UserData": base64.b64encode(payload).decode(),
        # IMDSv2 only: a stolen request cannot read the role's credentials with a plain GET.
        "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        "TagSpecifications": [{"ResourceType": "instance",
                               "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}]}],
    }


def ensure_launch_template(aws: AwsClients, s: ProjectSettings, tier: Tier, script: str) -> int:
    data = launch_template_data(s, tier, script)
    if not compute.launch_template_exists(aws, tier):
        aws.ec2.create_launch_template(LaunchTemplateName=tier.lt_name, LaunchTemplateData=data)
        log.info("Created launch template %s", tier.lt_name)
        return 1
    version = aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name, SourceVersion="1",
        LaunchTemplateData=data)["LaunchTemplateVersion"]["VersionNumber"]
    log.info("Template %s exists, published version %s", tier.lt_name, version)
    return version


# ---- create / delete -----------------------------------------------------------------------
def require_gemini_key(aws: AwsClients, s: ProjectSettings) -> None:
    try:
        aws.ssm.get_parameter(Name=s.gemini_param_name)      # existence only, not decrypted
    except ClientError as err:
        if error_code(err) == "ParameterNotFound":
            raise StackError(f"{s.gemini_param_name} not found. Run python store_gemini_key.py "
                             "first.") from None
        raise


def create_stack(s: ProjectSettings, aws: AwsClients, iam_wait: int = 15,
                 sleep=time.sleep, clock=time.time) -> StackOutputs:
    log.info("== 1. Gemini key ==")
    require_gemini_key(aws, s)
    account = aws.sts.get_caller_identity()["Account"]

    log.info("== 2. Tables (TTL, on-demand, no backups), email topic, salt ==")
    facts_arn = data_stores.ensure_table(aws, s.facts_table)
    advisor_arn = data_stores.ensure_table(aws, s.advisor_table)
    topic_arn = data_stores.ensure_topic(aws, s)
    data_stores.ensure_salt(aws, s)

    log.info("== 3. Per-tier roles (exact ARNs, no wildcards) ==")
    for tier in s.tiers:
        ensure_role(aws, tier, policy_for(tier.name, s, account))
    log.info("Waiting %ss for IAM propagation...", iam_wait)
    sleep(iam_wait)

    log.info("== 4. Network ==")
    vpc_id = network.default_vpc_id(aws)
    subnets = [network.subnet_in_az(aws, vpc_id, s.az_a), network.subnet_in_az(aws, vpc_id, s.az_b)]
    alb_sg_id = network.ensure_alb_security_group(aws, s, vpc_id)
    network.restrict_app_security_group(aws, s, alb_sg_id)      # app port from the ALB only

    log.info("== 5. Target groups, load balancer, listeners ==")
    tg = {t.name: balancer.ensure_target_group(aws, s, t, vpc_id, health_path="/health")
          for t in s.tiers}
    alb_arn, alb_dns = balancer.ensure_load_balancer(aws, s, subnets, alb_sg_id)
    for tier in s.tiers:
        balancer.ensure_listener(aws, alb_arn, tier.listener_port, tg[tier.name])

    log.info("== 6. Launch templates (Python boot scripts) ==")
    ensure_launch_template(aws, s, s.weather,
                           build_boot_script("weather", s.weather.app_dir, weather_env(s)))
    ensure_launch_template(aws, s, s.advisor,
                           build_boot_script("advisor", s.advisor.app_dir,
                                             advisor_env(s, alb_dns, topic_arn)))

    log.info("== 7. Auto Scaling Groups ==")
    for tier in s.tiers:
        scaling.ensure_asg(aws, tier, tg[tier.name], subnets)

    return StackOutputs(alb_dns_name=alb_dns, alb_arn=alb_arn, weather_tg_arn=tg["weather"],
                        advisor_tg_arn=tg["advisor"], alb_security_group_id=alb_sg_id,
                        topic_arn=topic_arn, facts_table_arn=facts_arn,
                        advisor_table_arn=advisor_arn, created_at=clock())


def delete_stack(s: ProjectSettings, aws: AwsClients, sleep=time.sleep) -> None:
    """Everything except the Gemini key, in reverse order. Safe to run twice."""
    log.info("== Auto Scaling Groups (terminates the instances) ==")
    for tier in s.tiers:
        scaling.delete_asg(aws, tier.asg_name, sleep=sleep)

    log.info("== Load balancer and target groups ==")
    balancer.delete_load_balancer(aws, s.alb_name)
    for tier in s.tiers:
        balancer.delete_target_group(aws, tier.tg_name)

    log.info("== Security groups ==")
    vpc_id = network.default_vpc_id(aws)
    alb_sg_id = network.find_security_group(aws, s.alb_sg_name, vpc_id)
    network.restore_app_security_group(aws, s, alb_sg_id)
    if alb_sg_id:
        network.delete_alb_security_group(aws, alb_sg_id, sleep=sleep)

    log.info("== Launch templates, roles, profiles ==")
    for tier in s.tiers:
        compute.delete_launch_template(aws, tier)
        storage.delete_role_and_profile(aws, tier)

    log.info("== Tables, email topic (with all subscriptions), salt ==")
    account = aws.sts.get_caller_identity()["Account"]
    data_stores.delete_topic(aws, s.topic_arn(account))
    for table in (s.facts_table, s.advisor_table):
        data_stores.delete_table(aws, table)
    data_stores.delete_salt(aws, s)

    StackOutputs.remove()
    log.info("Stack removed. Only the Gemini key parameter was kept.")
