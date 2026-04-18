#!/usr/bin/env python3
"""Canonical data models for the all-x integration.

All shapes follow official X API v2 field naming conventions.
Adapters map their provider-specific payloads into these canonical types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class XTweetMetrics:
    retweet_count: Optional[int] = None
    reply_count: Optional[int] = None
    like_count: Optional[int] = None
    quote_count: Optional[int] = None
    bookmark_count: Optional[int] = None
    impression_count: Optional[int] = None


@dataclass
class XUserMetrics:
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    tweet_count: Optional[int] = None
    listed_count: Optional[int] = None


@dataclass
class XReferencedTweet:
    type: str      # "replied_to" | "quoted" | "retweeted"
    id: str


@dataclass
class XTweetEntities:
    hashtags: Optional[List[Dict[str, Any]]] = None
    urls: Optional[List[Dict[str, Any]]] = None
    mentions: Optional[List[Dict[str, Any]]] = None
    cashtags: Optional[List[Dict[str, Any]]] = None


@dataclass
class XTweetAttachments:
    media_keys: Optional[List[str]] = None
    poll_ids: Optional[List[str]] = None


@dataclass
class XUser:
    id: str
    username: str
    name: str = ""
    description: Optional[str] = None
    created_at: Optional[str] = None
    verified: Optional[bool] = None
    verified_type: Optional[str] = None        # "blue" | "government" | etc
    profile_image_url: Optional[str] = None
    public_metrics: Optional[XUserMetrics] = None
    url: Optional[str] = None
    location: Optional[str] = None
    pinned_tweet_id: Optional[str] = None
    source_provider: str = ""


@dataclass
class XTweet:
    id: str
    text: str
    author: Optional[XUser] = None
    conversation_id: Optional[str] = None
    created_at: Optional[str] = None          # ISO 8601
    lang: Optional[str] = None
    possibly_sensitive: Optional[bool] = None
    in_reply_to_user_id: Optional[str] = None
    referenced_tweets: Optional[List[XReferencedTweet]] = None
    public_metrics: Optional[XTweetMetrics] = None
    entities: Optional[XTweetEntities] = None
    attachments: Optional[XTweetAttachments] = None
    edit_history_tweet_ids: Optional[List[str]] = None
    url: Optional[str] = None                 # https://x.com/{username}/status/{id}
    source_provider: str = ""                  # "aisa" | "xai" | "x_official"
    raw_provider_payload: Optional[Dict[str, Any]] = None  # debug only


@dataclass
class XCitation:
    text: str
    url: str


@dataclass
class XIncludes:
    users: Optional[List[XUser]] = None
    tweets: Optional[List[XTweet]] = None


@dataclass
class XSearchResult:
    query: str
    provider: str       # "aisa" | "xai" | "x_official"
    mode: str           # "keyword" | "semantic"
    result_count: int
    next_cursor: Optional[str] = None
    tweets: List[XTweet] = field(default_factory=list)
    includes: Optional[XIncludes] = None
    citations: Optional[List[XCitation]] = None   # xAI semantic results


@dataclass
class XRateLimit:
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_at: Optional[str] = None


@dataclass
class XWriteResult:
    provider: str
    action: str         # "post" | "reply" | "like" | etc
    success: bool
    tweet: Optional[XTweet] = None
    errors: Optional[List[str]] = None
    rate_limit: Optional[XRateLimit] = None
