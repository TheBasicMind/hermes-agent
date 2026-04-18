#!/usr/bin/env python3
"""AISA adapter — HTTP client for the AISA X/Twitter read API.

Source reference: OpenClaw twitter_client.py TwitterClient class.
BASE_URL: https://api.aisa.one/apis/v1
Auth: Bearer token via AISA_API_KEY env var.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..errors import XApiError, XAuthError, XProviderUnavailableError


class AisaAdapter:
    """Read-only AISA API adapter for X/Twitter data."""

    BASE_URL = "https://api.aisa.one/apis/v1"
    TIMEOUT = 60

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("AISA_API_KEY")
        if not self.api_key:
            raise XAuthError(
                "AISA_API_KEY is required. Set it in ~/.hermes/.env",
                provider="aisa",
            )

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an HTTP request to the AISA API."""
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            query_string = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            url = f"{url}?{query_string}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Hermes-all-x/1.0",
        }

        request_data = None
        if method == "POST":
            body = data.copy() if data else {}
            body.setdefault("aisa_api_key", self.api_key)
            request_data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=request_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                parsed = json.loads(error_body)
            except json.JSONDecodeError:
                parsed = {"error": {"code": str(e.code), "message": error_body}}
            if e.code == 401 or e.code == 403:
                raise XAuthError(
                    f"AISA auth failed ({e.code}): {error_body[:200]}",
                    provider="aisa",
                )
            raise XApiError(
                f"AISA API error ({e.code}): {error_body[:300]}",
                provider="aisa",
                status_code=e.code,
            )
        except urllib.error.URLError as e:
            raise XProviderUnavailableError(
                f"AISA unreachable: {e.reason}",
                provider="aisa",
            )

    # ==================== User Read APIs ====================

    def user_info(self, username: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/info", params={"userName": username})

    def user_about(self, username: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user_about", params={"userName": username})

    def batch_user_info(self, user_ids: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/batch_info_by_ids", params={"userIds": user_ids})

    def user_tweets(self, username: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/last_tweets", params={"userName": username, "cursor": cursor})

    def user_mentions(self, username: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/mentions", params={"userName": username, "cursor": cursor})

    def followers(self, username: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/followers", params={"userName": username, "cursor": cursor})

    def followings(self, username: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/followings", params={"userName": username, "cursor": cursor})

    def verified_followers(self, user_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/verifiedFollowers", params={"user_id": user_id, "cursor": cursor})

    def check_follow_relationship(self, source: str, target: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/twitter/user/check_follow_relationship",
            params={"source_user_name": source, "target_user_name": target},
        )

    def user_search(self, query: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/user/search", params={"query": query, "cursor": cursor})

    # ==================== Tweet Read APIs ====================

    def tweet_search(self, query: str, query_type: str = "Latest", cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/twitter/tweet/advanced_search",
            params={"query": query, "queryType": query_type, "cursor": cursor},
        )

    def tweet_detail(self, tweet_ids: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/tweets", params={"tweet_ids": tweet_ids})

    def tweet_replies(self, tweet_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/tweet/replies", params={"tweetId": tweet_id, "cursor": cursor})

    def tweet_quotes(self, tweet_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/tweet/quotes", params={"tweetId": tweet_id, "cursor": cursor})

    def tweet_retweeters(self, tweet_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/tweet/retweeters", params={"tweetId": tweet_id, "cursor": cursor})

    def tweet_thread(self, tweet_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/tweet/thread_context", params={"tweetId": tweet_id, "cursor": cursor})

    def article(self, tweet_id: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/article", params={"tweet_id": tweet_id})

    # ==================== Trends, Lists, Communities, Spaces ====================

    def trends(self, woeid: int = 1) -> Dict[str, Any]:
        return self._request("GET", "/twitter/trends", params={"woeid": woeid})

    def list_members(self, list_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/list/members", params={"list_id": list_id, "cursor": cursor})

    def list_followers(self, list_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/list/followers", params={"list_id": list_id, "cursor": cursor})

    def community_info(self, community_id: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/community/info", params={"community_id": community_id})

    def community_members(self, community_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/community/members", params={"community_id": community_id, "cursor": cursor})

    def community_moderators(self, community_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/community/moderators", params={"community_id": community_id, "cursor": cursor})

    def community_tweets(self, community_id: str, cursor: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", "/twitter/community/tweets", params={"community_id": community_id, "cursor": cursor})

    def space_detail(self, space_id: str) -> Dict[str, Any]:
        return self._request("GET", "/twitter/spaces/detail", params={"space_id": space_id})
