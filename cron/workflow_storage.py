"""SQLite persistence for workflow runs and steps (per-profile).

Database lives at ${HERMES_HOME}/cron/queue.db. Tables are created on first
use via init_db(). Each profile has its own queue.db; runs and steps never
cross profiles.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes_time import now as _hermes_now


def _db_path() -> Path:
    return get_hermes_home() / "cron" / "queue.db"


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    definition_snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_steps (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    snapshot TEXT NOT NULL,
    result TEXT,
    last_error TEXT,
    PRIMARY KEY (run_id, step_id),
    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_steps_status ON workflow_steps(run_id, status);
"""


def init_db() -> None:
    with _connect() as c:
        c.executescript(_SCHEMA)


@contextmanager
def _cursor():
    init_db()
    with _connect() as c:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def create_run(run_id: str, workflow_name: str, *, triggered_by: str,
               definition_snapshot: Dict[str, Any]) -> None:
    now = _hermes_now().isoformat()
    with _cursor() as c:
        c.execute(
            "INSERT INTO workflow_runs (run_id, workflow_name, triggered_by, "
            "triggered_at, started_at, status, definition_snapshot) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?)",
            (run_id, workflow_name, triggered_by, now, now,
             json.dumps(definition_snapshot)),
        )


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _cursor() as c:
        row = c.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(workflow_name: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    q = "SELECT * FROM workflow_runs"
    args: tuple = ()
    if workflow_name:
        q += " WHERE workflow_name = ?"
        args = (workflow_name,)
    q += " ORDER BY triggered_at DESC LIMIT ?"
    args = args + (limit,)
    with _cursor() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _cursor() as c:
        c.execute(f"UPDATE workflow_runs SET {cols} WHERE run_id = ?",
                  (*fields.values(), run_id))


def create_step(run_id: str, step_id: str, *, snapshot: Dict[str, Any],
                status: str = "pending") -> None:
    with _cursor() as c:
        c.execute(
            "INSERT INTO workflow_steps (run_id, step_id, status, snapshot) "
            "VALUES (?, ?, ?, ?)",
            (run_id, step_id, status, json.dumps(snapshot)),
        )


def update_step(run_id: str, step_id: str, **fields: Any) -> None:
    if not fields:
        return
    if "status" in fields and fields["status"] in ("running",) and "started_at" not in fields:
        fields["started_at"] = _hermes_now().isoformat()
    if "status" in fields and fields["status"] in ("succeeded", "failed", "skipped", "cancelled") \
            and "finished_at" not in fields:
        fields["finished_at"] = _hermes_now().isoformat()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _cursor() as c:
        c.execute(
            f"UPDATE workflow_steps SET {cols} WHERE run_id = ? AND step_id = ?",
            (*fields.values(), run_id, step_id),
        )


def get_step(run_id: str, step_id: str) -> Optional[Dict[str, Any]]:
    with _cursor() as c:
        row = c.execute(
            "SELECT * FROM workflow_steps WHERE run_id = ? AND step_id = ?",
            (run_id, step_id),
        ).fetchone()
    return dict(row) if row else None


def list_steps_for_run(run_id: str) -> List[Dict[str, Any]]:
    with _cursor() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM workflow_steps WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()]
