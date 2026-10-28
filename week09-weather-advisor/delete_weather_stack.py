#!/usr/bin/env python3
"""Tear down the weather advisor stack: the Python version of delete_weather_stack.sh.

The database and the Gemini key parameter are left alone. Run delete_database.py afterwards.
"""
import argparse

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.weather_config import WeatherSettings
from stack.weather_stack import delete_weather_stack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = WeatherSettings.from_file(args.env)
    delete_weather_stack(settings, AwsClients.create(settings.region))


if __name__ == "__main__":
    main()
