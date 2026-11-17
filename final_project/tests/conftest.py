"""Shared fixtures. Unit tests use fake settings; integration tests use your real .env."""
from pathlib import Path

import pytest

from stack.clients import AwsClients
from stack.config import load_dotenv
from stack.project_config import ProjectSettings
from stack.project_stack import StackOutputs

ROOT = Path(__file__).resolve().parent.parent


def fake_values() -> dict:
    """The real .env.example (so the test sees the same keys you deploy with), with test names."""
    v = load_dotenv(ROOT / ".env.example")
    v.update({"AMI_ID": "ami-12345678", "KEY_NAME": "test-key",
              "APP_SECURITY_GROUP_ID": "sg-placeholder", "CONTACT_EMAIL": "student@example.com",
              "GEMINI_PARAM_NAME": "/t/gemini-api-key", "SALT_PARAM_NAME": "/t/visitor-salt",
              "GEMINI_MODEL": "gemini-test-model"})
    return v


@pytest.fixture
def fake_settings():
    return ProjectSettings.from_values(fake_values(), base_dir=ROOT)


# ---- fixtures for tests that talk to your deployed stack ----
@pytest.fixture(scope="session")
def settings():
    return ProjectSettings.from_file(ROOT / ".env")


@pytest.fixture(scope="session")
def aws(settings):
    return AwsClients.create(settings.region)


@pytest.fixture(scope="session")
def outputs():
    try:
        return StackOutputs.load(ROOT)
    except FileNotFoundError:
        pytest.skip("stack_outputs.json not found. Run python create_stack.py first.")
