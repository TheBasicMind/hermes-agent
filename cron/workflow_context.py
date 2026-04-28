"""Workflow Context preamble and needs file generation.

The preamble is prepended to a step's prompt at dispatch time so the running
agent sees its parent step results without needing to query canonical storage.
The needs file is the corresponding machine-readable payload, written to disk
so the agent can read the full structured form when the inline summary is
insufficient.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List

from hermes_constants import get_hermes_home


def _queue_dir(run_id: str) -> Path:
    d = get_hermes_home() / "cron" / "queue" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_preamble(run_id: str, workflow_name: str, step_id: str,
                    parents: List[Dict[str, Any]]) -> str:
    lines = [
        "## Workflow Context",
        f"You are running as step `{step_id}` of workflow `{workflow_name}`,",
        f"run_id `{run_id}`.",
        "",
        "Parent step results:",
    ]
    for p in parents:
        summary = p.get("result") or p.get("last_error") or ""
        summary = summary.replace("\n", " ")[:120]
        lines.append(f"- {p['step_id']}: {p['status']} — {summary}")
    lines += [
        "",
        "Full parent results JSON: $HERMES_WORKFLOW_NEEDS_FILE",
        "Use these results as authoritative input for this run; you do NOT",
        "need to re-derive parent outputs from canonical storage.",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def write_needs_file(run_id: str, step_id: str,
                     parents: List[Dict[str, Any]]) -> Path:
    p = _queue_dir(run_id) / f"{step_id}.needs.json"
    p.write_text(json.dumps({"parents": parents}, indent=2))
    return p
