"""Dispatch a ready workflow step to the cron worker.

The dispatcher composes:
- the step's snapshot from workflow_storage,
- the parent context envelope from workflow_context (preamble + needs file),
- env-var injection (HERMES_WORKFLOW_RUN_ID, HERMES_WORKFLOW_NEEDS_FILE),

then invokes the worker and records the outcome on the step row, finally
calling advance + finalize so dependents become ready (or the run terminates).

``_run_worker`` is a thin adapter over :func:`cron.scheduler.run_job_in_subprocess`
— the dispatcher hands it the job snapshot plus a pre-built env, and the
adapter forwards only the workflow-specific overrides into the worker.
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional

from cron.scheduler import run_job_in_subprocess
from cron.workflow_concurrency import acquire_group_slot, release_group_slot
from cron.workflow_context import render_preamble, write_needs_file
from cron.workflow_runtime import advance, finalize_run_if_done
from cron.workflow_storage import get_run, get_step, update_step


_RESULT_CAP_BYTES = 64 * 1024


def _direct_parent_results(run_id: str, step_id: str,
                           wf: Dict[str, Any]) -> List[Dict[str, Any]]:
    step_defs = {s["id"]: s for s in wf["steps"]}
    needs = step_defs[step_id].get("needs", []) or []
    out: List[Dict[str, Any]] = []
    for nid in needs:
        st = get_step(run_id, nid) or {}
        out.append({
            "step_id": nid,
            "status": st.get("status"),
            "result": st.get("result"),
            "last_error": st.get("last_error"),
            "run_id": f"{run_id}.{nid}",
        })
    return out


def _run_worker(job: Dict[str, Any], *,
                env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Adapter to the real cron worker.

    ``env`` arrives from :func:`dispatch_step` as a *full* ``os.environ``
    copy plus the workflow-specific vars; the worker already inherits
    ``os.environ`` itself, so we forward only the workflow-scoped overrides
    to avoid clobbering unrelated env state.
    """
    overrides = {
        k: v for k, v in (env or {}).items()
        if k.startswith("HERMES_WORKFLOW_")
    }
    return run_job_in_subprocess(job, env_overrides=overrides)


def dispatch_step(run_id: str, step_id: str) -> None:
    run = get_run(run_id)
    if not run:
        raise KeyError(run_id)
    wf = json.loads(run["definition_snapshot"])
    step = get_step(run_id, step_id)
    if not step or step["status"] != "ready":
        raise RuntimeError(
            f"step '{step_id}' is not ready (status={step['status'] if step else 'missing'})"
        )

    snap = json.loads(step["snapshot"])
    parents = _direct_parent_results(run_id, step_id, wf)

    preamble = render_preamble(run_id, wf["name"], step_id, parents)
    needs_path = write_needs_file(run_id, step_id, parents)

    env = {**os.environ,
           "HERMES_WORKFLOW_RUN_ID": run_id,
           "HERMES_WORKFLOW_NEEDS_FILE": str(needs_path)}

    job_for_worker = {
        **snap,
        "id": f"{run_id}.{step_id}",
        "name": f"{wf['name']}/{step_id}",
        "prompt": preamble + (snap.get("prompt") or ""),
    }

    step_def = next((s for s in wf["steps"] if s["id"] == step_id), {})
    group = step_def.get("group")
    group_policy = (
        (wf.get("concurrency_group_defaults") or {}).get(group) if group else None
    )

    slot_acquired = False
    if group and group_policy:
        if not acquire_group_slot(group, group_policy):
            return  # leave step in 'ready'; retried next tick
        slot_acquired = True

    update_step(run_id, step_id, status="running")
    try:
        try:
            result = _run_worker(job_for_worker, env=env)
        except Exception as exc:
            update_step(run_id, step_id, status="failed",
                        last_error=str(exc)[:1000])
        else:
            stdout = (result.get("stdout") or "")[:_RESULT_CAP_BYTES]
            if result.get("exit_code", 0) == 0:
                update_step(run_id, step_id, status="succeeded", result=stdout)
            else:
                err_text = result.get("stderr") or stdout
                update_step(run_id, step_id, status="failed",
                            last_error=err_text[:1000])
    finally:
        if slot_acquired:
            release_group_slot(group)

    advance(run_id)
    finalize_run_if_done(run_id)
