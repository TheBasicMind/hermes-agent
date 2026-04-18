#!/usr/bin/env python3
"""Hermes-native tools for the all-x X/Twitter integration.

Three tools, one toolset:
  all_x_read   — Structured reads, keyword search, user/tweet/community lookups
  all_x_search — Semantic/agentic real-time X search (xAI/Grok)
  all_x_write  — Post, reply, like, follow, DM, media upload

Backed by the shared integration layer:
  agent/integrations/all_x/
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from tools.registry import registry, tool_error


# ==================== Serialization helpers ====================

def _serialize(value: Any) -> Any:
    """Recursively serialize dataclasses and lists for JSON output."""
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items() if v is not None}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _check_all_x_read() -> bool:
    """all_x_read is available if AISA_API_KEY is set (primary provider)."""
    return bool(os.getenv("AISA_API_KEY"))


def _check_all_x_search() -> bool:
    """all_x_search is available if XAI_API_KEY is set."""
    return bool(os.getenv("XAI_API_KEY"))


def _check_all_x_write() -> bool:
    """all_x_write is available if official X API user auth creds are set."""
    return bool(
        os.getenv("X_API_KEY") and
        os.getenv("X_API_SECRET") and
        os.getenv("X_ACCESS_TOKEN") and
        os.getenv("X_ACCESS_TOKEN_SECRET")
    )


# ==================== all_x_read ====================

def all_x_read(
    action: str,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    query: Optional[str] = None,
    query_type: Optional[str] = None,
    tweet_id: Optional[str] = None,
    tweet_ids: Optional[str] = None,
    source_username: Optional[str] = None,
    target_username: Optional[str] = None,
    list_id: Optional[str] = None,
    community_id: Optional[str] = None,
    woeid: Optional[int] = None,
    cursor: Optional[str] = None,
    provider: Optional[str] = None,
) -> str:
    """Handle all_x_read tool invocations."""
    try:
        from agent.integrations.all_x import (
            route_read,
            ALL_READ_ACTIONS,
        )
        from agent.integrations.all_x.mappers.aisa_to_x import (
            map_aisa_search_result,
            map_aisa_user_result,
            map_aisa_trends_result,
        )
        from agent.integrations.all_x.mappers.xai_to_x import map_xai_search_result

        if action not in ALL_READ_ACTIONS:
            return tool_error(
                f"Unknown read action '{action}'. Use one of: {', '.join(sorted(ALL_READ_ACTIONS))}",
                success=False,
            )

        kwargs = {}
        if username:
            kwargs["username"] = username
        if user_id:
            kwargs["user_id"] = user_id
        if query:
            kwargs["query"] = query
        if query_type:
            kwargs["query_type"] = query_type
        if tweet_id:
            kwargs["tweet_id"] = tweet_id
        if tweet_ids:
            kwargs["tweet_ids"] = tweet_ids
        if source_username:
            kwargs["source_username"] = source_username
        if target_username:
            kwargs["target_username"] = target_username
        if list_id:
            kwargs["list_id"] = list_id
        if community_id:
            kwargs["community_id"] = community_id
        if woeid is not None:
            kwargs["woeid"] = woeid
        if cursor:
            kwargs["cursor"] = cursor
        if provider:
            kwargs["provider_override"] = provider

        result = route_read(action, **kwargs)

        # Map the raw response into canonical objects
        used_provider = result.get("provider", "aisa")
        raw = result.get("raw", {})

        # User-oriented actions
        if action in ("user_get", "user_search"):
            canonical = map_aisa_user_result(raw, query or username or "")
        elif action == "trends":
            canonical = map_aisa_trends_result(raw, woeid or 1)
        # Tweet search results
        elif action in ("tweet_search", "user_timeline", "user_mentions",
                        "tweet_replies", "tweet_quotes", "tweet_retweeters",
                        "tweet_thread", "community_tweets"):
            canonical = map_aisa_search_result(raw, query or tweet_id or username or "")
        # Single tweet / article
        elif action in ("tweet_get", "article_get"):
            from agent.integrations.all_x.mappers.aisa_to_x import map_aisa_tweet
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            if isinstance(data, list) and data:
                tweets = [map_aisa_tweet(t) for t in data if isinstance(t, dict)]
                canonical = {
                    "provider": used_provider,
                    "action": action,
                    "tweets": [_serialize(t) for t in tweets],
                    "result_count": len(tweets),
                }
            elif isinstance(data, dict):
                tweet = map_aisa_tweet(data)
                canonical = {
                    "provider": used_provider,
                    "action": action,
                    "tweet": _serialize(tweet),
                }
            else:
                canonical = {"provider": used_provider, "action": action, "data": raw}
        # Follower / relationship / list / community / space results
        else:
            # For actions like followers, following, verified_followers, follow_relationship,
            # list_members, list_followers, community_info, community_members,
            # community_moderators, space_detail — normalize best we can
            if action in ("followers", "following", "verified_followers",
                          "list_members", "list_followers",
                          "community_members", "community_moderators"):
                canonical = map_aisa_user_result(raw, query or username or user_id or "")
            elif action in ("follow_relationship", "community_info", "space_detail"):
                # Pass through with light normalization
                canonical = {"provider": used_provider, "action": action, "data": raw.get("data", raw)}
            else:
                canonical = {"provider": used_provider, "action": action, "data": raw}

        return json.dumps({
            "success": True,
            "provider": used_provider,
            "action": action,
            "result": _serialize(canonical),
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return tool_error(str(e), success=False)


# ==================== all_x_search ====================

def all_x_search(
    query: str,
    handles_allow: Optional[List[str]] = None,
    handles_exclude: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    include_images: bool = False,
    include_video: bool = False,
    summarize: bool = True,
    model: Optional[str] = None,
) -> str:
    """Handle all_x_search tool invocations (semantic search via xAI)."""
    try:
        from agent.integrations.all_x import route_search

        result = route_search(
            query=query,
            handles_allow=handles_allow,
            handles_exclude=handles_exclude,
            from_date=from_date,
            to_date=to_date,
            include_images=include_images,
            include_video=include_video,
            model=model,
        )

        return json.dumps({
            "success": True,
            "result": _serialize(result),
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return tool_error(str(e), success=False)


# ==================== all_x_write ====================

def all_x_write(
    action: str,
    text: Optional[str] = None,
    tweet_id: Optional[str] = None,
    username: Optional[str] = None,
    media_ids: Optional[List[str]] = None,
    media_path: Optional[str] = None,
    dm_text: Optional[str] = None,
    dm_recipient_id: Optional[str] = None,
    max_results: Optional[int] = None,
) -> str:
    """Handle all_x_write tool invocations."""
    try:
        from agent.integrations.all_x import route_write, ALL_WRITE_ACTIONS

        if action not in ALL_WRITE_ACTIONS:
            return tool_error(
                f"Unknown write action '{action}'. Use one of: {', '.join(sorted(ALL_WRITE_ACTIONS))}",
                success=False,
            )

        kwargs = {}
        if text is not None:
            kwargs["text"] = text
        if tweet_id is not None:
            kwargs["tweet_id"] = tweet_id
        if username is not None:
            kwargs["username"] = username
        if media_ids is not None:
            kwargs["media_ids"] = media_ids
        if media_path is not None:
            kwargs["media_path"] = media_path
        if dm_text is not None:
            kwargs["dm_text"] = dm_text
        if dm_recipient_id is not None:
            kwargs["dm_recipient_id"] = dm_recipient_id
        if max_results is not None:
            kwargs["max_results"] = max_results

        result = route_write(action, **kwargs)

        return json.dumps({
            "success": result.success,
            "provider": result.provider,
            "action": result.action,
            "tweet": _serialize(result.tweet) if result.tweet else None,
            "errors": result.errors,
            "rate_limit": _serialize(result.rate_limit) if result.rate_limit else None,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return tool_error(str(e), success=False)


# ==================== Tool Schemas ====================

ALL_X_READ_SCHEMA = {
    "name": "all_x_read",
    "description": (
        "Read data from X/Twitter — user info, timelines, tweets, search, trends, "
        "lists, communities, spaces. Routes to AISA (primary, cheap/legal) with "
        "optional fallback to official X API. Supports 22 read actions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "user_get", "user_search", "user_timeline", "user_mentions",
                    "followers", "following", "verified_followers", "follow_relationship",
                    "tweet_get", "tweet_search", "tweet_replies", "tweet_quotes",
                    "tweet_retweeters", "tweet_thread",
                    "article_get", "trends", "list_members", "list_followers",
                    "community_info", "community_members", "community_moderators",
                    "community_tweets", "space_detail",
                ],
                "description": "Read operation to perform.",
            },
            "username": {"type": "string", "description": "Target username (for user_* actions)."},
            "user_id": {"type": "string", "description": "Target user ID (for verified_followers)."},
            "query": {"type": "string", "description": "Search query (for user_search, tweet_search)."},
            "query_type": {"type": "string", "enum": ["Latest", "Top"], "description": "Search type for tweet_search (default: Latest)."},
            "tweet_id": {"type": "string", "description": "Target tweet ID (for tweet_get, tweet_replies, etc)."},
            "tweet_ids": {"type": "string", "description": "Comma-separated tweet IDs for batch tweet_get."},
            "source_username": {"type": "string", "description": "Source username for follow_relationship."},
            "target_username": {"type": "string", "description": "Target username for follow_relationship."},
            "list_id": {"type": "string", "description": "List ID (for list_members, list_followers)."},
            "community_id": {"type": "string", "description": "Community ID (for community_* actions)."},
            "woeid": {"type": "integer", "description": "WOEID for trends (default: 1 = worldwide)."},
            "cursor": {"type": "string", "description": "Pagination cursor from a previous response."},
            "provider": {"type": "string", "enum": ["aisa", "x_official"], "description": "Override provider (default: auto-route)."},
        },
        "required": ["action"],
    },
}

ALL_X_SEARCH_SCHEMA = {
    "name": "all_x_search",
    "description": (
        "Semantic/agentic real-time X search powered by xAI Grok. "
        "Use for natural-language queries about what's happening on X. "
        "Returns text summaries with citation URLs. This is the only "
        "semantic search provider — do NOT fall back to keyword mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language search query (required)."},
            "handles_allow": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict results to these handles (max 10).",
            },
            "handles_exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude these handles (max 10).",
            },
            "from_date": {"type": "string", "description": "Start date YYYY-MM-DD."},
            "to_date": {"type": "string", "description": "End date YYYY-MM-DD."},
            "include_images": {"type": "boolean", "description": "Enable image understanding (default: false)."},
            "include_video": {"type": "boolean", "description": "Enable video understanding (default: false)."},
            "summarize": {"type": "boolean", "description": "Request summary mode (default: true)."},
            "model": {"type": "string", "description": "xAI model override (default: grok-4.20-reasoning)."},
        },
        "required": ["query"],
    },
}

ALL_X_WRITE_SCHEMA = {
    "name": "all_x_write",
    "description": (
        "Write operations on X/Twitter — post, reply, quote, delete, like, repost, "
        "bookmark, follow, block, mute, DM, media upload. Uses official X API v2 "
        "(requires OAuth 1.0a User Context credentials)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "post", "reply", "quote", "delete",
                    "like", "unlike", "repost", "unrepost",
                    "bookmark", "unbookmark",
                    "follow", "unfollow", "block", "unblock", "mute", "unmute",
                    "media_upload", "media_status", "dm_send", "dm_list",
                ],
                "description": "Write operation to perform.",
            },
            "text": {"type": "string", "description": "Tweet text (for post, reply, quote)."},
            "tweet_id": {"type": "string", "description": "Target tweet ID (for reply, quote, like, etc)."},
            "username": {"type": "string", "description": "Target username (for follow, block, mute)."},
            "media_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Uploaded media keys to attach.",
            },
            "media_path": {"type": "string", "description": "Local file path for media_upload."},
            "dm_text": {"type": "string", "description": "DM text (for dm_send)."},
            "dm_recipient_id": {"type": "string", "description": "Recipient user ID (for dm_send)."},
            "max_results": {"type": "integer", "description": "Max results for dm_list (default: 50)."},
        },
        "required": ["action"],
    },
}


# ==================== Registry ====================

registry.register(
    name="all_x_read",
    toolset="all_x",
    schema=ALL_X_READ_SCHEMA,
    handler=lambda args, **kw: all_x_read(
        action=args.get("action", ""),
        username=args.get("username"),
        user_id=args.get("user_id"),
        query=args.get("query"),
        query_type=args.get("query_type"),
        tweet_id=args.get("tweet_id"),
        tweet_ids=args.get("tweet_ids"),
        source_username=args.get("source_username"),
        target_username=args.get("target_username"),
        list_id=args.get("list_id"),
        community_id=args.get("community_id"),
        woeid=args.get("woeid"),
        cursor=args.get("cursor"),
        provider=args.get("provider"),
    ),
    check_fn=_check_all_x_read,
    requires_env=["AISA_API_KEY"],
    emoji="𝕏",
)

registry.register(
    name="all_x_search",
    toolset="all_x",
    schema=ALL_X_SEARCH_SCHEMA,
    handler=lambda args, **kw: all_x_search(
        query=args.get("query", ""),
        handles_allow=args.get("handles_allow"),
        handles_exclude=args.get("handles_exclude"),
        from_date=args.get("from_date"),
        to_date=args.get("to_date"),
        include_images=args.get("include_images", False),
        include_video=args.get("include_video", False),
        summarize=args.get("summarize", True),
        model=args.get("model"),
    ),
    check_fn=_check_all_x_search,
    requires_env=["XAI_API_KEY"],
    emoji="𝕏",
)

registry.register(
    name="all_x_write",
    toolset="all_x",
    schema=ALL_X_WRITE_SCHEMA,
    handler=lambda args, **kw: all_x_write(
        action=args.get("action", ""),
        text=args.get("text"),
        tweet_id=args.get("tweet_id"),
        username=args.get("username"),
        media_ids=args.get("media_ids"),
        media_path=args.get("media_path"),
        dm_text=args.get("dm_text"),
        dm_recipient_id=args.get("dm_recipient_id"),
        max_results=args.get("max_results"),
    ),
    check_fn=_check_all_x_write,
    requires_env=["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    emoji="𝕏",
)
