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


def _extract_reply_parent_id(tweet: Dict[str, Any]) -> Optional[str]:
    """Return the parent tweet ID when this tweet is a reply, if present."""
    refs = tweet.get("referenced_tweets")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and ref.get("type") == "replied_to" and ref.get("id"):
                return str(ref["id"])
    return None


def _tweet_is_empty(tweet: Dict[str, Any]) -> bool:
    """Heuristic for empty/placeholder tweet payloads."""
    return not bool(tweet.get("id") or tweet.get("text"))


def _map_single_tweet_payload(action: str, provider: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map a single-tweet provider payload to canonical dict shape."""
    from agent.integrations.all_x.mappers.aisa_to_x import map_aisa_tweet

    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if isinstance(data, dict):
        tweet = map_aisa_tweet(data)
        return {
            "provider": provider,
            "action": action,
            "tweet": _serialize(tweet),
        }
    return {"provider": provider, "action": action, "data": raw}


def _canonicalize_tweet_response(provider: str, action: str, raw: Dict[str, Any]):
    """Normalize a tweet_get / article_get raw provider response.

    AISA tweet_detail returns {"code":0, "tweets":[...]}. Official X returns
    {"data": {<tweet>}}. We unwrap either shape and return:
        (canonical_dict, is_empty)
    """
    from agent.integrations.all_x.mappers.aisa_to_x import map_aisa_tweet

    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw

    # Unwrap outer wrapper like {"code":0, "tweets":[...]}
    if isinstance(data, dict) and not data.get("id") and not data.get("id_str") and not data.get("text"):
        for tweets_key in ("tweets", "tweet_list"):
            candidate = data.get(tweets_key)
            if isinstance(candidate, list):
                data = candidate
                break

    if isinstance(data, list):
        tweets = [_serialize(map_aisa_tweet(t)) for t in data if isinstance(t, dict)]
        canonical = {
            "provider": provider,
            "action": action,
            "tweets": tweets,
            "result_count": len(tweets),
        }
        is_empty = not tweets or all(_tweet_is_empty(t) for t in tweets)
        return canonical, is_empty

    if isinstance(data, dict):
        tweet_dict = _serialize(map_aisa_tweet(data))
        canonical = {
            "provider": provider,
            "action": action,
            "tweet": tweet_dict,
        }
        return canonical, _tweet_is_empty(tweet_dict)

    # Unknown shape — treat as empty so the caller can decide to fall back / fail.
    return {"provider": provider, "action": action, "data": raw}, True


def _primary_tweet(canonical: Dict[str, Any]):
    """Return the primary tweet dict from a canonical tweet_get payload, or None."""
    if not isinstance(canonical, dict):
        return None
    if isinstance(canonical.get("tweet"), dict):
        return canonical["tweet"]
    tweets = canonical.get("tweets")
    if isinstance(tweets, list) and tweets and isinstance(tweets[0], dict):
        return tweets[0]
    return None


def _build_reply_context_chain(start_tweet: Dict[str, Any], route_read_fn, max_depth: int = 20) -> Dict[str, Any]:
    """Climb reply ancestors until OP (or depth/error/loop) and return context metadata."""
    chain: List[Dict[str, Any]] = []
    visited = set()

    current = start_tweet
    depth = 0
    stop_reason = "no_parent"

    while depth < max_depth:
        parent_id = _extract_reply_parent_id(current)
        if not parent_id:
            stop_reason = "no_parent"
            break
        if parent_id in visited:
            stop_reason = "loop_detected"
            break

        visited.add(parent_id)

        # Try auto-route first
        parent_result = route_read_fn("tweet_get", tweet_id=parent_id)
        parent_provider = parent_result.get("provider", "aisa")
        parent_canonical = _map_single_tweet_payload("tweet_get", parent_provider, parent_result.get("raw", {}))
        parent_tweet = parent_canonical.get("tweet") if isinstance(parent_canonical, dict) else None

        # If auto-route returned an empty payload, try official API explicitly as a best-effort fallback
        if isinstance(parent_tweet, dict) and _tweet_is_empty(parent_tweet):
            try:
                parent_result_official = route_read_fn("tweet_get", provider_override="x_official", tweet_id=parent_id)
                parent_provider = parent_result_official.get("provider", "x_official")
                parent_canonical = _map_single_tweet_payload("tweet_get", parent_provider, parent_result_official.get("raw", {}))
                parent_tweet = parent_canonical.get("tweet") if isinstance(parent_canonical, dict) else None
            except Exception:
                pass

        if not isinstance(parent_tweet, dict) or _tweet_is_empty(parent_tweet):
            stop_reason = "parent_unavailable"
            chain.append({"id": parent_id, "unavailable": True})
            break

        chain.append(parent_tweet)
        current = parent_tweet
        depth += 1
    else:
        stop_reason = "max_depth"

    op = chain[-1] if chain else start_tweet
    return {
        "is_reply": _extract_reply_parent_id(start_tweet) is not None,
        "reply_context_depth": len(chain),
        "reply_chain": chain,
        "op_tweet": op,
        "context_stop_reason": stop_reason,
    }


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
            canonical, is_empty = _canonicalize_tweet_response(used_provider, action, raw)
            fallback_meta = None

            # Auto-fallback to official X when AISA returned no usable tweet.
            # Only fires when the caller did NOT explicitly pin to x_official already.
            if is_empty and used_provider == "aisa" and provider != "x_official":
                fb_kwargs = {k: v for k, v in kwargs.items() if k != "provider_override"}
                fallback_meta = {"attempted": True, "provider": "x_official"}
                try:
                    fb_result = route_read(action, provider_override="x_official", **fb_kwargs)
                    fb_provider = fb_result.get("provider", "x_official")
                    fb_canonical, fb_empty = _canonicalize_tweet_response(
                        fb_provider, action, fb_result.get("raw", {})
                    )
                    if not fb_empty:
                        canonical = fb_canonical
                        used_provider = fb_provider
                        is_empty = False
                        fallback_meta["succeeded"] = True
                    else:
                        fallback_meta["succeeded"] = False
                        fallback_meta["reason"] = "x_official also returned empty"
                except Exception as e:
                    fallback_meta["succeeded"] = False
                    fallback_meta["error"] = str(e)

            # Hard-fail when both providers returned nothing usable.
            # Filing flows must NOT proceed with empty tweets misreported as success.
            if is_empty:
                msg = f"{action} returned no tweet content from AISA"
                if fallback_meta:
                    err = fallback_meta.get("error") or fallback_meta.get("reason") or "unknown"
                    msg += f"; x_official fallback failed ({err})"
                else:
                    msg += "; x_official fallback skipped (provider explicitly pinned)"
                msg += f". tweet_id={tweet_id or tweet_ids or '?'}"
                return tool_error(msg, success=False)

            if fallback_meta is not None:
                canonical["fallback"] = fallback_meta
                # Preserve legacy field name for article_get callers
                if action == "article_get":
                    canonical["article_fallback"] = fallback_meta

            # Enrich tweet_get with reply ancestry context up to OP.
            if action == "tweet_get":
                target = _primary_tweet(canonical)
                if isinstance(target, dict) and not _tweet_is_empty(target):
                    canonical["context"] = _build_reply_context_chain(target, route_read_fn=route_read)
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
