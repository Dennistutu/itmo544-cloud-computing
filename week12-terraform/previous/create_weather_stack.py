#!/usr/bin/env python3
"""Build the weather advisor stack: the Python version of create_weather_stack.sh."""
import argparse
import sys

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.network import StackError
from stack.weather_config import WeatherSettings
from stack.weather_outputs import DatabaseOutputs
from stack.weather_stack import create_weather_stack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = WeatherSettings.from_file(args.env)
    try:
        db = DatabaseOutputs.load()
    except FileNotFoundError:
        sys.exit("db_outputs.json was not found. Run python3 create_database.py first (Part E).")
    try:
        outputs = create_weather_stack(settings, AwsClients.create(settings.region), db)
    except StackError as err:
        sys.exit(str(err))
    outputs.save()

    dns = outputs.alb_dns_name
    print()
    print(f"Done. ALB DNS name: {dns}")
    print(f"Weather service: http://{dns}:{settings.weather.listener_port}/weather?zip=60616 "
          "(saved lookups: /history)")
    print(f"Advisor page:    http://{dns}:{settings.advisor.listener_port}/")
    print("Give the target groups 2 to 3 minutes to report healthy (the first boot installs Node and your app).")


if __name__ == "__main__":
    main()
