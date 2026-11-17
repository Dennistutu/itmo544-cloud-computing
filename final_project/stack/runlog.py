"""A small log of when the stack was created and deleted, for budget.py (real hours)."""
from __future__ import annotations

import json
import time
from pathlib import Path

RUNLOG = Path("stack_runs.json")


def _read(path: Path = RUNLOG) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []


def record_start(at: float | None = None, path: Path = RUNLOG) -> None:
    runs = _read(path)
    if runs and runs[-1].get("stop") is None:      # a re-run of create while already up
        return
    runs.append({"start": at or time.time(), "stop": None})
    path.write_text(json.dumps(runs, indent=2) + "\n")


def record_stop(at: float | None = None, path: Path = RUNLOG) -> None:
    runs = _read(path)
    if runs and runs[-1].get("stop") is None:
        runs[-1]["stop"] = at or time.time()
        path.write_text(json.dumps(runs, indent=2) + "\n")


def hours_run(now: float | None = None, path: Path = RUNLOG) -> float:
    """Total hours the stack has existed, counting a still-running stack up to now."""
    now = now or time.time()
    return sum(((r["stop"] or now) - r["start"]) for r in _read(path)) / 3600
