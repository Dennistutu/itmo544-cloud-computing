import pytest

from stack.config import ConfigError
from stack.weather_config import WeatherSettings
from tests.conftest import FAKE_VALUES


def test_tiers_and_numbers_come_from_env(fake_settings):
    weather, advisor = fake_settings.tiers
    assert (weather.name, weather.listener_port, weather.app_dir.name) == ("weather", 8080, "weather-app")
    assert (advisor.name, advisor.listener_port, advisor.app_dir.name) == ("advisor", 8081, "advisor-app")
    assert fake_settings.db_instance_class == "db.t4g.micro"
    assert (fake_settings.db_allocated_storage, fake_settings.db_port, fake_settings.cache_seconds) == (20, 5432, 600)


def test_missing_keys_are_named_in_the_error():
    values = dict(FAKE_VALUES)
    del values["DB_INSTANCE_CLASS"]
    with pytest.raises(ConfigError, match="DB_INSTANCE_CLASS"):
        WeatherSettings.from_values(values)


def test_a_non_number_gives_a_clear_error():
    with pytest.raises(ConfigError, match="CACHE_SECONDS must be a whole number"):
        WeatherSettings.from_values(dict(FAKE_VALUES, CACHE_SECONDS="ten minutes"))


def test_parameter_arn_has_a_single_slash(fake_settings):
    arn = fake_settings.parameter_arn("123456789012", "/t/db-password")
    assert arn == "arn:aws:ssm:us-east-2:123456789012:parameter/t/db-password"
