#!/usr/bin/env python3
"""X-specific error types for the all-x integration."""


class XError(Exception):
    """Base exception for all X integration errors."""
    def __init__(self, message: str, provider: str = "", action: str = ""):
        super().__init__(message)
        self.provider = provider
        self.action = action


class XAuthError(XError):
    """Authentication failure — missing or invalid credentials."""
    pass


class XRateLimitError(XError):
    """Rate limit exceeded."""
    def __init__(self, message: str, provider: str = "", action: str = "",
                 limit: int = 0, remaining: int = 0, reset_at: str = ""):
        super().__init__(message, provider=provider, action=action)
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at


class XProviderUnavailableError(XError):
    """Requested provider is not configured or unreachable."""
    pass


class XActionNotSupportedError(XError):
    """The requested action is not supported by the selected provider."""
    pass


class XApiError(XError):
    """Upstream API returned an error response."""
    def __init__(self, message: str, provider: str = "", action: str = "",
                 status_code: int = 0, error_code: str = ""):
        super().__init__(message, provider=provider, action=action)
        self.status_code = status_code
        self.error_code = error_code
