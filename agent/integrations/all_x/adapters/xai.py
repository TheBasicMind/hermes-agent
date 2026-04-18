#!/usr/bin/env python3
"""xAI/Grok adapter — semantic/agentic real-time X search.

Source reference: OpenClaw x-search search.py.
API_URL: https://api.x.ai/v1/responses
Model: grok-4.20-reasoning (configurable)
Tool config type: x_search
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..errors import XApiError, XAuthError, XProviderUnavailableError


TWEET_URL_RE = re.compile(r"x\.com/([a-zA-Z0-9_]{1,15})/status/(\d+)")


class XaiAdapter:
    """Semantic search adapter using xAI Grok API."""

    API_URL = "https://api.x.ai/v1/responses"
    DEFAULT_MODEL = "grok-4.20-reasoning"
    TIMEOUT = 120

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        if not self.api_key:
            raise XAuthError(
                "XAI_API_KEY is required for semantic X search. Set it in ~/.hermes/.env",
                provider="xai",
            )

    def search(
        self,
        query: str,
        handles_allow: Optional[List[str]] = None,
        handles_exclude: Optional[List[str]] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        include_images: bool = False,
        include_video: bool = False,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a semantic X search via xAI/Grok.

        Returns the raw xAI response dict (to be normalized by the mapper).
        """
        tool_config: Dict[str, Any] = {"type": "x_search"}
        if handles_allow:
            tool_config["allowed_x_handles"] = handles_allow
        if handles_exclude:
            tool_config["excluded_x_handles"] = handles_exclude
        if from_date:
            tool_config["from_date"] = from_date
        if to_date:
            tool_config["to_date"] = to_date
        if include_images:
            tool_config["enable_image_understanding"] = True
        if include_video:
            tool_config["enable_video_understanding"] = True

        body = json.dumps({
            "model": model or self.DEFAULT_MODEL,
            "input": [{"role": "user", "content": query}],
            "tools": [tool_config],
        }).encode()

        req = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Hermes-all-x/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in (401, 403):
                raise XAuthError(
                    f"xAI auth failed ({e.code}): {error_body[:200]}",
                    provider="xai",
                )
            raise XApiError(
                f"xAI API error ({e.code}): {error_body[:300]}",
                provider="xai",
                status_code=e.code,
            )
        except urllib.error.URLError as e:
            raise XProviderUnavailableError(
                f"xAI unreachable: {e.reason}",
                provider="xai",
            )

        return self._format_response(data, query)

    def _format_response(self, data: Dict[str, Any], query: str) -> Dict[str, Any]:
        """Parse the xAI responses API format into a structured dict."""
        outputs = data.get("output", [])
        if not isinstance(outputs, list):
            outputs = []

        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        tool_details = usage.get("server_side_tool_usage_details", {})
        if not isinstance(tool_details, dict):
            tool_details = {}

        message = next((o for o in outputs if isinstance(o, dict) and o.get("type") == "message"), None)
        content_blocks = (message or {}).get("content", [])
        if not isinstance(content_blocks, list):
            content_blocks = []

        text = "\n\n".join(
            c.get("text", "")
            for c in content_blocks
            if isinstance(c, dict) and c.get("text")
        )

        annotations = [
            a
            for c in content_blocks if isinstance(c, dict)
            for a in (c.get("annotations") or []) if isinstance(a, dict)
        ]

        citations = [
            {"text": a.get("title", ""), "url": a["url"]}
            for a in annotations
            if a.get("type") == "url_citation" and a.get("url")
        ]

        status = data.get("status", "unknown") or "unknown"
        if status not in ("completed", "unknown"):
            error = data.get("error", {})
            error_msg = error.get("message", "") if isinstance(error, dict) else str(error)
            text = f"Search {status}" + (f": {error_msg}" if error_msg else "") + ("\n\n" + text if text else "")

        return {
            "status": status,
            "query": query,
            "text": text,
            "citations": citations,
            "searches": tool_details.get("x_search_calls", 0),
            "tokens": {
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        }
