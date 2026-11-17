#!/usr/bin/env python3
"""Delete the weather database with no final snapshot and no kept backups.

The Python version of delete_database.sh. Run it after delete_weather_stack.py.
"""
import argparse

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.database import delete_database
from stack.weather_config import WeatherSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = WeatherSettings.from_file(args.env)
    delete_database(settings, AwsClients.create(settings.region))


if __name__ == "__main__":
    main()
