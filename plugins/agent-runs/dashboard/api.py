"""Agent Runs dashboard plugin API.

Side-channel PTY runner for Codex/Claude/generic CLI sessions.

Design constraints:
- Full terminal transcript is persisted to disk and streamed to browsers.
- Terminal output is not fed into Hermes model context by default.
- Coordinator-facing endpoints return bounded structured metadata/summaries only.
- Runs are retained after completion and can be deleted only once completed.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import pty
import select
import shlex
import signal
import subprocess
import sys
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

try:
    from hermes_constants import get_hermes_home, get_subprocess_home
except Exception:  # pragma: no cover - plugin load fallback
    def get_hermes_home() -> Path:
        val = os.environ.get("HERMES_HOME", "").strip()
        if not val:
            raise RuntimeError("HERMES_HOME is required when hermes_constants is unavailable")
        return Path(val)

    def get_subprocess_home() -> Optional[str]:
        return None

try:
    from hermes_cli.config import cfg_get, load_config
except Exception:  # pragma: no cover - plugin load fallback
    def cfg_get(cfg: Optional[Dict[str, Any]], *keys: str, default: Any = None) -> Any:
        node: Any = cfg
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def load_config() -> Dict[str, Any]:
        return {}


router = APIRouter()

SESSION_HEADER = "X-Hermes-Session-Token"


def _dashboard_token() -> Optional[str]:
    """Return the web dashboard ephemeral token when available."""
    mod = sys.modules.get("hermes_cli.web_server")
    token = getattr(mod, "_SESSION_TOKEN", None) if mod is not None else None
    return token if isinstance(token, str) and token else None


def _require_token(request: Request) -> None:
    token = _dashboard_token()
    if not token:
        return
    supplied = request.headers.get(SESSION_HEADER, "")
    auth = request.headers.get("authorization", "")
    ok = hmac.compare_digest(supplied.encode(), token.encode()) or hmac.compare_digest(
        auth.encode(), f"Bearer {token}".encode()
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _websocket_token_ok(websocket: WebSocket) -> bool:
    token = _dashboard_token()
    if not token:
        return True
    supplied = websocket.query_params.get("token", "") or websocket.headers.get(SESSION_HEADER, "")
    return hmac.compare_digest(supplied.encode(), token.encode())


def _now() -> float:
    return time.time()


def _coerce_runs_root(value: Any) -> Optional[Path]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_absolute():
        path = get_hermes_home() / path
    return path


def _configured_runs_root() -> Optional[Path]:
    """Return shared Agent Runs storage root from env/config, if set.

    ``HERMES_AGENT_RUNS_HOME`` is process-wide and takes precedence so a
    launcher/service can make multiple profile dashboards share one store.
    ``dashboard.agent_runs_home`` is the persistent per-profile config hook.
    Without either, the plugin keeps its install-friendly default under the
    current profile home and creates the directory lazily on first use.
    """
    env_root = _coerce_runs_root(os.environ.get("HERMES_AGENT_RUNS_HOME"))
    if env_root is not None:
        return env_root
    try:
        config = load_config()
    except Exception:
        config = {}
    return _coerce_runs_root(cfg_get(config, "dashboard", "agent_runs_home"))


def _runs_root() -> Path:
    root = _configured_runs_root() or (get_hermes_home() / "agent-runs")
    (root / "transcripts").mkdir(parents=True, exist_ok=True)
    return root


def _metadata_path() -> Path:
    return _runs_root() / "sessions.json"


def _transcript_path(run_id: str) -> Path:
    return _runs_root() / "transcripts" / f"{run_id}.ansi"


def _load_sessions() -> Dict[str, Dict[str, Any]]:
    path = _metadata_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}
    return {}


def _save_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    path = _metadata_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sessions, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _update_session(run_id: str, **updates: Any) -> Dict[str, Any]:
    with _STORE_LOCK:
        sessions = _load_sessions()
        current = sessions.get(run_id)
        if current is None:
            raise KeyError(run_id)
        current.update(updates)
        current["updated_at"] = _now()
        sessions[run_id] = current
        _save_sessions(sessions)
        return dict(current)


_STORE_LOCK = threading.Lock()


class StartRunBody(BaseModel):
    harness: str = Field(default="generic", description="codex, claude, or generic")
    cwd: str = Field(..., description="Project working directory; AGENTS.md/CLAUDE.md are discovered from here")
    prompt: str = Field(default="", description="Task prompt for Codex/Claude adapters")
    command: Optional[str] = Field(default=None, description="Generic shell command, only for harness=generic")
    title: Optional[str] = None
    full_auto: bool = False
    extra_args: List[str] = Field(default_factory=list)


@dataclass
class LiveRun:
    run_id: str
    process: subprocess.Popen
    master_fd: int
    subscribers: set[asyncio.Queue[str]] = field(default_factory=set)
    loop: Optional[asyncio.AbstractEventLoop] = None
    reader: Optional[threading.Thread] = None


_LIVE_RUNS: Dict[str, LiveRun] = {}
_LIVE_LOCK = threading.Lock()


_COMPLETED = {"completed", "failed", "killed"}


def _sanitize_harness(harness: str) -> str:
    h = (harness or "generic").strip().lower()
    if h not in {"codex", "claude", "generic"}:
        raise HTTPException(status_code=400, detail="harness must be codex, claude, or generic")
    return h


def _build_command(body: StartRunBody) -> List[str] | str:
    harness = _sanitize_harness(body.harness)
    if harness == "codex":
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required for codex runs")
        cmd = ["codex", "exec"]
        if body.full_auto:
            cmd.append("--full-auto")
        cmd.extend(body.extra_args)
        cmd.append(body.prompt)
        return cmd
    if harness == "claude":
        if not body.prompt.strip():
            raise HTTPException(status_code=400, detail="prompt is required for claude runs")
        # Print mode is automation-friendly but still runs under a PTY so humans
        # can watch the stream in the dashboard. Project cwd ensures CLAUDE.md is honored.
        cmd = ["claude", "-p", body.prompt]
        cmd.extend(body.extra_args)
        return cmd
    if not body.command or not body.command.strip():
        raise HTTPException(status_code=400, detail="command is required for generic runs")
    return body.command


def _command_display(cmd: List[str] | str) -> str:
    if isinstance(cmd, str):
        return cmd
    return " ".join(shlex.quote(part) for part in cmd)


def _append_transcript(run_id: str, text: str) -> None:
    path = _transcript_path(run_id)
    with path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(text)


def _read_tail(run_id: str, max_chars: int = 12000) -> str:
    path = _transcript_path(run_id)
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) > max_chars * 4:
        data = data[-max_chars * 4 :]
    text = data.decode("utf-8", errors="replace")
    return text[-max_chars:]


def _broadcast(live: LiveRun, text: str) -> None:
    if not live.loop:
        return
    for queue in list(live.subscribers):
        try:
            live.loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception:
            pass


def _reader_thread(live: LiveRun) -> None:
    status = "completed"
    exit_code: Optional[int] = None
    try:
        while True:
            ready, _, _ = select.select([live.master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(live.master_fd, 8192)
                except OSError:
                    chunk = b""
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    _append_transcript(live.run_id, text)
                    _broadcast(live, text)
            exit_code = live.process.poll()
            if exit_code is not None:
                # Drain any final output.
                while True:
                    ready, _, _ = select.select([live.master_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        chunk = os.read(live.master_fd, 8192)
                    except OSError:
                        break
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    _append_transcript(live.run_id, text)
                    _broadcast(live, text)
                break
        existing = _load_sessions().get(live.run_id, {})
        status = "killed" if (existing.get("status") in {"killing", "killed"} or (isinstance(exit_code, int) and exit_code < 0)) else ("completed" if exit_code == 0 else "failed")
    except Exception as exc:
        status = "failed"
        _append_transcript(live.run_id, f"\n[agent-runs] reader error: {exc}\n")
    finally:
        try:
            os.close(live.master_fd)
        except Exception:
            pass
        ended_at = _now()
        try:
            current = _load_sessions().get(live.run_id, {})
            if current.get("status") in {"killing", "killed"}:
                status = "killed"
            meta = _update_session(
                live.run_id,
                status=status,
                exit_code=exit_code,
                ended_at=ended_at,
                duration_seconds=ended_at - current.get("created_at", ended_at),
                summary={
                    "status": status,
                    "exit_code": exit_code,
                    "transcript_tail": _read_tail(live.run_id, 4000),
                },
            )
        except Exception:
            meta = {"status": status, "exit_code": exit_code}
        _broadcast(live, f"\n[agent-runs] session {status}; exit_code={exit_code}\n")
        _broadcast(live, "\u0004")
        with _LIVE_LOCK:
            _LIVE_RUNS.pop(live.run_id, None)


def _public_session(meta: Dict[str, Any]) -> Dict[str, Any]:
    path = _transcript_path(meta["id"])
    data = dict(meta)
    data["transcript_bytes"] = path.stat().st_size if path.exists() else 0
    data["running"] = meta["id"] in _LIVE_RUNS
    return data


@router.get("/runs")
async def list_runs(request: Request):
    _require_token(request)
    sessions = _load_sessions()
    items = [_public_session(v) for v in sessions.values()]
    items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return {"runs": items}


@router.post("/runs")
async def start_run(body: StartRunBody, request: Request):
    _require_token(request)
    cwd = Path(body.cwd).expanduser().resolve()
    if not cwd.exists() or not cwd.is_dir():
        raise HTTPException(status_code=400, detail=f"cwd does not exist or is not a directory: {cwd}")
    harness = _sanitize_harness(body.harness)
    cmd = _build_command(body)
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    title = body.title or (body.prompt.strip().splitlines()[0][:80] if body.prompt.strip() else _command_display(cmd)[:80])
    meta = {
        "id": run_id,
        "title": title,
        "harness": harness,
        "cwd": str(cwd),
        "command": _command_display(cmd),
        "status": "running",
        "exit_code": None,
        "created_at": _now(),
        "updated_at": _now(),
        "ended_at": None,
        "duration_seconds": None,
        "finalized": False,
        "summary": None,
    }
    with _STORE_LOCK:
        sessions = _load_sessions()
        sessions[run_id] = meta
        _save_sessions(sessions)
    _transcript_path(run_id).write_text(
        f"[agent-runs] starting {harness} in {cwd}\n[agent-runs] command: {_command_display(cmd)}\n\n",
        encoding="utf-8",
    )

    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")
    profile_home = get_subprocess_home()
    if profile_home:
        env["HOME"] = profile_home
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            shell=isinstance(cmd, str),
            start_new_session=True,
            close_fds=True,
        )
    except FileNotFoundError as exc:
        os.close(master_fd)
        os.close(slave_fd)
        _update_session(run_id, status="failed", exit_code=127, ended_at=_now(), summary={"error": str(exc)})
        _append_transcript(run_id, f"[agent-runs] failed to start: {exc}\n")
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            os.close(slave_fd)
        except Exception:
            pass

    live = LiveRun(run_id=run_id, process=proc, master_fd=master_fd)
    try:
        live.loop = asyncio.get_running_loop()
    except RuntimeError:
        live.loop = None
    thread = threading.Thread(target=_reader_thread, args=(live,), name=f"agent-run-{run_id}", daemon=True)
    live.reader = thread
    with _LIVE_LOCK:
        _LIVE_RUNS[run_id] = live
    thread.start()
    return _public_session(meta)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    _require_token(request)
    meta = _load_sessions().get(run_id)
    if not meta:
        raise HTTPException(status_code=404, detail="run not found")
    return _public_session(meta)


@router.get("/runs/{run_id}/transcript")
async def get_transcript(run_id: str, request: Request, tail: Optional[int] = None):
    _require_token(request)
    if run_id not in _load_sessions():
        raise HTTPException(status_code=404, detail="run not found")
    path = _transcript_path(run_id)
    if not path.exists():
        return {"run_id": run_id, "content": ""}
    if tail is not None and tail > 0:
        content = _read_tail(run_id, min(tail, 200000))
    else:
        content = path.read_text(encoding="utf-8", errors="replace")
    return {"run_id": run_id, "content": content}


@router.get("/runs/{run_id}/summary")
async def get_summary(run_id: str, request: Request, tail_chars: int = 4000):
    """Bounded coordinator-facing run summary. Never returns the full transcript."""
    _require_token(request)
    meta = _load_sessions().get(run_id)
    if not meta:
        raise HTTPException(status_code=404, detail="run not found")
    tail_chars = max(0, min(tail_chars, 20000))
    return {
        "id": run_id,
        "title": meta.get("title"),
        "harness": meta.get("harness"),
        "cwd": meta.get("cwd"),
        "status": meta.get("status"),
        "exit_code": meta.get("exit_code"),
        "created_at": meta.get("created_at"),
        "ended_at": meta.get("ended_at"),
        "duration_seconds": meta.get("duration_seconds"),
        "finalized": meta.get("finalized", False),
        "transcript_tail": _read_tail(run_id, tail_chars),
    }


@router.post("/runs/{run_id}/input")
async def send_input(run_id: str, body: Dict[str, str], request: Request):
    _require_token(request)
    live = _LIVE_RUNS.get(run_id)
    if not live:
        raise HTTPException(status_code=409, detail="run is not live")
    data = body.get("data", "")
    os.write(live.master_fd, data.encode("utf-8", errors="replace"))
    return {"ok": True}


@router.post("/runs/{run_id}/kill")
async def kill_run(run_id: str, request: Request):
    _require_token(request)
    live = _LIVE_RUNS.get(run_id)
    if not live:
        raise HTTPException(status_code=409, detail="run is not live")
    try:
        os.killpg(live.process.pid, signal.SIGTERM)
    except Exception:
        live.process.terminate()
    _update_session(run_id, status="killing")
    _append_transcript(run_id, "\n[agent-runs] kill requested\n")
    return {"ok": True}


@router.post("/runs/{run_id}/finalize")
async def finalize_run(run_id: str, body: Dict[str, Any], request: Request):
    """Explicit Hermes/Kanban finalization marker; separate from process exit."""
    _require_token(request)
    meta = _load_sessions().get(run_id)
    if not meta:
        raise HTTPException(status_code=404, detail="run not found")
    if meta.get("status") not in _COMPLETED:
        raise HTTPException(status_code=409, detail="run must be completed before finalization")
    updates = {
        "finalized": True,
        "finalized_at": _now(),
        "finalization": {
            "status": body.get("status", "review_required"),
            "notes": body.get("notes", ""),
            "kanban_task_id": body.get("kanban_task_id"),
        },
    }
    return _public_session(_update_session(run_id, **updates))


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, request: Request):
    _require_token(request)
    with _STORE_LOCK:
        sessions = _load_sessions()
        meta = sessions.get(run_id)
        if not meta:
            raise HTTPException(status_code=404, detail="run not found")
        if meta.get("status") not in _COMPLETED:
            raise HTTPException(status_code=409, detail="can only delete completed runs")
        sessions.pop(run_id, None)
        _save_sessions(sessions)
    try:
        _transcript_path(run_id).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


@router.websocket("/runs/{run_id}/terminal")
async def terminal_ws(websocket: WebSocket, run_id: str):
    if not _websocket_token_ok(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if run_id not in _load_sessions():
        await websocket.send_text("[agent-runs] run not found\n")
        await websocket.close(code=1008)
        return

    # Replay current transcript first so reconnects/completed sessions are useful.
    replay = _read_tail(run_id, 200000)
    if replay:
        await websocket.send_text(replay)

    live = _LIVE_RUNS.get(run_id)
    if not live:
        await websocket.close()
        return

    queue: asyncio.Queue[str] = asyncio.Queue()
    live.subscribers.add(queue)
    try:
        while True:
            text = await queue.get()
            if text == "\u0004":
                await websocket.close()
                return
            await websocket.send_text(text)
    except WebSocketDisconnect:
        pass
    finally:
        live.subscribers.discard(queue)
