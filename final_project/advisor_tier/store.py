"""DynamoDB access for the advisor tier: saved reports, counters and limits.

One table, key "pk", TTL attribute "expires_at" on every item (Section 4.1).

    report#<zip>#<date>#<clothing>   saved report (facts + advice), no email
    gemini#<YYYY-MM-DD>              daily Gemini call counter (Decision 13)
    rl#<kind>#<visitor-hash>#<window> per-visitor request counter (Decision 12)

Visitor IPs are hashed with a per-deployment salt, so no address is stored.
"""
from __future__ import annotations

import hashlib
import json
import time

from botocore.exceptions import ClientError


def visitor_id(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:24]


class AdvisorStore:
    def __init__(self, table, report_seconds: int):
        self.table = table
        self.report_seconds = report_seconds

    # ---- saved reports
    @staticmethod
    def report_key(zip_code: str, day: str, clothing: str) -> str:
        return f"report#{zip_code}#{day}#{clothing}"

    def get_report(self, key: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        try:
            item = self.table.get_item(Key={"pk": key}).get("Item")
        except ClientError:
            return None
        if not item or int(item["expires_at"]) <= now:
            return None
        return json.loads(item["report"])

    def put_report(self, key: str, report: dict, now: float | None = None) -> None:
        now = now or time.time()
        try:
            self.table.put_item(Item={"pk": key, "report": json.dumps(report),
                                      "expires_at": int(now + self.report_seconds)})
        except ClientError:
            pass   # a report that could not be saved is still shown

    # ---- atomic counters
    def _increment_below(self, pk: str, limit: int, expires_at: int) -> bool:
        """Add 1 to the counter only if it is below `limit`. One atomic DynamoDB write,
        so two racing requests can never both take the last slot."""
        if limit <= 0:          # a cap of 0 means "never": no counter item exists yet to test
            return False
        try:
            self.table.update_item(
                Key={"pk": pk},
                UpdateExpression="ADD #c :one SET expires_at = if_not_exists(expires_at, :exp)",
                ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
                ExpressionAttributeNames={"#c": "count"},
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":exp": expires_at})
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def take_gemini_call(self, day_utc: str, cap: int, now: float | None = None) -> bool:
        """Reserve one Gemini call from today's cap. False means the cap is reached."""
        now = now or time.time()
        return self._increment_below(f"gemini#{day_utc}", cap, int(now + 2 * 86400))

    def count_failure(self, day_utc: str, now: float | None = None) -> None:
        """Count one Gemini failure for the day (Section 9: "count the failure")."""
        now = now or time.time()
        try:
            self.table.update_item(
                Key={"pk": f"gemini_fail#{day_utc}"},
                UpdateExpression="ADD #c :one SET expires_at = if_not_exists(expires_at, :exp)",
                ExpressionAttributeNames={"#c": "count"},
                ExpressionAttributeValues={":one": 1, ":exp": int(now + 2 * 86400)})
        except ClientError:
            pass

    def gemini_count(self, day_utc: str) -> int:
        item = self.table.get_item(Key={"pk": f"gemini#{day_utc}"}).get("Item") or {}
        return int(item.get("count", 0))

    def allow(self, kind: str, visitor: str, limit: int, window_seconds: int,
              now: float | None = None) -> bool:
        """Fixed-window rate limit per visitor and kind ("report" or "email")."""
        now = now or time.time()
        window = int(now // window_seconds)
        return self._increment_below(f"rl#{kind}#{visitor}#{window}", limit,
                                     int((window + 2) * window_seconds))
