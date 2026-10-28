#!/usr/bin/env python3
"""Create the weather database (RDS PostgreSQL, free-tier size, private, no backups).

The Python version of create_database.sh.
"""
import argparse
import sys

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.database import create_database
from stack.network import StackError
from stack.weather_config import WeatherSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = WeatherSettings.from_file(args.env)
    try:
        outputs = create_database(settings, AwsClients.create(settings.region))
    except StackError as err:
        sys.exit(str(err))
    outputs.save()

    print()
    print(f"Done. Database endpoint: {outputs.db_host}")
    print("Automated backups are OFF (retention 0) and no final snapshot will be taken on delete.")
    print("Next: python3 create_weather_stack.py. When testing is finished: "
          "python3 delete_weather_stack.py then python3 delete_database.py")


if __name__ == "__main__":
    main()
