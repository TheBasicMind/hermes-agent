"""Workflow run lifecycle + needs_policy evaluation."""
from __future__ import annotations
import json
import uuid
from typing import Any, Dict, List, Optional

from cron.workflow_snapshot import snapshot_step
from cron.workflow_storage import (
    create_run, create_step, get_run, list_steps_for_run,
    update_run, update_step,
)
from hermes_time import now as _hermes_now


_TERMINAL = {"succeeded", "failed", "skipped", "cancelled"}


def evaluate_policy(policy: str, parents_status: List[str]) -> str:
    """Return 'ready' (all conditions met), 'wait' (still pending parents),
    or 'skip' (policy unsatisfiable)."""
    succ = sum(1 for s in parents_status if s == "succeeded")
    term = sum(1 for s in parents_status if s in _TERMINAL)
    total = len(parents_status)

    if policy == "all_success":
        if succ == total:
            return "ready"
        if term == total:
            return "skip"
        return "wait"
    if policy == "any_success":
        if succ >= 1:
            return "ready"
        if term == total:
            return "skip"
        return "wait"
    if policy == "all_complete":
        if term == total:
            return "ready"
        return "wait"
    if policy == "any_complete":
        if term >= 1:
            return "ready"
        return "wait"
    raise ValueError(f"unknown needs_policy: {policy}")


def _new_run_id() -> str:
    return f"wf_{_hermes_now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"


def start_run(wf: Dict[str, Any], *, triggered_by: str) -> str:
    run_id = _new_run_id()
    create_run(run_id, wf["name"], triggered_by=triggered_by,
               definition_snapshot=wf)
    for s in wf["steps"]:
        snap = snapshot_step(s)
        create_step(run_id, s["id"], snapshot=snap, status="pending")
    advance(run_id)
    return run_id


def advance(run_id: str) -> List[str]:
    """Mark newly-ready steps; mark unsatisfiable ones as skipped.
    Returns the list of step ids transitioned to 'ready' on this call."""
    run = get_run(run_id)
    if not run:
        return []
    wf = json.loads(run["definition_snapshot"])
    steps = {s["step_id"]: s for s in list_steps_for_run(run_id)}
    step_defs = {s["id"]: s for s in wf["steps"]}

    just_ready: List[str] = []
    progress = True
    while progress:
        progress = False
        for sid, defn in step_defs.items():
            cur = steps[sid]["status"]
            if cur != "pending":
                continue
            needs = defn.get("needs", []) or []
            policy = defn.get("needs_policy", "all_success")
            parents_status = (
                [steps[n]["status"] for n in needs] if needs else ["succeeded"]
            )
            decision = evaluate_policy(policy, parents_status)
            if decision == "ready":
                update_step(run_id, sid, status="ready")
                steps[sid]["status"] = "ready"
                just_ready.append(sid)
                progress = True
            elif decision == "skip":
                update_step(run_id, sid, status="skipped",
                            last_error="needs_policy not satisfiable")
                steps[sid]["status"] = "skipped"
                progress = True  # may unblock dependents (any_*) or skip them
    return just_ready


def finalize_run_if_done(run_id: str) -> Optional[str]:
    """If every step is terminal, set the run status and return it."""
    steps = list_steps_for_run(run_id)
    statuses = [s["status"] for s in steps]
    if not all(st in _TERMINAL for st in statuses):
        return None

    if any(st == "cancelled" for st in statuses):
        final = "cancelled"
    elif all(st == "succeeded" for st in statuses):
        final = "succeeded"
    elif all(st == "failed" for st in statuses):
        final = "failed"
    else:
        # Mixed outcome: any combination of succeeded/failed/skipped that
        # isn't pure-success or pure-failure. Includes "some succeeded with
        # failures/skips" and "all-skip-with-some-fail" — both indicate the
        # run terminated without a clean win or a clean loss.
        final = "partial"
    update_run(run_id, status=final, finished_at=_hermes_now().isoformat())
    return final
