"""Shared fixtures. Unit tests use fake settings, integration tests use your real .env."""
from pathlib import Path

import pytest

from stack.clients import AwsClients
from stack.weather_config import WeatherSettings
from stack.weather_outputs import DatabaseOutputs, WeatherStackOutputs

ROOT = Path(__file__).resolve().parent.parent

FAKE_VALUES = {
    "AWS_REGION": "us-east-2", "AMI_ID": "ami-12345678", "KEY_NAME": "test-key",
    "INSTANCE_TYPE": "t3.micro", "APP_SECURITY_GROUP_ID": "sg-placeholder",
    "AZ_A": "us-east-2a", "AZ_B": "us-east-2b", "APP_PORT": "3000",
    "ALB_NAME": "t-weather-alb", "ALB_SG_NAME": "t-weather-alb-sg",
    "WEATHER_ROLE_NAME": "t-weather-role", "WEATHER_PROFILE_NAME": "t-weather-profile",
    "WEATHER_LT_NAME": "t-weather-lt", "WEATHER_TG_NAME": "t-weather-tg",
    "WEATHER_ASG_NAME": "t-weather-asg", "WEATHER_LISTENER_PORT": "8080",
    "ADVISOR_ROLE_NAME": "t-advisor-role", "ADVISOR_PROFILE_NAME": "t-advisor-profile",
    "ADVISOR_LT_NAME": "t-advisor-lt", "ADVISOR_TG_NAME": "t-advisor-tg",
    "ADVISOR_ASG_NAME": "t-advisor-asg", "ADVISOR_LISTENER_PORT": "8081",
    "DB_INSTANCE_ID": "t-weather-db", "DB_INSTANCE_CLASS": "db.t4g.micro",
    "DB_ALLOCATED_STORAGE": "20", "DB_PORT": "5432", "DB_NAME": "weather",
    "DB_USER": "weatherapp", "DB_SG_NAME": "t-weather-db-sg",
    "DB_SUBNET_GROUP_NAME": "t-weather-db-subnets", "DB_PASSWORD_PARAM": "/t/db-password",
    "CACHE_SECONDS": "600", "GEMINI_PARAM_NAME": "/t/gemini-api-key",
    "GEMINI_MODEL": "gemini-test-model", "KEY_CACHE_SECONDS": "60",
    "CONTACT_EMAIL": "student@example.com",
}


@pytest.fixture
def fake_settings():
    """Fake names, but the real weather-app/ and advisor-app/ code, so sizes are realistic."""
    return WeatherSettings.from_values(FAKE_VALUES, base_dir=ROOT)


# ---- fixtures for tests that talk to your deployed stack ----

@pytest.fixture(scope="session")
def settings():
    return WeatherSettings.from_file(ROOT / ".env")


@pytest.fixture(scope="session")
def aws(settings):
    return AwsClients.create(settings.region)


@pytest.fixture(scope="session")
def outputs():
    try:
        return WeatherStackOutputs.load(ROOT)
    except FileNotFoundError:
        pytest.skip("stack_outputs.json not found. Run python3 create_weather_stack.py first.")


@pytest.fixture(scope="session")
def db():
    try:
        return DatabaseOutputs.load(ROOT)
    except FileNotFoundError:
        pytest.skip("db_outputs.json not found. Run python3 create_database.py first.")
