#!/usr/bin/env python3
"""Provider routing logic for the all-x integration.

Routes actions to the appropriate provider adapter based on the
routing table defined in IMPLEMENTATION-BRIEF.md sections 4.1-4.3.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .adapters.aisa import AisaAdapter
from .adapters.xai import XaiAdapter
from .adapters.x_official import XOfficialAdapter
from .errors import (
    XActionNotSupportedError,
    XAuthError,
    XProviderUnavailableError,
)
from .mappers.aisa_to_x import (
    map_aisa_search_result,
    map_aisa_trends_result,
    map_aisa_tweet,
    map_aisa_user,
    map_aisa_user_result,
)
from .mappers.xai_to_x import map_xai_search_result
from .models import XSearchResult, XTweet, XWriteResult


# Actions where AISA is primary, official X is fallback
_AISA_PRIMARY_READS = {
    "user_get", "user_search", "user_timeline", "user_mentions",
    "followers", "following", "verified_followers", "follow_relationship",
    "tweet_get", "tweet_search", "tweet_replies", "tweet_quotes",
    "tweet_retweeters", "tweet_thread",
}

# AISA-only reads (no official X fallback)
_AISA_ONLY_READS = {
    "article_get", "trends", "list_members", "list_followers",
    "community_info", "community_members", "community_moderators",
    "community_tweets", "space_detail",
}

ALL_READ_ACTIONS = _AISA_PRIMARY_READS | _AISA_ONLY_READS

ALL_WRITE_ACTIONS = {
    "post", "reply", "quote", "delete",
    "like", "unlike", "repost", "unrepost",
    "bookmark", "unbookmark",
    "follow", "unfollow", "block", "unblock", "mute", "unmute",
    "media_upload", "media_status", "dm_send", "dm_list",
}


def _check_aisa() -> bool:
    """Check if AISA is available (AISA_API_KEY set)."""
    import os
    return bool(os.environ.get("AISA_API_KEY"))


def _check_xai() -> bool:
    """Check if xAI is available (XAI_API_KEY set)."""
    import os
    return bool(os.environ.get("XAI_API_KEY"))


def _check_x_official() -> bool:
    """Check if official X API is available (user auth creds set)."""
    import os
    return bool(
        os.environ.get("X_API_KEY") and
        os.environ.get("X_API_SECRET") and
        os.environ.get("X_ACCESS_TOKEN") and
        os.environ.get("X_ACCESS_TOKEN_SECRET")
    )


def get_available_providers() -> Dict[str, bool]:
    """Return dict of provider name -> availability."""
    return {
        "aisa": _check_aisa(),
        "xai": _check_xai(),
        "x_official": _check_x_official(),
    }


def route_read(
    action: str,
    provider_override: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Route a read action to the appropriate provider.

    Returns a dict with the raw provider response and metadata
    for the tool layer to construct canonical output.
    """
    # Validate action
    if action not in ALL_READ_ACTIONS:
        raise XActionNotSupportedError(
            f"Unknown read action '{action}'. Supported: {sorted(ALL_READ_ACTIONS)}",
            provider="routing",
            action=action,
        )

    # Provider selection
    provider = provider_override
    if provider and provider not in ("aisa", "x_official"):
        raise XActionNotSupportedError(
            f"Read provider must be 'aisa' or 'x_official', got '{provider}'",
            provider=provider,
            action=action,
        )

    # Auto-route: AISA primary, official X fallback
    if not provider:
        if action in _AISA_ONLY_READS:
            provider = "aisa"
        elif action in _AISA_PRIMARY_READS:
            provider = "aisa" if _check_aisa() else "x_official"
        else:
            provider = "aisa"

    # Execute with primary provider
    try:
        if provider == "aisa":
            return _execute_aisa_read(action, **kwargs)
        elif provider == "x_official":
            return _execute_x_official_read(action, **kwargs)
    except (XProviderUnavailableError, XAuthError) as e:
        # Try fallback for AISA-primary reads
        if provider == "aisa" and action in _AISA_PRIMARY_READS and _check_x_official():
            try:
                return _execute_x_official_read(action, **kwargs)
            except Exception:
                pass  # Fall through to re-raise original error
        raise

    raise XActionNotSupportedError(
        f"Cannot route read action '{action}'",
        provider=provider or "none",
        action=action,
    )


def route_search(
    query: str,
    **kwargs,
) -> XSearchResult:
    """Route a semantic search to xAI (only provider for this mode)."""
    if not _check_xai():
        raise XProviderUnavailableError(
            "xAI is the only provider for semantic X search, but XAI_API_KEY is not set. "
            "Do NOT fall back to keyword mode — set XAI_API_KEY in ~/.hermes/.env",
            provider="xai",
            action="semantic_search",
        )

    adapter = XaiAdapter()
    raw = adapter.search(query=query, **kwargs)
    return map_xai_search_result(raw, query)


def route_write(
    action: str,
    **kwargs,
) -> XWriteResult:
    """Route a write action to the official X API (only provider for writes)."""
    if action not in ALL_WRITE_ACTIONS:
        raise XActionNotSupportedError(
            f"Unknown write action '{action}'. Supported: {sorted(ALL_WRITE_ACTIONS)}",
            provider="x_official",
            action=action,
        )

    if not _check_x_official():
        raise XAuthError(
            "Official X API credentials are required for write operations. "
            "Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET in ~/.hermes/.env",
            provider="x_official",
            action=action,
        )

    adapter = XOfficialAdapter()
    result = _execute_x_official_write(adapter, action, **kwargs)
    return result


def _execute_aisa_read(action: str, **kwargs) -> Dict[str, Any]:
    """Execute a read via AISA and return mapped result."""
    adapter = AisaAdapter()

    # Dispatch to the correct AISA method
    action_map = {
        "user_get": lambda: adapter.user_info(kwargs.get("username", "")),
        "user_search": lambda: adapter.user_search(kwargs.get("query", ""), cursor=kwargs.get("cursor")),
        "user_timeline": lambda: adapter.user_tweets(kwargs.get("username", ""), cursor=kwargs.get("cursor")),
        "user_mentions": lambda: adapter.user_mentions(kwargs.get("username", ""), cursor=kwargs.get("cursor")),
        "followers": lambda: adapter.followers(kwargs.get("username", ""), cursor=kwargs.get("cursor")),
        "following": lambda: adapter.followings(kwargs.get("username", ""), cursor=kwargs.get("cursor")),
        "verified_followers": lambda: adapter.verified_followers(kwargs.get("user_id", ""), cursor=kwargs.get("cursor")),
        "follow_relationship": lambda: adapter.check_follow_relationship(
            kwargs.get("source_username", ""), kwargs.get("target_username", "")),
        "tweet_get": lambda: adapter.tweet_detail(kwargs.get("tweet_ids") or kwargs.get("tweet_id", "")),
        "tweet_search": lambda: adapter.tweet_search(
            kwargs.get("query", ""), query_type=kwargs.get("query_type", "Latest"), cursor=kwargs.get("cursor")),
        "tweet_replies": lambda: adapter.tweet_replies(kwargs.get("tweet_id", ""), cursor=kwargs.get("cursor")),
        "tweet_quotes": lambda: adapter.tweet_quotes(kwargs.get("tweet_id", ""), cursor=kwargs.get("cursor")),
        "tweet_retweeters": lambda: adapter.tweet_retweeters(kwargs.get("tweet_id", ""), cursor=kwargs.get("cursor")),
        "tweet_thread": lambda: adapter.tweet_thread(kwargs.get("tweet_id", ""), cursor=kwargs.get("cursor")),
        "article_get": lambda: adapter.article(kwargs.get("tweet_id", "")),
        "trends": lambda: adapter.trends(woeid=kwargs.get("woeid", 1)),
        "list_members": lambda: adapter.list_members(kwargs.get("list_id", ""), cursor=kwargs.get("cursor")),
        "list_followers": lambda: adapter.list_followers(kwargs.get("list_id", ""), cursor=kwargs.get("cursor")),
        "community_info": lambda: adapter.community_info(kwargs.get("community_id", "")),
        "community_members": lambda: adapter.community_members(kwargs.get("community_id", ""), cursor=kwargs.get("cursor")),
        "community_moderators": lambda: adapter.community_moderators(kwargs.get("community_id", ""), cursor=kwargs.get("cursor")),
        "community_tweets": lambda: adapter.community_tweets(kwargs.get("community_id", ""), cursor=kwargs.get("cursor")),
        "space_detail": lambda: adapter.space_detail(kwargs.get("space_id") or kwargs.get("community_id", "")),
    }

    handler = action_map.get(action)
    if not handler:
        raise XActionNotSupportedError(
            f"AISA does not support read action '{action}'",
            provider="aisa",
            action=action,
        )

    raw = handler()
    return {"provider": "aisa", "action": action, "raw": raw}


def _execute_x_official_read(action: str, **kwargs) -> Dict[str, Any]:
    """Execute a read via official X API (fallback) and return mapped result."""
    adapter = XOfficialAdapter()

    action_map = {
        "user_get": lambda: adapter.user_get(kwargs.get("username", "")),
        "tweet_get": lambda: adapter.tweet_get(kwargs.get("tweet_id", "")),
    }

    handler = action_map.get(action)
    if not handler:
        raise XActionNotSupportedError(
            f"Official X API fallback does not support read action '{action}'",
            provider="x_official",
            action=action,
        )

    raw = handler()
    return {"provider": "x_official", "action": action, "raw": raw}


def _execute_x_official_write(adapter: XOfficialAdapter, action: str, **kwargs) -> XWriteResult:
    """Execute a write via official X API and return XWriteResult."""
    action_map = {
        "post": lambda: adapter.post(kwargs.get("text", ""), media_ids=kwargs.get("media_ids")),
        "reply": lambda: adapter.reply(kwargs.get("text", ""), kwargs.get("tweet_id", ""), media_ids=kwargs.get("media_ids")),
        "quote": lambda: adapter.quote(kwargs.get("text", ""), kwargs.get("tweet_id", ""), media_ids=kwargs.get("media_ids")),
        "delete": lambda: adapter.delete(kwargs.get("tweet_id", "")),
        "like": lambda: adapter.like(kwargs.get("tweet_id", "")),
        "unlike": lambda: adapter.unlike(kwargs.get("tweet_id", "")),
        "repost": lambda: adapter.repost(kwargs.get("tweet_id", "")),
        "unrepost": lambda: adapter.unrepost(kwargs.get("tweet_id", "")),
        "bookmark": lambda: adapter.bookmark(kwargs.get("tweet_id", "")),
        "unbookmark": lambda: adapter.unbookmark(kwargs.get("tweet_id", "")),
        "follow": lambda: adapter.follow(kwargs.get("username", "")),
        "unfollow": lambda: adapter.unfollow(kwargs.get("username", "")),
        "block": lambda: adapter.block(kwargs.get("username", "")),
        "unblock": lambda: adapter.unblock(kwargs.get("username", "")),
        "mute": lambda: adapter.mute(kwargs.get("username", "")),
        "unmute": lambda: adapter.unmute(kwargs.get("username", "")),
        "media_upload": lambda: adapter.media_upload(kwargs.get("media_path", "")),
        "dm_send": lambda: adapter.dm_send(kwargs.get("dm_recipient_id", ""), kwargs.get("dm_text", "")),
        "dm_list": lambda: adapter.dm_list(max_results=kwargs.get("max_results", 50)),
    }

    handler = action_map.get(action)
    if not handler:
        return XWriteResult(
            provider="x_official",
            action=action,
            success=False,
            errors=[f"Unsupported write action: {action}"],
        )

    try:
        result = handler()
        success = result.get("success", False)
        errors = result.get("errors")
        tweet_data = result.get("data")

        tweet = None
        if isinstance(tweet_data, dict) and "id" in tweet_data:
            tweet = XTweet(
                id=str(tweet_data.get("id", "")),
                text=tweet_data.get("text", ""),
                source_provider="x_official",
            )

        return XWriteResult(
            provider="x_official",
            action=action,
            success=success,
            tweet=tweet,
            errors=errors,
        )
    except Exception as e:
        return XWriteResult(
            provider="x_official",
            action=action,
            success=False,
            errors=[str(e)],
        )


# ==================== Shared Integration API ====================
# last30days imports these — it does NOT call the chat-facing tools.

def search_keyword(query: str, query_type: str = "Latest", cursor: Optional[str] = None) -> XSearchResult:
    """Keyword search via AISA. Shared integration entry point for last30days."""
    result = route_read("tweet_search", query=query, query_type=query_type, cursor=cursor)
    raw = result.get("raw", {})
    return map_aisa_search_result(raw, query)


def search_semantic(
    query: str,
    handles_allow: Optional[list] = None,
    handles_exclude: Optional[list] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    include_images: bool = False,
    include_video: bool = False,
    model: Optional[str] = None,
) -> XSearchResult:
    """Semantic search via xAI. Shared integration entry point for last30days."""
    return route_search(
        query=query,
        handles_allow=handles_allow,
        handles_exclude=handles_exclude,
        from_date=from_date,
        to_date=to_date,
        include_images=include_images,
        include_video=include_video,
        model=model,
    )
