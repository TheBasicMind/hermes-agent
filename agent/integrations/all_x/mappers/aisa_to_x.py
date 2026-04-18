#!/usr/bin/env python3
"""AISA-to-canonical mapper.

Normalizes AISA API payloads into the official-X-shaped canonical dataclasses.

Mapping rules (from IMPLEMENTATION-BRIEF.md section 5.1):
- likes -> public_metrics.like_count
- retweets/reposts -> public_metrics.retweet_count
- replies -> public_metrics.reply_count
- quotes -> public_metrics.quote_count
- views/impressions -> public_metrics.impression_count
- author_handle -> author.username
- author_name -> author.name
- tweet_id/id_str -> id
- full_text/text -> text
- URL: construct https://x.com/{username}/status/{id} if not directly provided
- bookmarks -> public_metrics.bookmark_count
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..models import (
    XIncludes,
    XSearchResult,
    XTweet,
    XTweetAttachments,
    XTweetEntities,
    XTweetMetrics,
    XUser,
    XUserMetrics,
)


def _int_or_none(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _str_or_none(val: Any) -> Optional[str]:
    if val is None:
        return None
    return str(val) if val else None


def map_aisa_user(raw: Dict[str, Any]) -> XUser:
    """Map an AISA user object to canonical XUser."""
    user_id = str(raw.get("id_str") or raw.get("user_id") or raw.get("id") or "")
    username = str(raw.get("screen_name") or raw.get("username") or raw.get("userName") or "").lstrip("@")
    name = str(raw.get("name") or raw.get("author_name") or username)

    metrics_raw = raw.get("public_metrics") or {}
    if not isinstance(metrics_raw, dict):
        metrics_raw = {}

    # AISA sometimes puts metrics at top level
    metrics = XUserMetrics(
        followers_count=_int_or_none(metrics_raw.get("followers_count") or raw.get("followers_count") or raw.get("followers")),
        following_count=_int_or_none(metrics_raw.get("following_count") or raw.get("following_count") or raw.get("followings_count")),
        tweet_count=_int_or_none(metrics_raw.get("tweet_count") or raw.get("statuses_count") or raw.get("tweet_count")),
        listed_count=_int_or_none(metrics_raw.get("listed_count") or raw.get("listed_count")),
    )

    return XUser(
        id=user_id,
        username=username,
        name=name,
        description=raw.get("description") or raw.get("bio"),
        created_at=raw.get("created_at"),
        verified=raw.get("verified"),
        verified_type=raw.get("verified_type"),
        profile_image_url=raw.get("profile_image_url") or raw.get("profile_image_url_https"),
        public_metrics=metrics,
        url=raw.get("url"),
        location=raw.get("location"),
        pinned_tweet_id=_str_or_none(raw.get("pinned_tweet_id")),
        source_provider="aisa",
    )


def map_aisa_tweet(raw: Dict[str, Any], includes_users: Optional[Dict[str, XUser]] = None) -> XTweet:
    """Map an AISA tweet object to canonical XTweet."""
    tweet_id = str(raw.get("id_str") or raw.get("tweet_id") or raw.get("id") or "")
    text = raw.get("full_text") or raw.get("text") or ""

    # Metrics — AISA uses varied field names
    metrics_raw = raw.get("public_metrics") or {}
    if not isinstance(metrics_raw, dict):
        metrics_raw = {}

    metrics = XTweetMetrics(
        retweet_count=_int_or_none(metrics_raw.get("retweet_count") or raw.get("retweet_count") or raw.get("retweets") or raw.get("reposts")),
        reply_count=_int_or_none(metrics_raw.get("reply_count") or raw.get("reply_count") or raw.get("replies")),
        like_count=_int_or_none(metrics_raw.get("like_count") or raw.get("like_count") or raw.get("likes") or raw.get("favorite_count")),
        quote_count=_int_or_none(metrics_raw.get("quote_count") or raw.get("quote_count") or raw.get("quotes")),
        bookmark_count=_int_or_none(metrics_raw.get("bookmark_count") or raw.get("bookmark_count") or raw.get("bookmarks")),
        impression_count=_int_or_none(metrics_raw.get("impression_count") or raw.get("impression_count") or raw.get("views") or raw.get("view_count")),
    )

    # Author resolution — inline or from includes
    author = None
    author_data = raw.get("author") or raw.get("user") or raw.get("tweet_result")
    if isinstance(author_data, dict):
        author = map_aisa_user(author_data)
    elif includes_users:
        author_id = str(raw.get("author_id") or raw.get("user_id_str") or "")
        if author_id in includes_users:
            author = includes_users[author_id]
        else:
            # Try matching by handle
            author_handle = str(raw.get("author_handle") or raw.get("screen_name") or "").lstrip("@")
            for u in includes_users.values():
                if u.username.lower() == author_handle.lower():
                    author = u
                    break

    if author is None:
        # Build a minimal author from inline fields
        author_handle = str(raw.get("author_handle") or raw.get("screen_name") or "").lstrip("@")
        author_name = str(raw.get("author_name") or raw.get("name") or author_handle)
        author_id = str(raw.get("author_id") or raw.get("user_id_str") or "")
        if author_handle or author_id:
            author = XUser(
                id=author_id,
                username=author_handle,
                name=author_name,
                source_provider="aisa",
            )

    # Construct URL
    url = raw.get("url")
    if not url and author and author.username and tweet_id:
        url = f"https://x.com/{author.username}/status/{tweet_id}"

    # Referenced tweets
    ref_tweets = None
    ref_raw = raw.get("referenced_tweets")
    if isinstance(ref_raw, list):
        from ..models import XReferencedTweet
        ref_tweets = [
            XReferencedTweet(type=r.get("type", ""), id=str(r.get("id", "")))
            for r in ref_raw if isinstance(r, dict) and r.get("id")
        ]

    # Entities
    entities = None
    ent_raw = raw.get("entities")
    if isinstance(ent_raw, dict):
        entities = XTweetEntities(
            hashtags=ent_raw.get("hashtags"),
            urls=ent_raw.get("urls"),
            mentions=ent_raw.get("mentions"),
            cashtags=ent_raw.get("cashtags"),
        )

    # Attachments
    attachments = None
    att_raw = raw.get("attachments")
    if isinstance(att_raw, dict):
        attachments = XTweetAttachments(
            media_keys=att_raw.get("media_keys"),
            poll_ids=att_raw.get("poll_ids"),
        )

    return XTweet(
        id=tweet_id,
        text=text,
        author=author,
        conversation_id=_str_or_none(raw.get("conversation_id") or raw.get("conversation_id_str")),
        created_at=raw.get("created_at"),
        lang=raw.get("lang"),
        possibly_sensitive=raw.get("possibly_sensitive"),
        in_reply_to_user_id=_str_or_none(raw.get("in_reply_to_user_id")),
        referenced_tweets=ref_tweets,
        public_metrics=metrics,
        entities=entities,
        attachments=attachments,
        edit_history_tweet_ids=raw.get("edit_history_tweet_ids"),
        url=url,
        source_provider="aisa",
    )


def map_aisa_search_result(raw: Dict[str, Any], query: str) -> XSearchResult:
    """Map an AISA search/timeline response to XSearchResult."""
    # AISA responses vary: some have "data.tweets", some have "data", some have "tweets"
    tweets_raw: List[Dict[str, Any]] = []

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    if not data:
        data = raw

    # Try multiple paths where tweets might live
    for key in ("tweets", "tweet_list", "timeline"):
        candidate = data.get(key)
        if isinstance(candidate, list):
            tweets_raw = candidate
            break

    # If data itself is a list of tweets
    if not tweets_raw and isinstance(raw.get("data"), list):
        tweets_raw = raw["data"]

    # Some endpoints return data as a single tweet or user, not a list
    if not tweets_raw and "text" in data:
        tweets_raw = [data]

    # Build includes users dict
    includes_users: Dict[str, XUser] = {}
    includes_raw = raw.get("includes") or data.get("includes")
    if isinstance(includes_raw, dict):
        for u in (includes_raw.get("users") or []):
            if isinstance(u, dict):
                user = map_aisa_user(u)
                includes_users[user.id] = user

    # Also check for top-level user objects in the response
    for key in ("users", "user_list"):
        candidate = data.get(key)
        if isinstance(candidate, list):
            for u in candidate:
                if isinstance(u, dict):
                    user = map_aisa_user(u)
                    includes_users[user.id] = user

    tweets = [map_aisa_tweet(t, includes_users=includes_users) for t in tweets_raw if isinstance(t, dict)]

    # Cursor/next page
    next_cursor = None
    for cursor_key in ("next_cursor", "cursor", "next_cursor_str", "pagination_token"):
        cursor_val = data.get(cursor_key) or raw.get(cursor_key)
        if cursor_val:
            next_cursor = str(cursor_val)
            break

    includes = None
    if includes_users:
        includes = XIncludes(users=list(includes_users.values()))

    return XSearchResult(
        query=query,
        provider="aisa",
        mode="keyword",
        result_count=len(tweets),
        next_cursor=next_cursor,
        tweets=tweets,
        includes=includes,
    )


def map_aisa_user_result(raw: Dict[str, Any], query: str) -> XSearchResult:
    """Map an AISA user search/info response (no tweets, just users)."""
    users_raw: List[Dict[str, Any]] = []
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if isinstance(data, dict):
        for key in ("users", "user_list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                users_raw = candidate
                break
        # Single user response
        if not users_raw and ("id" in data or "screen_name" in data or "username" in data):
            users_raw = [data]
    elif isinstance(data, list):
        users_raw = data

    users = [map_aisa_user(u) for u in users_raw if isinstance(u, dict)]
    includes = XIncludes(users=users) if users else None

    return XSearchResult(
        query=query,
        provider="aisa",
        mode="keyword",
        result_count=len(users),
        tweets=[],
        includes=includes,
    )


def map_aisa_trends_result(raw: Dict[str, Any], woeid: int) -> Dict[str, Any]:
    """Map AISA trends response — trends have a unique shape, pass through with normalization."""
    data = raw.get("data") if isinstance(raw.get("data"), (dict, list)) else raw
    if isinstance(data, dict):
        trends = data.get("trends") or data.get("data") or []
    elif isinstance(data, list):
        trends = data
    else:
        trends = []
    return {
        "woeid": woeid,
        "trends": trends,
        "provider": "aisa",
    }
