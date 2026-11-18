"""budget.py and the run log: real hours, Gemini calls, budget arithmetic."""
from budget import report
from stack import runlog


def test_hours_and_cost_from_runs():
    runs = [{"start": 0, "stop": 3600, "gemini_calls": {"2026-11-17": 4}},
            {"start": 7200, "stop": 7200 + 1800, "gemini_calls": {}}]
    text, cost = report(runs, 0.07, 2.00, now=10_000)
    assert abs(cost - 0.105) < 1e-9
    assert "1.50" in text and "$0.11 of $2.00" in text and "STILL RUNNING" not in text


def test_running_stack_is_counted_to_now_and_flagged():
    text, cost = report([{"start": 0, "stop": None}], 0.07, 2.00, now=7200, live_gemini=3)
    assert abs(cost - 0.14) < 1e-9 and "WARNING: the stack is running now" in text


def test_runlog_start_stop(tmp_path):
    path = tmp_path / "runs.json"
    runlog.record_start(100, path=path)
    runlog.record_start(200, path=path)          # create run again while up: no new entry
    runlog.record_stop(3700, gemini={"2026-11-17": 2}, path=path)
    runlog.record_stop(9999, path=path)          # delete run again: nothing changes
    assert runlog.runs(path) == [{"start": 100, "stop": 3700, "gemini_calls": {"2026-11-17": 2}}]
    assert runlog.hours_run(path=path) == 1.0
