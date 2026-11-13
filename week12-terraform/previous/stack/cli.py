"""Shared setup for the command-line scripts."""
import logging


def setup_logging() -> None:
    """Show our own progress messages, and keep boto's internal logging quiet.

    botocore can log request details at debug level. Pinning it to WARNING is a
    second guard (after simply never logging secrets ourselves) that no request
    containing a password can reach the screen.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("botocore", "boto3", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
