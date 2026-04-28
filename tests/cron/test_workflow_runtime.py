import pytest
from cron.workflow_runtime import (
    evaluate_policy, start_run, advance, finalize_run_if_done,
)
from cron.workflow_storage import get_run, get_step, list_steps_for_run, update_step


def test_evaluate_policy_all_success():
    assert evaluate_policy("all_success", ["succeeded", "succeeded"]) == "ready"
    assert evaluate_policy("all_success", ["succeeded", "failed"]) == "skip"
    assert evaluate_policy("all_success", ["succeeded", "running"]) == "wait"


def test_evaluate_policy_any_success():
    assert evaluate_policy("any_success", ["failed", "succeeded"]) == "ready"
    assert evaluate_policy("any_success", ["failed", "failed"]) == "skip"
    assert evaluate_policy("any_success", ["failed", "running"]) == "wait"


def test_evaluate_policy_all_complete():
    assert evaluate_policy("all_complete", ["succeeded", "failed"]) == "ready"
    assert evaluate_policy("all_complete", ["running", "skipped"]) == "wait"


def test_evaluate_policy_any_complete():
    assert evaluate_policy("any_complete", ["running", "succeeded"]) == "ready"
    assert evaluate_policy("any_complete", ["pending", "running"]) == "wait"


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    monkeypatch.setattr(
        "cron.workflow_snapshot.get_job",
        lambda pid: {"id": pid, "prompt": f"P {pid}", "skills": [],
                     "skill": None, "model": None, "provider": None,
                     "base_url": None, "script": None, "deliver": "local",
                     "origin": None},
    )
    return tmp_path


def test_start_run_creates_pending_steps_and_advances(tmp_home):
    wf = {
        "name": "wf",
        "trigger": {"manual": True},
        "max_concurrent_runs": 1,
        "steps": [
            {"id": "a", "package": "p1"},
            {"id": "b", "package": "p2", "needs": ["a"]},
        ],
        "topo_order": ["a", "b"],
    }
    run_id = start_run(wf, triggered_by="manual")
    steps = list_steps_for_run(run_id)
    assert {s["step_id"]: s["status"] for s in steps} == {"a": "ready", "b": "pending"}


def test_finalize_succeeded_when_all_succeed(tmp_home):
    wf = {"name": "wf", "trigger": {"manual": True}, "steps": [
        {"id": "a", "package": "p1"}], "topo_order": ["a"]}
    run_id = start_run(wf, triggered_by="manual")
    update_step(run_id, "a", status="succeeded")
    assert finalize_run_if_done(run_id) == "succeeded"
    assert get_run(run_id)["status"] == "succeeded"


def test_finalize_partial_when_any_success_with_failure(tmp_home):
    wf = {"name": "wf", "trigger": {"manual": True}, "steps": [
        {"id": "a", "package": "p1"},
        {"id": "b", "package": "p2", "needs": ["a"], "needs_policy": "any_success"},
    ], "topo_order": ["a", "b"]}
    run_id = start_run(wf, triggered_by="manual")
    update_step(run_id, "a", status="failed", last_error="boom")
    advance(run_id)
    s = get_step(run_id, "b")
    # any_success policy with one parent that failed: cannot be satisfied yet
    # because the only parent is a — and it's failed. So b is skipped.
    assert s["status"] == "skipped"
    assert finalize_run_if_done(run_id) == "partial"


def test_skip_propagates_when_policy_unsatisfiable(tmp_home):
    wf = {"name": "wf", "trigger": {"manual": True}, "steps": [
        {"id": "a", "package": "p1"},
        {"id": "b", "package": "p2", "needs": ["a"], "needs_policy": "all_success"},
    ], "topo_order": ["a", "b"]}
    run_id = start_run(wf, triggered_by="manual")
    update_step(run_id, "a", status="failed", last_error="boom")
    advance(run_id)
    assert get_step(run_id, "b")["status"] == "skipped"
