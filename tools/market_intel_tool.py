#!/usr/bin/env python3
"""Hermes-native tools for the OpenClaw market-intel intake database.

Backed by the copied OpenClaw Python API under:
  ~/.hermes/integrations/openclaw/market-intel/scripts/

Persistent data lives under:
  ~/.hermes/data/openclaw/market-intel/intake.db
"""

from __future__ import annotations

import hashlib
import importlib
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home, display_hermes_home
from tools.registry import registry, tool_error


HERMES_HOME = get_hermes_home()
MARKET_INTEL_DB = HERMES_HOME / "data" / "openclaw" / "market-intel" / "intake.db"
MARKET_INTEL_MEDIA_CACHE_DIR = HERMES_HOME / "data" / "openclaw" / "market-intel" / "media-cache"
MARKET_INTEL_SCRIPTS = HERMES_HOME / "integrations" / "openclaw" / "market-intel" / "scripts"
KNOWLEDGE_URI_SCRIPTS = HERMES_HOME / "integrations" / "openclaw" / "knowledge-store" / "scripts"

_MARKET_MODULES = ["config", "db", "models", "intake_api", "uri"]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _cache_media_file(local_path: str, media_url: str, mime_type: str | None = None) -> tuple[str, str, int]:
    """Copy local media file into managed market-intel media cache and return (cached_path, sha256, size_bytes)."""
    src = Path(local_path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"local_path not found or not a file: {src}")

    sha = _sha256_file(src)
    size_bytes = src.stat().st_size

    ext = src.suffix
    if not ext and mime_type:
        guessed = mimetypes.guess_extension(mime_type)
        ext = guessed or ""

    # Preserve some source identity for debugging while keeping dedupe by hash stable.
    parsed = urllib.parse.urlparse(media_url)
    base_name = Path(parsed.path).name or "media"
    safe_base = "".join(ch for ch in base_name if ch.isalnum() or ch in ("-", "_", "."))[:80] or "media"
    cached_name = f"{sha[:16]}_{safe_base}"
    if ext and not cached_name.endswith(ext):
        cached_name = f"{cached_name}{ext}"

    MARKET_INTEL_MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = (MARKET_INTEL_MEDIA_CACHE_DIR / cached_name).resolve()
    if not dest.exists():
        shutil.copy2(src, dest)

    return str(dest), sha, size_bytes


def _download_remote_media(media_url: str) -> tuple[str, str | None]:
    """Download remote media URL to a temp file. Returns (temp_path, content_type)."""
    req = urllib.request.Request(
        media_url,
        headers={
            "User-Agent": "HermesMarketIntel/1.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        content_type = resp.headers.get("Content-Type")
        with tempfile.NamedTemporaryFile(delete=False, prefix="mi-media-", suffix=".bin") as tmp:
            shutil.copyfileobj(resp, tmp)
            return tmp.name, content_type


def _parse_metadata(raw: str | None) -> dict[str, Any] | None:
    if raw is None or str(raw).strip() == "":
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object")
    return parsed


def _reset_market_modules() -> None:
    for name in _MARKET_MODULES:
        sys.modules.pop(name, None)


def _load_market_store():
    if not MARKET_INTEL_SCRIPTS.exists():
        raise FileNotFoundError(
            f"Market-intel integration scripts not found at {MARKET_INTEL_SCRIPTS}"
        )
    MARKET_INTEL_DB.parent.mkdir(parents=True, exist_ok=True)
    os.environ["INTEL_DB_PATH"] = str(MARKET_INTEL_DB)
    sys.path.insert(0, str(KNOWLEDGE_URI_SCRIPTS))
    sys.path.insert(0, str(MARKET_INTEL_SCRIPTS))
    try:
        _reset_market_modules()
        config = importlib.import_module("config")
        db_mod = importlib.import_module("db")
        api_mod = importlib.import_module("intake_api")
        cfg = config.get_config()
        db = db_mod.IntakeDB(db_path=cfg["db_path"])
        db.initialize()
        return api_mod.IntakeStore(db=db)
    finally:
        for path in (str(MARKET_INTEL_SCRIPTS), str(KNOWLEDGE_URI_SCRIPTS)):
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def market_intel_intake(
    action: str,
    url: str | None = None,
    candidate_id: str | None = None,
    title: str | None = None,
    snippet: str | None = None,
    source_id: str | None = None,
    source_type: str | None = None,
    metadata: str | None = None,
    content_type: str | None = None,
    kb_record_id: str | None = None,
    asset_id: str | None = None,
    media_url: str | None = None,
    local_path: str | None = None,
    platform: str | None = None,
    media_type: str | None = None,
    mime_type: str | None = None,
    limit: int | None = None,
) -> str:
    try:
        store = _load_market_store()
        try:
            parsed_metadata = _parse_metadata(metadata)
            if action == "add_candidate":
                if not url:
                    return tool_error("url is required for add_candidate", success=False)
                result = store.add_candidate(
                    url=url,
                    title=title,
                    snippet=snippet,
                    source_id=source_id,
                    source_type=source_type,
                    metadata=parsed_metadata,
                    content_type=content_type,
                )
                if is_dataclass(result):
                    result = asdict(result)
            elif action == "add_pre_extracted":
                if not url or not title or not kb_record_id:
                    return tool_error(
                        "url, title, and kb_record_id are required for add_pre_extracted",
                        success=False,
                    )
                result = store.add_pre_extracted(
                    url=url,
                    title=title,
                    kb_record_id=kb_record_id,
                    source_id=source_id,
                    source_type=source_type,
                    snippet=snippet,
                    metadata=parsed_metadata,
                    content_type=content_type,
                )
                if is_dataclass(result):
                    result = asdict(result)
            elif action == "cache_media":
                if not url or not media_url or not local_path:
                    return tool_error(
                        "url, media_url, and local_path are required for cache_media",
                        success=False,
                    )
                cached_path, sha256, size_bytes = _cache_media_file(
                    local_path=local_path,
                    media_url=media_url,
                    mime_type=mime_type,
                )
                result = store.cache_media(
                    source_url=url,
                    original_media_url=media_url,
                    cached_path=cached_path,
                    candidate_id=candidate_id,
                    platform=platform,
                    media_type=media_type,
                    mime_type=mime_type,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    metadata=parsed_metadata,
                )
                record = result.get("record") if isinstance(result, dict) else None
                if is_dataclass(record):
                    result["record"] = asdict(record)
            elif action == "cache_media_remote":
                if not url or not media_url:
                    return tool_error(
                        "url and media_url are required for cache_media_remote",
                        success=False,
                    )
                temp_path = None
                try:
                    temp_path, downloaded_mime = _download_remote_media(media_url)
                    effective_mime = mime_type or downloaded_mime
                    cached_path, sha256, size_bytes = _cache_media_file(
                        local_path=temp_path,
                        media_url=media_url,
                        mime_type=effective_mime,
                    )
                finally:
                    if temp_path:
                        try:
                            Path(temp_path).unlink(missing_ok=True)
                        except Exception:
                            pass

                result = store.cache_media(
                    source_url=url,
                    original_media_url=media_url,
                    cached_path=cached_path,
                    candidate_id=candidate_id,
                    platform=platform,
                    media_type=media_type,
                    mime_type=(mime_type or downloaded_mime),
                    sha256=sha256,
                    size_bytes=size_bytes,
                    metadata=parsed_metadata,
                )
                record = result.get("record") if isinstance(result, dict) else None
                if is_dataclass(record):
                    result["record"] = asdict(record)
            elif action == "get_media":
                if not asset_id:
                    return tool_error("asset_id is required for get_media", success=False)
                media = store.get_cached_media(asset_id)
                result = asdict(media) if media else None
            elif action == "list_media":
                media = store.list_cached_media(
                    source_url=url,
                    candidate_id=candidate_id,
                    platform=platform,
                    limit=limit or 100,
                )
                result = [asdict(m) for m in media]
            elif action == "media_stats":
                result = store.cached_media_stats()
            elif action == "has_url":
                if not url:
                    return tool_error("url is required for has_url", success=False)
                result = {"url": url, "exists": store.has_url(url)}
            elif action == "get_candidate":
                if not candidate_id:
                    return tool_error("candidate_id is required for get_candidate", success=False)
                candidate = store.get(candidate_id)
                result = asdict(candidate) if candidate else None
            elif action == "stats":
                result = store.stats()
            else:
                return tool_error(
                    f"Unknown action '{action}'. Use: add_candidate, add_pre_extracted, cache_media, cache_media_remote, get_media, list_media, media_stats, has_url, get_candidate, stats",
                    success=False,
                )
            return json.dumps({
                "success": True,
                "action": action,
                "db_path": str(MARKET_INTEL_DB),
                "result": result,
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


def market_intel_triage(
    action: str,
    candidate_id: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
    kb_record_id: str | None = None,
    source_score: float | None = None,
    relevance_score: float | None = None,
    novelty_score: float | None = None,
    actionability_score: float | None = None,
    limit: int | None = None,
) -> str:
    try:
        store = _load_market_store()
        try:
            if action == "transition":
                if not candidate_id or not to_status:
                    return tool_error("candidate_id and to_status are required for transition", success=False)
                result = asdict(store.transition(
                    candidate_id=candidate_id,
                    to_status=to_status,
                    source_score=source_score,
                    relevance_score=relevance_score,
                    novelty_score=novelty_score,
                    actionability_score=actionability_score,
                    decision_reason=reason,
                ))
            elif action == "reject":
                if not candidate_id:
                    return tool_error("candidate_id is required for reject", success=False)
                result = asdict(store.reject(candidate_id, reason=reason))
            elif action == "mark_extracted":
                if not candidate_id or not kb_record_id:
                    return tool_error("candidate_id and kb_record_id are required for mark_extracted", success=False)
                result = asdict(store.mark_extracted(candidate_id, kb_record_id=kb_record_id))
            elif action == "pending_triage":
                result = [asdict(c) for c in store.pending_triage(limit=limit or 50)]
            elif action == "extraction_queue":
                result = [asdict(c) for c in store.extraction_queue(limit=limit or 50)]
            else:
                return tool_error(
                    f"Unknown action '{action}'. Use: transition, reject, mark_extracted, pending_triage, extraction_queue",
                    success=False,
                )
            return json.dumps({
                "success": True,
                "action": action,
                "db_path": str(MARKET_INTEL_DB),
                "result": result,
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


MARKET_INTEL_INTAKE_SCHEMA = {
    "name": "market_intel_intake",
    "description": (
        "Operate the market-intel intake database copied from OpenClaw into Hermes. "
        f"Backed by {display_hermes_home()}/data/openclaw/market-intel/intake.db. "
        "Use for candidate URL intake, existence checks, pre-extracted synth records, candidate lookup, media caching, and intake stats."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "add_candidate", "add_pre_extracted", "cache_media", "cache_media_remote", "get_media", "list_media", "media_stats",
                    "has_url", "get_candidate", "stats"
                ],
                "description": "Operation to perform."
            },
            "url": {"type": "string", "description": "Candidate URL or synthetic URI."},
            "candidate_id": {"type": "string", "description": "Candidate UUID for get_candidate."},
            "title": {"type": "string", "description": "Candidate title."},
            "snippet": {"type": "string", "description": "Short snippet or summary."},
            "source_id": {"type": "string", "description": "Source identifier such as scout/grok/last30days."},
            "source_type": {"type": "string", "description": "Source type such as rss, synth, api."},
            "metadata": {"type": "string", "description": "Optional JSON object encoded as a string."},
            "content_type": {"type": "string", "description": "Optional content type classification."},
            "kb_record_id": {"type": "string", "description": "Knowledge-store record UUID for add_pre_extracted."},
            "asset_id": {"type": "string", "description": "Cached media asset UUID for get_media."},
            "media_url": {"type": "string", "description": "Original remote media URL for cache_media."},
            "local_path": {"type": "string", "description": "Local downloaded media path for cache_media."},
            "platform": {"type": "string", "description": "Platform label (x, youtube, instagram, etc)."},
            "media_type": {"type": "string", "description": "Media type (image, video, audio, gif, document, etc)."},
            "mime_type": {"type": "string", "description": "Optional MIME type for cached media."},
            "limit": {"type": "integer", "description": "Optional max rows for list_media."}
        },
        "required": ["action"]
    }
}


MARKET_INTEL_TRIAGE_SCHEMA = {
    "name": "market_intel_triage",
    "description": (
        "Operate the market-intel triage/extraction queue copied from OpenClaw into Hermes. "
        "Use for candidate state transitions, rejection, extraction queue reads, and linking extracted candidates to knowledge-store records."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["transition", "reject", "mark_extracted", "pending_triage", "extraction_queue"],
                "description": "Operation to perform."
            },
            "candidate_id": {"type": "string", "description": "Candidate UUID."},
            "to_status": {"type": "string", "description": "Destination status for transition."},
            "reason": {"type": "string", "description": "Optional decision reason or rejection reason."},
            "kb_record_id": {"type": "string", "description": "Knowledge-store record UUID for mark_extracted."},
            "source_score": {"type": "number", "description": "Optional source score."},
            "relevance_score": {"type": "number", "description": "Optional relevance score."},
            "novelty_score": {"type": "number", "description": "Optional novelty score."},
            "actionability_score": {"type": "number", "description": "Optional actionability score."},
            "limit": {"type": "integer", "description": "Optional queue size limit."}
        },
        "required": ["action"]
    }
}


registry.register(
    name="market_intel_intake",
    toolset="market_intel",
    schema=MARKET_INTEL_INTAKE_SCHEMA,
    handler=lambda args, **kw: market_intel_intake(
        action=args.get("action", ""),
        url=args.get("url"),
        candidate_id=args.get("candidate_id"),
        title=args.get("title"),
        snippet=args.get("snippet"),
        source_id=args.get("source_id"),
        source_type=args.get("source_type"),
        metadata=args.get("metadata"),
        content_type=args.get("content_type"),
        kb_record_id=args.get("kb_record_id"),
        asset_id=args.get("asset_id"),
        media_url=args.get("media_url"),
        local_path=args.get("local_path"),
        platform=args.get("platform"),
        media_type=args.get("media_type"),
        mime_type=args.get("mime_type"),
        limit=args.get("limit"),
    ),
    emoji="📡",
)

registry.register(
    name="market_intel_triage",
    toolset="market_intel",
    schema=MARKET_INTEL_TRIAGE_SCHEMA,
    handler=lambda args, **kw: market_intel_triage(
        action=args.get("action", ""),
        candidate_id=args.get("candidate_id"),
        to_status=args.get("to_status"),
        reason=args.get("reason"),
        kb_record_id=args.get("kb_record_id"),
        source_score=args.get("source_score"),
        relevance_score=args.get("relevance_score"),
        novelty_score=args.get("novelty_score"),
        actionability_score=args.get("actionability_score"),
        limit=args.get("limit"),
    ),
    emoji="📡",
)
