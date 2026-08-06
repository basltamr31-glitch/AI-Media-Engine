"""Email ingestion: Google OAuth and the Gmail fetcher."""

from __future__ import annotations

from engine.adapters.sources.email.gmail import (
    GmailApiClient,
    GmailFetcher,
    GmailFetchError,
    GoogleApiGmailClient,
)
from engine.adapters.sources.email.oauth import (
    DEFAULT_SCOPES,
    GMAIL_READONLY_SCOPE,
    GoogleAuthError,
    GoogleCredentialStore,
)

__all__ = [
    "DEFAULT_SCOPES",
    "GMAIL_READONLY_SCOPE",
    "GmailApiClient",
    "GmailFetchError",
    "GmailFetcher",
    "GoogleApiGmailClient",
    "GoogleAuthError",
    "GoogleCredentialStore",
]
