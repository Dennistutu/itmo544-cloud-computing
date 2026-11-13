"""Least-privilege policies for the two secrets, as pure functions that tests can check."""
from __future__ import annotations

from .weather_config import WeatherSettings


def read_one_parameter_policy(parameter_arn: str) -> dict:
    """Allow ssm:GetParameter on exactly one parameter, and nothing else."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": parameter_arn,
        }],
    }


def secret_arn_for(settings: WeatherSettings, tier_name: str, account_id: str) -> str:
    """The one secret each tier may read: weather gets the database password, advisor the Gemini key."""
    name = {"weather": settings.db_password_param, "advisor": settings.gemini_param_name}[tier_name]
    return settings.parameter_arn(account_id, name)
