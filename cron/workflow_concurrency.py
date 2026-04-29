"""In-process concurrency tracking for workflow runs and groups.

Lives in the gateway daemon's memory. Crash-restart resets counters,
which is fine because run states are also persisted in SQLite — only
'running' rows count toward `max_concurrent_runs`, and any in-flight
run that crashed mid-flight will be visible there for an operator to
clean up.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict

from cron.workflow_storage import _cursor


_group_counts: Dict[str, int] = defaultdict(int)


def _max_for(policy: str) -> int:
    if policy == "serial":
        return 1
    if policy == "parallel":
        return 10**9  # effectively unbounded
    if policy.startswith("parallel_max:"):
        return int(policy.split(":", 1)[1])
    raise ValueError(f"unknown group policy: {policy}")


def acquire_group_slot(group: str, policy: str) -> bool:
    cap = _max_for(policy)
    if _group_counts[group] >= cap:
        return False
    _group_counts[group] += 1
    return True


def release_group_slot(group: str) -> None:
    if _group_counts[group] > 0:
        _group_counts[group] -= 1


def can_start_run(workflow_name: str, *, max_concurrent_runs: int = 1) -> bool:
    # Use _cursor() (which closes the connection) rather than `with _connect()`,
    # whose context manager only commits the transaction and leaks the fd.
    with _cursor() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM workflow_runs WHERE workflow_name = ? AND status = 'running'",
            (workflow_name,),
        ).fetchone()[0]
    return n < max_concurrent_runs
