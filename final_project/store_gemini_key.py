#!/usr/bin/env python3
"""Read the Gemini API key at a hidden prompt, test it once, and store it as an SSM SecureString.

The key is never echoed, printed, logged, or written to a file.
"""
import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

import boto3

from stack.config import load_dotenv

TEST_PROMPT = "Suggest what to wear in 45 F weather with light rain, in one sentence."


def test_key(key: str, model: str) -> tuple[bool, str]:
    """Make one generateContent call. Returns (ok, model text or a short error message)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": TEST_PROMPT}]}]}).encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            data = json.load(resp)
        return True, data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except urllib.error.HTTPError as err:
        return False, f"HTTP {err.code} (404 = model name, 400/403 = key, 429 = rate limit)"
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as err:
        return False, f"{type(err).__name__}: {err}"


def store_key(ssm, name: str, key: str) -> None:
    """Write the key as a SecureString, replacing any older value."""
    ssm.put_parameter(Name=name, Type="SecureString", Value=key, Overwrite=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--no-test", action="store_true", help="store without calling Gemini first")
    args = parser.parse_args()
    env = load_dotenv(args.env)

    key = getpass.getpass("Gemini API key (input hidden): ").strip()
    if not key:
        sys.exit("No key entered.")

    if not args.no_test:
        ok, message = test_key(key, env["GEMINI_MODEL"])
        print(f"Test call to {env['GEMINI_MODEL']}: {'OK' if ok else 'FAILED'}")
        print(f"  {message}")
        if not ok:
            sys.exit("Not storing a key that failed its test. Fix the problem above and run again.")

    ssm = boto3.client("ssm", region_name=env["AWS_REGION"])
    store_key(ssm, env["GEMINI_PARAM_NAME"], key)
    del key

    info = ssm.describe_parameters(ParameterFilters=[
        {"Key": "Name", "Values": [env["GEMINI_PARAM_NAME"]]}])["Parameters"][0]
    print(f"Stored {info['Name']} as {info['Type']} ({info['Tier']} tier). The value was not printed.")


if __name__ == "__main__":
    main()
