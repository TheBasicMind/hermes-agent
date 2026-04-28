"""Snapshot a cron job's executable fields into a workflow step record."""
from __future__ import annotations
from typing import Any, Dict

from cron.jobs import get_job


_FIELDS = ("prompt", "skills", "skill", "model", "provider", "base_url",
           "script", "deliver", "origin")


def snapshot_step(step: Dict[str, Any]) -> Dict[str, Any]:
    pkg_id = step["package"]
    job = get_job(pkg_id)
    if not job:
        raise KeyError(f"package '{pkg_id}' not found in cron jobs")
    snap = {k: job.get(k) for k in _FIELDS}
    snap["package_id"] = pkg_id
    return snap
