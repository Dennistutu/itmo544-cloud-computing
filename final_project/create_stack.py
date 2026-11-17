#!/usr/bin/env python3
"""Build the whole Weather Advisor stack from nothing. Safe to run again (idempotent)."""
import argparse
import sys

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.network import StackError
from stack.project_config import ProjectSettings
from stack.project_stack import create_stack
from stack.runlog import record_start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = ProjectSettings.from_file(args.env)
    try:
        outputs = create_stack(settings, AwsClients.create(settings.region))
    except StackError as err:
        sys.exit(str(err))
    outputs.save()
    record_start(outputs.created_at)

    dns = outputs.alb_dns_name
    print(f"\nDone. Weather Advisor: http://{dns}:{settings.advisor.listener_port}/")
    print(f"Data tier health:  http://{dns}:{settings.weather.listener_port}/health")
    print("First boot installs Python and the libraries; allow 3 to 5 minutes for healthy targets.")
    print("Delete the stack when you stop:  python delete_stack.py")


if __name__ == "__main__":
    main()
