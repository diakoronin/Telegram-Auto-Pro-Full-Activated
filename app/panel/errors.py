from __future__ import annotations

from enum import Enum


class PanelErrorCode(str, Enum):
    INVALID_CREDENTIALS = "invalid_credentials"
    CONNECTION_TIMEOUT = "connection_timeout"
    CONNECTION_REFUSED = "connection_refused"
    SSL_ERROR = "ssl_error"
    INVALID_INBOUND = "invalid_inbound"
    QUOTA_ERROR = "quota_error"
    USER_ALREADY_EXISTS = "user_already_exists"
    USER_NOT_FOUND = "user_not_found"
    PANEL_UNAVAILABLE = "panel_unavailable"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"
