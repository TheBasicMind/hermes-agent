#!/usr/bin/env python3
"""Official X API v2 write adapter.

Uses tweepy for OAuth 1.0a User Context (required for writes).
Auth env vars: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
Bearer token (X_BEARER_TOKEN) used for app-only reads when falling back.

This adapter only handles write operations — reads go through AISA (primary)
or are forwarded here as fallback.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..errors import XApiError, XAuthError, XRateLimitError


class XOfficialAdapter:
    """Official X API v2 adapter for write operations."""

    BASE_URL = "https://api.twitter.com/2"
    UPLOAD_URL = "https://upload.twitter.com/1.1"
    TIMEOUT = 30

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        bearer_token: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_secret: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("X_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("X_API_SECRET", "")
        self.bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN", "")
        self.access_token = access_token or os.environ.get("X_ACCESS_TOKEN", "")
        self.access_token_secret = access_token_secret or os.environ.get("X_ACCESS_TOKEN_SECRET", "")

        self._tweepy_client = None

    @property
    def has_user_auth(self) -> bool:
        """Check if OAuth 1.0a User Context credentials are available."""
        return bool(self.api_key and self.api_secret and self.access_token and self.access_token_secret)

    @property
    def has_bearer(self) -> bool:
        """Check if Bearer Token (app-only) is available."""
        return bool(self.bearer_token)

    def _require_user_auth(self) -> None:
        """Raise if user auth credentials are missing."""
        if not self.has_user_auth:
            raise XAuthError(
                "Official X API write requires OAuth 1.0a User Context credentials. "
                "Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET in ~/.hermes/.env",
                provider="x_official",
            )

    def _get_tweepy_client(self):
        """Lazy-init tweepy client (optional dependency).

        Initialized with both bearer token (for app-only reads) and OAuth1 user
        creds (for writes). Tweepy picks the right one per call: read methods
        default to user_auth=False (bearer), writes default to user_auth=True
        (OAuth1). This is important because bearer and user creds can have
        different validity windows on this account.
        """
        if self._tweepy_client is not None:
            return self._tweepy_client

        try:
            import tweepy
        except ImportError:
            raise XAuthError(
                "tweepy is required for official X API access. Install with: pip install tweepy",
                provider="x_official",
            )

        if not self.has_bearer and not self.has_user_auth:
            raise XAuthError(
                "Official X API requires at least one of: X_BEARER_TOKEN (reads) "
                "or OAuth1 user creds (X_API_KEY/X_API_SECRET/X_ACCESS_TOKEN/X_ACCESS_TOKEN_SECRET, writes)",
                provider="x_official",
            )

        self._tweepy_client = tweepy.Client(
            bearer_token=self.bearer_token or None,
            consumer_key=self.api_key or None,
            consumer_secret=self.api_secret or None,
            access_token=self.access_token or None,
            access_token_secret=self.access_token_secret or None,
            wait_on_rate_limit=False,
        )
        return self._tweepy_client

    # ==================== Write Operations ====================

    def post(self, text: str, media_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Post a new tweet."""
        client = self._get_tweepy_client()
        try:
            kwargs: Dict[str, Any] = {"text": text}
            if media_ids:
                kwargs["media_ids"] = media_ids
            response = client.create_tweet(**kwargs)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "post")

    def reply(self, text: str, tweet_id: str, media_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Reply to a tweet."""
        client = self._get_tweepy_client()
        try:
            kwargs: Dict[str, Any] = {"text": text, "in_reply_to_tweet_id": tweet_id}
            if media_ids:
                kwargs["media_ids"] = media_ids
            response = client.create_tweet(**kwargs)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "reply")

    def quote(self, text: str, tweet_id: str, media_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Quote a tweet."""
        client = self._get_tweepy_client()
        try:
            kwargs: Dict[str, Any] = {"text": text, "quote_tweet_id": tweet_id}
            if media_ids:
                kwargs["media_ids"] = media_ids
            response = client.create_tweet(**kwargs)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "quote")

    def delete(self, tweet_id: str) -> Dict[str, Any]:
        """Delete a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.delete_tweet(id=tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "delete")

    def like(self, tweet_id: str) -> Dict[str, Any]:
        """Like a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.like(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "like")

    def unlike(self, tweet_id: str) -> Dict[str, Any]:
        """Unlike a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.unlike(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unlike")

    def repost(self, tweet_id: str) -> Dict[str, Any]:
        """Repost (retweet) a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.retweet(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "repost")

    def unrepost(self, tweet_id: str) -> Dict[str, Any]:
        """Unrepost (unretweet) a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.unretweet(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unrepost")

    def bookmark(self, tweet_id: str) -> Dict[str, Any]:
        """Bookmark a tweet."""
        client = self._get_tweepy_client()
        try:
            response = client.bookmark(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "bookmark")

    def unbookmark(self, tweet_id: str) -> Dict[str, Any]:
        """Remove a bookmark."""
        client = self._get_tweepy_client()
        try:
            response = client.remove_bookmark(tweet_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unbookmark")

    def follow(self, username: str) -> Dict[str, Any]:
        """Follow a user by username."""
        client = self._get_tweepy_client()
        try:
            # First get user ID from username
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.follow_user(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "follow")

    def unfollow(self, username: str) -> Dict[str, Any]:
        """Unfollow a user by username."""
        client = self._get_tweepy_client()
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.unfollow_user(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unfollow")

    def block(self, username: str) -> Dict[str, Any]:
        """Block a user."""
        client = self._get_tweepy_client()
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.block(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "block")

    def unblock(self, username: str) -> Dict[str, Any]:
        """Unblock a user."""
        client = self._get_tweepy_client()
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.unblock(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unblock")

    def mute(self, username: str) -> Dict[str, Any]:
        """Mute a user."""
        client = self._get_tweepy_client()
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.mute(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "mute")

    def unmute(self, username: str) -> Dict[str, Any]:
        """Unmute a user."""
        client = self._get_tweepy_client()
        try:
            user_resp = client.get_user(username=username)
            if not user_resp.data:
                return {"success": False, "errors": [f"User @{username} not found"]}
            user_id = user_resp.data.id
            response = client.unmute(user_id)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "unmute")

    def media_upload(self, media_path: str) -> Dict[str, Any]:
        """Upload media and return the media_key for use in tweets."""
        import tweepy
        # tweepy media upload uses API v1.1 auth
        auth = tweepy.OAuth1UserHandler(
            self.api_key, self.api_secret,
            self.access_token, self.access_token_secret,
        )
        api = tweepy.API(auth)
        try:
            media = api.media_upload(media_path)
            return {"media_key": media.media_id_string, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "media_upload")

    def dm_send(self, recipient_id: str, text: str) -> Dict[str, Any]:
        """Send a direct message."""
        client = self._get_tweepy_client()
        try:
            response = client.create_direct_message(participant_id=recipient_id, text=text)
            return {"data": response.data, "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "dm_send")

    def dm_list(self, max_results: int = 50) -> Dict[str, Any]:
        """List recent direct message events."""
        client = self._get_tweepy_client()
        try:
            # Use the raw HTTP approach since tweepy DM listing varies by version
            import tweepy
            response = client.get_direct_message_events(max_results=max_results)
            return {"data": str(response.data), "success": True}
        except Exception as e:
            return self._handle_tweepy_error(e, "dm_list")

    # ==================== Fallback Read Operations ====================

    def user_get(self, username: str) -> Dict[str, Any]:
        """Get user info (fallback read via official API). Prefers bearer token."""
        if not self.has_bearer and not self.has_user_auth:
            raise XAuthError("No auth available for official X API read fallback", provider="x_official")
        client = self._get_tweepy_client()
        try:
            response = client.get_user(username=username, user_auth=not self.has_bearer, user_fields=[
                "created_at", "description", "public_metrics", "verified",
                "verified_type", "profile_image_url", "url", "location",
                "pinned_tweet_id",
            ])
            if not response.data:
                return {"data": None}
            user = response.data
            result = dict(user.data)
            if response.includes and "tweets" in response.includes:
                result["includes"] = {"tweets": [dict(t.data) for t in response.includes["tweets"]]}
            return {"data": result}
        except Exception as e:
            return self._handle_tweepy_error(e, "user_get")

    def tweet_get(self, tweet_id: str) -> Dict[str, Any]:
        """Get tweet detail (fallback read via official API). Prefers bearer token."""
        if not self.has_bearer and not self.has_user_auth:
            raise XAuthError("No auth available for official X API read fallback", provider="x_official")
        client = self._get_tweepy_client()
        try:
            response = client.get_tweet(
                id=tweet_id,
                user_auth=not self.has_bearer,
                tweet_fields=["created_at", "public_metrics", "entities",
                              "attachments", "referenced_tweets", "conversation_id",
                              "lang", "possibly_sensitive", "in_reply_to_user_id",
                              "edit_history_tweet_ids"],
                expansions=["author_id", "referenced_tweets.id.author_id"],
                user_fields=["username", "name", "public_metrics", "verified",
                             "verified_type", "profile_image_url"],
            )
            result: Dict[str, Any] = {"data": dict(response.data.data) if response.data else None}
            if response.includes:
                includes: Dict[str, Any] = {}
                if "users" in response.includes:
                    includes["users"] = [dict(u.data) for u in response.includes["users"]]
                if "tweets" in response.includes:
                    includes["tweets"] = [dict(t.data) for t in response.includes["tweets"]]
                result["includes"] = includes
            return result
        except Exception as e:
            return self._handle_tweepy_error(e, "tweet_get")

    def _handle_tweepy_error(self, e: Exception, action: str) -> Dict[str, Any]:
        """Convert tweepy exceptions to structured error dicts."""
        import tweepy
        if isinstance(e, tweepy.TooManyRequests):
            raise XRateLimitError(
                f"X API rate limit exceeded for {action}",
                provider="x_official",
                action=action,
            )
        if isinstance(e, tweepy.Unauthorized):
            raise XAuthError(
                f"X API auth failed for {action}: {e}",
                provider="x_official",
                action=action,
            )
        if isinstance(e, tweepy.Forbidden):
            raise XAuthError(
                f"X API forbidden for {action}: {e}",
                provider="x_official",
                action=action,
            )
        # Generic error
        return {"success": False, "errors": [str(e)], "action": action}
