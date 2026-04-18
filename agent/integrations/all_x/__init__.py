#!/usr/bin/env python3
"""all-x integration package.

Provides unified X/Twitter access via three provider adapters:
- AISA (cheap/legal reads)
- xAI/Grok (semantic search)
- Official X API (writes + strict fallback)

Canonical schema matches official X API v2 field naming.

Shared integration entry points (used by last30days):
    from agent.integrations.all_x import search_keyword, search_semantic
"""

from .models import (
    XCitation,
    XIncludes,
    XRateLimit,
    XReferencedTweet,
    XSearchResult,
    XTweet,
    XTweetAttachments,
    XTweetEntities,
    XTweetMetrics,
    XUser,
    XUserMetrics,
    XWriteResult,
)
from .routing import (
    ALL_READ_ACTIONS,
    ALL_WRITE_ACTIONS,
    get_available_providers,
    route_read,
    route_search,
    route_write,
    search_keyword,
    search_semantic,
)
from .errors import (
    XActionNotSupportedError,
    XApiError,
    XAuthError,
    XError,
    XProviderUnavailableError,
    XRateLimitError,
)

__all__ = [
    # Models
    "XCitation",
    "XIncludes",
    "XRateLimit",
    "XReferencedTweet",
    "XSearchResult",
    "XTweet",
    "XTweetAttachments",
    "XTweetEntities",
    "XTweetMetrics",
    "XUser",
    "XUserMetrics",
    "XWriteResult",
    # Routing
    "ALL_READ_ACTIONS",
    "ALL_WRITE_ACTIONS",
    "get_available_providers",
    "route_read",
    "route_search",
    "route_write",
    "search_keyword",
    "search_semantic",
    # Errors
    "XActionNotSupportedError",
    "XApiError",
    "XAuthError",
    "XError",
    "XProviderUnavailableError",
    "XRateLimitError",
]
