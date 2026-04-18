#!/usr/bin/env python3
"""Hermes-native tools for the OpenClaw market-intel intake database.

Backed by the copied OpenClaw Python API under:
  ~/.hermes/integrations/openclaw/market-intel/scripts/

Persistent data lives under:
  ~/.hermes/data/openclaw/market-intel/intake.db
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home, display_hermes_home
from tools.registry import registry, tool_error


HERMES_HOME = get_hermes_home()
MARKET_INTEL_DB = HERMES_HOME / "data" / "openclaw" / "market-intel" / "intake.db"
MARKET_INTEL_SCRIPTS = HERMES_HOME / "integrations" / "openclaw" / "market-intel" / "scripts"
KNOWLEDGE_URI_SCRIPTS = HERMES_HOME / "integrations" / "openclaw" / "knowledge-store" / "scripts"

_MARKET_MODULES = ["config", "db", "models", "intake_api", "uri"]


def _parse_metadata(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
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
                    f"Unknown action '{action}'. Use: add_candidate, add_pre_extracted, has_url, get_candidate, stats",
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
        "Use for candidate URL intake, existence checks, pre-extracted synth records, candidate lookup, and intake stats."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add_candidate", "add_pre_extracted", "has_url", "get_candidate", "stats"],
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
            "kb_record_id": {"type": "string", "description": "Knowledge-store record UUID for add_pre_extracted."}
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
