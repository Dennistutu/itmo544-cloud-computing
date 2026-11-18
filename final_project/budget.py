#!/usr/bin/env python3
"""Cost report for the final project (Section 10): real hours the stack ran, Gemini
calls, and the cost of everything that is not free, against the $2.00 budget.

    python budget.py            # from stack_runs.json (written by create/delete)
    python budget.py --live     # also read today's Gemini counter from the running stack
"""
import argparse
import time
from datetime import datetime, timezone

from stack.config import load_dotenv
from stack.runlog import runs

# Free-tier and price assumptions, stated so the report can be checked.
NOTES = [
    "Stack rate covers the ALB, two t3.micro instances and their public IPv4 addresses "
    "(guide Section 10: about $0.07 per hour).",
    "Gemini: free tier; calls are capped by GEMINI_DAILY_CAP, so the charge is $0.",
    "NWS, Zippopotam.us and Open-Meteo: free, no key.",
    "DynamoDB on-demand, SSM standard parameters, SNS email: a few hundred requests per "
    "session, well under one cent; shown as $0.00.",
]


def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def report(run_list, hourly, budget, now, live_gemini=None):
    lines = ["Weather Advisor - cost report", "=" * 72,
             f"{'#':>2}  {'created':<20} {'deleted':<20} {'hours':>6} {'cost':>7} {'Gemini':>7}"]
    total_h = total_c = 0.0
    total_g = 0
    for i, r in enumerate(run_list, 1):
        stop = r.get("stop")
        hours = ((stop or now) - r["start"]) / 3600
        gem = sum((r.get("gemini_calls") or {}).values())
        if stop is None and live_gemini is not None:
            gem = live_gemini
        cost = hours * hourly
        total_h, total_c, total_g = total_h + hours, total_c + cost, total_g + gem
        lines.append(f"{i:>2}  {fmt(r['start']):<20} {(fmt(stop) if stop else 'STILL RUNNING'):<20} "
                     f"{hours:>6.2f} {cost:>7.3f} {gem:>7}")
    lines += ["-" * 72,
              f"    {'TOTAL':<41} {total_h:>6.2f} {total_c:>7.3f} {total_g:>7}", "",
              f"Stack rate:          ${hourly:.3f} per hour",
              f"Gemini, DynamoDB, SSM, SNS, weather APIs: $0.00 (see notes)",
              f"Total spent:         ${total_c:.2f} of ${budget:.2f} "
              f"({100 * total_c / budget:.0f}%)",
              f"Remaining:           ${budget - total_c:.2f}  "
              f"(about {(budget - total_c) / hourly:.1f} more stack hours)", ""]
    if any(r.get("stop") is None for r in run_list):
        lines.append("WARNING: the stack is running now. Run python delete_stack.py when you stop.")
    lines += ["Notes:"] + [f"  - {n}" for n in NOTES]
    return "\n".join(lines), total_c


def live_gemini_today(env):
    import boto3
    table = boto3.resource("dynamodb", region_name=env["AWS_REGION"]).Table(env["ADVISOR_TABLE"])
    item = table.get_item(Key={"pk": "gemini#" + time.strftime("%Y-%m-%d", time.gmtime())}).get("Item")
    return int(item["count"]) if item else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    env = load_dotenv(".env")
    live = live_gemini_today(env) if args.live else None
    text, _ = report(runs(), float(env["STACK_HOURLY_COST"]), float(env["PROJECT_BUDGET"]),
                     time.time(), live)
    print(text)


if __name__ == "__main__":
    main()
