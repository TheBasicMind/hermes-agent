#!/usr/bin/env python3
"""xAI-to-canonical mapper.

Normalizes xAI/Grok search response payloads into canonical dataclasses.

Mapping rules (from IMPLEMENTATION-BRIEF.md section 5.2):
- Extract individual tweet references from text/citations where possible
- Each citation URL matching x.com/*/status/* becomes a tweet stub
- Text summaries that aren't tied to specific tweets become note objects, not fake tweets
- citations remain first-class on the XSearchResult envelope
- Where official fields are missing from xAI output, keep them null
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..models import XCitation, XSearchResult, XTweet, XUser


TWEET_URL_RE = re.compile(r"x\.com/([a-zA-Z0-9_]{1,15})/status/(\d+)")


def _extract_tweet_stubs_from_citations(citations: List[Dict[str, Any]]) -> List[XTweet]:
    """Extract tweet stubs from citation URLs matching x.com/*/status/*."""
    stubs: List[XTweet] = []
    seen_ids: set = set()

    for cite in citations:
        url = cite.get("url", "")
        match = TWEET_URL_RE.search(url)
        if match:
            username = match.group(1)
            tweet_id = match.group(2)
            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)
            stubs.append(XTweet(
                id=tweet_id,
                text=cite.get("text", ""),
                author=XUser(
                    id="",
                    username=username,
                    source_provider="xai",
                ) if username else None,
                url=url,
                source_provider="xai",
            ))

    return stubs


def _extract_tweet_stubs_from_text(text: str) -> List[XTweet]:
    """Extract additional tweet stubs from URLs embedded in the text body."""
    stubs: List[XTweet] = []
    seen_ids: set = set()

    for match in TWEET_URL_RE.finditer(text):
        username = match.group(1)
        tweet_id = match.group(2)
        if tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)
        url = f"https://x.com/{username}/status/{tweet_id}"
        stubs.append(XTweet(
            id=tweet_id,
            text="",
            author=XUser(
                id="",
                username=username,
                source_provider="xai",
            ) if username else None,
            url=url,
            source_provider="xai",
        ))

    return stubs


def map_xai_search_result(raw: Dict[str, Any], query: str) -> XSearchResult:
    """Map a formatted xAI search response to canonical XSearchResult."""
    text = raw.get("text", "")
    raw_citations = raw.get("citations", [])

    # Build tweet stubs from citations and text URLs
    tweet_stubs = _extract_tweet_stubs_from_citations(raw_citations)
    text_stubs = _extract_tweet_stubs_from_text(text)

    # Merge text stubs into citation stubs (avoid duplicates)
    existing_ids = {t.id for t in tweet_stubs}
    for stub in text_stubs:
        if stub.id not in existing_ids:
            tweet_stubs.append(stub)
            existing_ids.add(stub.id)

    # Build canonical citations
    citations = [
        XCitation(text=c.get("text", ""), url=c.get("url", ""))
        for c in raw_citations
        if c.get("url")
    ]

    return XSearchResult(
        query=query,
        provider="xai",
        mode="semantic",
        result_count=len(tweet_stubs),
        tweets=tweet_stubs,
        citations=citations if citations else None,
    )
