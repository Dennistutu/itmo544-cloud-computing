#!/usr/bin/env python3
"""Delete everything create_stack.py made, except the Gemini key. Safe to run twice."""
import argparse

from stack.cli import setup_logging
from stack.clients import AwsClients
from stack.project_config import ProjectSettings
from stack.project_stack import delete_stack
from stack.runlog import record_stop


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    setup_logging()
    settings = ProjectSettings.from_file(args.env)
    delete_stack(settings, AwsClients.create(settings.region))
    record_stop()


if __name__ == "__main__":
    main()
