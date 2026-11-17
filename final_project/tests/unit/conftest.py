"""Unit-test fixtures: no real AWS account is ever reachable from here."""
import dataclasses

import pytest
from moto import mock_aws

from stack.clients import AwsClients


@pytest.fixture(autouse=True)
def no_real_aws_credentials(monkeypatch, tmp_path):
    """Hide your real credentials and use fake ones, so a unit test can never touch AWS."""
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))


@pytest.fixture
def fake_aws(tmp_path, monkeypatch, fake_settings):
    """A fake AWS account holding what earlier labs created: the app security group, a key pair,
    an AMI, and the Gemini key parameter (with a fake value)."""
    monkeypatch.chdir(tmp_path)  # output files are written to the current folder
    with mock_aws():
        aws = AwsClients.create("us-east-2")
        vpc = aws.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]
        sg = aws.ec2.create_security_group(
            GroupName="app-sg", Description="app", VpcId=vpc["VpcId"])["GroupId"]
        aws.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 3000, "ToPort": 3000,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        aws.ec2.create_key_pair(KeyName="test-key")
        ami = aws.ec2.describe_images()["Images"][0]["ImageId"]
        aws.ssm.put_parameter(Name=fake_settings.gemini_param_name, Type="SecureString",
                              Value="fake-gemini-key-for-tests")
        settings = dataclasses.replace(fake_settings, app_sg_id=sg, ami_id=ami)
        yield settings, aws
