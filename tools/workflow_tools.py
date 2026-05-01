"""Agent-callable workflow tool (single compressed verb-dispatched action)."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from cron.workflow_loader import list_workflow_files, validate_workflow, workflows_dir
from cron.workflow_runtime import start_run, cancel_run
from cron.workflow_dispatcher import dispatch_step
from cron.workflow_storage import (
    get_run, list_runs, list_steps_for_run, get_step,
)


def _find_path(name: str) -> Path:
    for p in list_workflow_files():
        if p.stem == name:
            return p
    raise FileNotFoundError(f"workflow '{name}' not found in {workflows_dir()}")


def workflow(*, verb: str, name: Optional[str] = None,
             run_id: Optional[str] = None,
             step: Optional[str] = None) -> Dict[str, Any]:
    if verb == "list":
        out = []
        for p in list_workflow_files():
            try:
                wf = validate_workflow(p)
                out.append({"name": wf["name"], "trigger": wf["trigger"],
                            "steps": [s["id"] for s in wf["steps"]],
                            "valid": True})
            except Exception as exc:
                out.append({"name": p.stem, "valid": False, "error": str(exc)})
        return {"workflows": out}

    if verb == "show":
        if not name:
            raise ValueError("verb='show' requires name")
        wf = validate_workflow(_find_path(name))
        return {"workflow": wf}

    if verb == "validate":
        if not name:
            raise ValueError("verb='validate' requires name")
        try:
            wf = validate_workflow(_find_path(name))
            return {"ok": True, "topo_order": wf["topo_order"]}
        except FileNotFoundError:
            raise
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if verb == "run":
        if not name:
            raise ValueError("verb='run' requires name")
        wf = validate_workflow(_find_path(name))
        rid = start_run(wf, triggered_by="manual")
        # Non-blocking: do NOT dispatch the first ready step here.
        # The gateway's cron-tick (_tick_workflows) picks up ready steps
        # within ~60s.  Synchronous dispatch made this CLI verb block for
        # the entire duration of phase_1 (often 5–15 minutes), which left
        # runs stuck in 'running' whenever the calling agent / shell hit a
        # client-side timeout and aborted mid-dispatch.
        ready = [s["step_id"] for s in list_steps_for_run(rid) if s["status"] == "ready"]
        return {
            "run_id": rid,
            "status": "queued",
            "ready_steps": ready,
            "note": "Steps will dispatch on the next gateway cron tick (≤60s). "
                    "Use verb='status' to poll progress; verb='cancel' to abort.",
        }

    if verb == "status":
        if not run_id:
            raise ValueError("verb='status' requires run_id")
        return {"run": get_run(run_id), "steps": list_steps_for_run(run_id)}

    if verb == "logs":
        if not run_id:
            raise ValueError("verb='logs' requires run_id")
        if step:
            s = get_step(run_id, step)
            return {"step": s, "result": (s or {}).get("result")}
        return {"steps": [{"step_id": s["step_id"], "status": s["status"],
                           "result": s.get("result"),
                           "last_error": s.get("last_error")}
                          for s in list_steps_for_run(run_id)]}

    if verb == "cancel":
        if not run_id:
            raise ValueError("verb='cancel' requires run_id")
        cancel_run(run_id)
        return {"cancelled": run_id}

    if verb == "runs":
        return {"runs": list_runs(name)}

    raise ValueError(f"unknown verb: {verb}")
