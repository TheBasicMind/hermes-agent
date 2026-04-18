#!/usr/bin/env python3
"""Hermes-native tools for the OpenClaw-derived knowledge store.

Backed by the copied OpenClaw Python API under:
  ~/.hermes/integrations/openclaw/knowledge-store/scripts/

Persistent data lives under:
  ~/.hermes/data/openclaw/knowledge-store/knowledge.db
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from hermes_constants import get_hermes_home, display_hermes_home
from tools.registry import registry, tool_error


HERMES_HOME = get_hermes_home()
KNOWLEDGE_STORE_DB = HERMES_HOME / "data" / "openclaw" / "knowledge-store" / "knowledge.db"
KNOWLEDGE_STORE_SCRIPTS = HERMES_HOME / "integrations" / "openclaw" / "knowledge-store" / "scripts"

_KNOWLEDGE_MODULES = [
    "config", "db", "models", "chunker", "stability", "uri", "search", "kb_api", "providers", "providers.together"
]


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _load_runtime_config() -> dict[str, Any]:
    config_path = HERMES_HOME / "config.yaml"
    if not config_path.exists():
        return {}
    return yaml.safe_load(config_path.read_text()) or {}


def _knowledge_store_config() -> dict[str, Any]:
    cfg = _load_runtime_config().get("knowledge_store", {}) or {}
    db_path = cfg.get("db_path") or "data/openclaw/knowledge-store/knowledge.db"
    db_path = Path(db_path)
    if not db_path.is_absolute():
        db_path = HERMES_HOME / db_path
    return {
        "db_path": str(db_path),
        "embed_provider": cfg.get("embed_provider", "together"),
        "embed_model": cfg.get("embed_model", "intfloat/multilingual-e5-large-instruct"),
        "embed_dimensions": int(cfg.get("embed_dimensions", 1024)),
        "embed_base_url": cfg.get("embed_base_url", "https://api.together.xyz/v1"),
        "embed_batch_size": int(cfg.get("embed_batch_size", 128)),
        "embed_timeout": int(cfg.get("embed_timeout", 30)),
        "embed_max_retries": int(cfg.get("embed_max_retries", 3)),
        "chunk_size": int(cfg.get("chunk_size", 512)),
        "chunk_overlap": int(cfg.get("chunk_overlap", 64)),
        "chunk_min_size": int(cfg.get("chunk_min_size", 64)),
        "recheck_static_days": cfg.get("recheck_static_days", "never"),
        "recheck_semi_days": int(cfg.get("recheck_semi_days", 30)),
        "recheck_dynamic_days": int(cfg.get("recheck_dynamic_days", 3)),
        "recheck_unknown_days": int(cfg.get("recheck_unknown_days", 7)),
        "default_top_k": int(cfg.get("default_top_k", 10)),
        "rrf_k": int(cfg.get("rrf_k", 60)),
    }


def _reset_knowledge_modules() -> None:
    for name in _KNOWLEDGE_MODULES:
        sys.modules.pop(name, None)


def _load_knowledge_store():
    if not KNOWLEDGE_STORE_SCRIPTS.exists():
        raise FileNotFoundError(
            f"Knowledge-store integration scripts not found at {KNOWLEDGE_STORE_SCRIPTS}"
        )
    config = _knowledge_store_config()
    Path(config["db_path"]).parent.mkdir(parents=True, exist_ok=True)
    os.environ["KB_DB_PATH"] = str(config["db_path"])
    sys.path.insert(0, str(KNOWLEDGE_STORE_SCRIPTS))
    try:
        _reset_knowledge_modules()
        providers_mod = importlib.import_module("providers")
        api_mod = importlib.import_module("kb_api")
        provider = None
        try:
            provider = providers_mod.resolve_provider(config)
        except Exception:
            provider = None
        return api_mod.KnowledgeStore(config=config, provider=provider), config
    finally:
        try:
            sys.path.remove(str(KNOWLEDGE_STORE_SCRIPTS))
        except ValueError:
            pass


def _content_from_args(content: str | None = None, content_file: str | None = None) -> str | None:
    if content_file:
        return Path(content_file).expanduser().read_text()
    return content


def _topics_list(topics: list[str] | None = None, topics_csv: str | None = None) -> list[str] | None:
    if topics:
        return topics
    if topics_csv:
        return [item.strip() for item in topics_csv.split(",") if item.strip()]
    return None


def knowledge_store_write(
    action: str,
    uri: str | None = None,
    title: str | None = None,
    content: str | None = None,
    content_file: str | None = None,
    topics: list[str] | None = None,
    topics_csv: str | None = None,
    source_label: str | None = None,
    source_type: str | None = None,
    metadata: str | None = None,
    mime_type: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    http_status: int | None = None,
    stability_class: str | None = None,
    should_revisit: bool | None = None,
    defer_embedding: bool = False,
) -> str:
    try:
        if action not in {"store", "upsert"}:
            return tool_error("action must be 'store' or 'upsert'", success=False)
        if not uri:
            return tool_error("uri is required", success=False)
        store, config = _load_knowledge_store()
        try:
            parsed_metadata = json.loads(metadata) if metadata else None
            text = _content_from_args(content=content, content_file=content_file)
            topic_list = _topics_list(topics=topics, topics_csv=topics_csv)
            method = store.store_record if action == "store" else store.upsert_record
            result = method(
                uri=uri,
                title=title,
                content=text,
                topics=topic_list,
                source_label=source_label,
                source_type=source_type,
                metadata=parsed_metadata,
                mime_type=mime_type,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                stability_class=stability_class,
                should_revisit=should_revisit,
                defer_embedding=defer_embedding,
            )
            return json.dumps({
                "success": True,
                "action": action,
                "db_path": config["db_path"],
                "result": _serialize(result),
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


def knowledge_store_read(
    action: str,
    uri: str | None = None,
    record_id: str | None = None,
    exclude_dynamic: bool = False,
    exclude_stale: bool = False,
    limit: int | None = None,
) -> str:
    try:
        store, config = _load_knowledge_store()
        try:
            if action == "get":
                if uri:
                    result = store.get_by_uri(uri, exclude_dynamic=exclude_dynamic, exclude_stale=exclude_stale)
                    result = _serialize(result) if result else None
                elif record_id:
                    result = store.db.get_record(record_id)
                    result = _serialize(result) if result else None
                else:
                    return tool_error("uri or record_id is required for get", success=False)
            elif action == "has":
                if not uri:
                    return tool_error("uri is required for has", success=False)
                result = {"uri": uri, "exists": store.has_uri(uri, exclude_dynamic=exclude_dynamic, exclude_stale=exclude_stale)}
            elif action == "find_uri_variants":
                if not uri:
                    return tool_error("uri is required for find_uri_variants", success=False)
                result = store.find_uri_variants(uri)
            elif action == "topics":
                result = [_serialize(t) for t in store.list_topics()]
            elif action == "recheck_candidates":
                result = [_serialize(r) for r in store.recheck_candidates(limit=limit or 50)]
            elif action == "stats":
                result = store.stats()
            else:
                return tool_error(
                    "action must be one of: get, has, find_uri_variants, topics, recheck_candidates, stats",
                    success=False,
                )
            return json.dumps({
                "success": True,
                "action": action,
                "db_path": config["db_path"],
                "result": result,
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


def knowledge_store_search(
    query: str | None = None,
    canonical_uri: str | None = None,
    search_mode: str = "hybrid",
    topics: list[str] | None = None,
    topics_csv: str | None = None,
    top_k: int | None = None,
    include_dynamic: bool = True,
    include_stale: bool = True,
    return_chunks: bool = False,
    explain: bool = False,
) -> str:
    try:
        store, config = _load_knowledge_store()
        try:
            result = store.search(
                query=query,
                canonical_uri=canonical_uri,
                search_mode=search_mode,
                topics=_topics_list(topics=topics, topics_csv=topics_csv),
                top_k=top_k,
                include_dynamic=include_dynamic,
                include_stale=include_stale,
                return_chunks=return_chunks,
                explain=explain,
            )
            return json.dumps({
                "success": True,
                "action": "search",
                "db_path": config["db_path"],
                "result": _serialize(result),
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


def knowledge_store_maintain(
    action: str,
    record_id: str | None = None,
    batch_size: int | None = None,
    model_version: str | None = None,
    content_unchanged: bool = True,
    etag: str | None = None,
    last_modified: str | None = None,
    http_status: int | None = None,
) -> str:
    try:
        store, config = _load_knowledge_store()
        try:
            if action == "mark_verified":
                if not record_id:
                    return tool_error("record_id is required for mark_verified", success=False)
                store.mark_verified(
                    record_id=record_id,
                    content_unchanged=content_unchanged,
                    etag=etag,
                    last_modified=last_modified,
                    http_status=http_status,
                )
                result = {"record_id": record_id, "status": "verified"}
            elif action == "delete":
                if not record_id:
                    return tool_error("record_id is required for delete", success=False)
                store.delete_record(record_id)
                result = {"record_id": record_id, "status": "deleted"}
            elif action == "embed_pending":
                embedded = store.embed_pending(batch_size=batch_size or 100)
                result = {"embedded": embedded}
            elif action == "reembed_record":
                if not record_id:
                    return tool_error("record_id is required for reembed_record", success=False)
                store.reembed_record(record_id)
                result = {"record_id": record_id, "status": "reembedded"}
            elif action == "reembed_all":
                store.reembed_all(model_version=model_version)
                result = {"status": "reembedded_all", "model_version": model_version}
            else:
                return tool_error(
                    "action must be one of: mark_verified, delete, embed_pending, reembed_record, reembed_all",
                    success=False,
                )
            return json.dumps({
                "success": True,
                "action": action,
                "db_path": config["db_path"],
                "result": result,
            }, ensure_ascii=False)
        finally:
            store.close()
    except Exception as e:
        return tool_error(str(e), success=False)


KNOWLEDGE_STORE_WRITE_SCHEMA = {
    "name": "knowledge_store_write",
    "description": (
        "Write or upsert records into the OpenClaw-derived knowledge store copied into Hermes. "
        f"Backed by {display_hermes_home()}/data/openclaw/knowledge-store/knowledge.db."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["store", "upsert"]},
            "uri": {"type": "string", "description": "Canonical or source URI to store."},
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Inline text content to store."},
            "content_file": {"type": "string", "description": "Optional local text file path to read content from."},
            "topics": {"type": "array", "items": {"type": "string"}, "description": "Optional topic names."},
            "topics_csv": {"type": "string", "description": "Comma-separated topics if array is inconvenient."},
            "source_label": {"type": "string"},
            "source_type": {"type": "string"},
            "metadata": {"type": "string", "description": "Optional JSON object encoded as a string."},
            "mime_type": {"type": "string"},
            "etag": {"type": "string"},
            "last_modified": {"type": "string"},
            "http_status": {"type": "integer"},
            "stability_class": {"type": "string"},
            "should_revisit": {"type": "boolean"},
            "defer_embedding": {"type": "boolean"}
        },
        "required": ["action", "uri"]
    }
}


KNOWLEDGE_STORE_READ_SCHEMA = {
    "name": "knowledge_store_read",
    "description": "Read metadata from the Hermes-hosted OpenClaw-derived knowledge store: get, has, URI variants, topics, recheck candidates, and stats.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "has", "find_uri_variants", "topics", "recheck_candidates", "stats"]},
            "uri": {"type": "string"},
            "record_id": {"type": "string"},
            "exclude_dynamic": {"type": "boolean"},
            "exclude_stale": {"type": "boolean"},
            "limit": {"type": "integer"}
        },
        "required": ["action"]
    }
}


KNOWLEDGE_STORE_SEARCH_SCHEMA = {
    "name": "knowledge_store_search",
    "description": "Search the Hermes-hosted OpenClaw-derived knowledge store using uri, keyword, vector, or hybrid search.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "canonical_uri": {"type": "string"},
            "search_mode": {"type": "string", "enum": ["uri", "keyword", "vector", "hybrid"]},
            "topics": {"type": "array", "items": {"type": "string"}},
            "topics_csv": {"type": "string"},
            "top_k": {"type": "integer"},
            "include_dynamic": {"type": "boolean"},
            "include_stale": {"type": "boolean"},
            "return_chunks": {"type": "boolean"},
            "explain": {"type": "boolean"}
        },
        "required": []
    }
}


KNOWLEDGE_STORE_MAINTAIN_SCHEMA = {
    "name": "knowledge_store_maintain",
    "description": "Maintenance operations for the Hermes-hosted OpenClaw-derived knowledge store: mark verified, embed pending chunks, reembed, or delete records.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["mark_verified", "delete", "embed_pending", "reembed_record", "reembed_all"]},
            "record_id": {"type": "string"},
            "batch_size": {"type": "integer"},
            "model_version": {"type": "string"},
            "content_unchanged": {"type": "boolean"},
            "etag": {"type": "string"},
            "last_modified": {"type": "string"},
            "http_status": {"type": "integer"}
        },
        "required": ["action"]
    }
}


registry.register(
    name="knowledge_store_write",
    toolset="knowledge_store",
    schema=KNOWLEDGE_STORE_WRITE_SCHEMA,
    handler=lambda args, **kw: knowledge_store_write(
        action=args.get("action", ""),
        uri=args.get("uri"),
        title=args.get("title"),
        content=args.get("content"),
        content_file=args.get("content_file"),
        topics=args.get("topics"),
        topics_csv=args.get("topics_csv"),
        source_label=args.get("source_label"),
        source_type=args.get("source_type"),
        metadata=args.get("metadata"),
        mime_type=args.get("mime_type"),
        etag=args.get("etag"),
        last_modified=args.get("last_modified"),
        http_status=args.get("http_status"),
        stability_class=args.get("stability_class"),
        should_revisit=args.get("should_revisit"),
        defer_embedding=args.get("defer_embedding", False),
    ),
    emoji="📚",
)

registry.register(
    name="knowledge_store_read",
    toolset="knowledge_store",
    schema=KNOWLEDGE_STORE_READ_SCHEMA,
    handler=lambda args, **kw: knowledge_store_read(
        action=args.get("action", ""),
        uri=args.get("uri"),
        record_id=args.get("record_id"),
        exclude_dynamic=args.get("exclude_dynamic", False),
        exclude_stale=args.get("exclude_stale", False),
        limit=args.get("limit"),
    ),
    emoji="📚",
)

registry.register(
    name="knowledge_store_search",
    toolset="knowledge_store",
    schema=KNOWLEDGE_STORE_SEARCH_SCHEMA,
    handler=lambda args, **kw: knowledge_store_search(
        query=args.get("query"),
        canonical_uri=args.get("canonical_uri"),
        search_mode=args.get("search_mode", "hybrid"),
        topics=args.get("topics"),
        topics_csv=args.get("topics_csv"),
        top_k=args.get("top_k"),
        include_dynamic=args.get("include_dynamic", True),
        include_stale=args.get("include_stale", True),
        return_chunks=args.get("return_chunks", False),
        explain=args.get("explain", False),
    ),
    emoji="📚",
)

registry.register(
    name="knowledge_store_maintain",
    toolset="knowledge_store",
    schema=KNOWLEDGE_STORE_MAINTAIN_SCHEMA,
    handler=lambda args, **kw: knowledge_store_maintain(
        action=args.get("action", ""),
        record_id=args.get("record_id"),
        batch_size=args.get("batch_size"),
        model_version=args.get("model_version"),
        content_unchanged=args.get("content_unchanged", True),
        etag=args.get("etag"),
        last_modified=args.get("last_modified"),
        http_status=args.get("http_status"),
    ),
    emoji="📚",
)
