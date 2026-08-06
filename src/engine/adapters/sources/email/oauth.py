"""Google OAuth credential handling.

Separated from the fetcher because authentication has a completely different
lifecycle from ingestion: it is interactive, happens rarely, and involves a
browser. Mixing the two would make the fetcher impossible to test without a
Google account.

Two files, two meanings -- worth keeping straight:

``credentials.json``
    The OAuth *client*. Identifies the **application**. Downloaded by hand from
    Google Cloud Console. Read-only to us.

``token.json``
    The *user* grant. Identifies **you**. Written by ``engine sources auth``
    after you approve the consent screen, refreshed automatically thereafter.

Both are secrets and both are gitignored. Neither is ever logged.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Final

from engine.observability.logging import get_logger

logger = get_logger(__name__)

#: Read-only Gmail access. The narrowest scope that permits reading newsletters.
#:
#: We deliberately do NOT request ``gmail.modify``: marking mail as read would
#: mutate the user's inbox, and ingestion tracks processed state in our own
#: database instead. Non-destructive by design, and a materially easier
#: conversation during Google's verification review.
GMAIL_READONLY_SCOPE: Final = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_SCOPES: Final = (GMAIL_READONLY_SCOPE,)


class GoogleAuthError(RuntimeError):
    """Raised when credentials are missing, invalid or cannot be refreshed."""


class GoogleCredentialStore:
    """Loads, refreshes and persists Google OAuth credentials."""

    def __init__(
        self,
        *,
        credentials_file: Path,
        token_file: Path,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
    ) -> None:
        self._credentials_file = credentials_file
        self._token_file = token_file
        self._scopes = list(scopes)

    @property
    def credentials_file(self) -> Path:
        return self._credentials_file

    @property
    def token_file(self) -> Path:
        return self._token_file

    @property
    def scopes(self) -> list[str]:
        return list(self._scopes)

    def has_client_secrets(self) -> bool:
        return self._credentials_file.is_file()

    def has_token(self) -> bool:
        return self._token_file.is_file()

    def load(self) -> Any:
        """Return usable credentials, refreshing them if they have expired.

        Raises:
            GoogleAuthError: If no token exists or the refresh fails. Both cases
                are resolved the same way -- by running ``engine sources auth``
                -- so the error message says exactly that.
        """
        credentials = self._read_token()
        if credentials is None:
            msg = f"no cached Google token at {self._token_file}. Run: engine sources auth"
            raise GoogleAuthError(msg)

        if credentials.valid:
            return credentials

        if credentials.expired and credentials.refresh_token:
            self._refresh(credentials)
            return credentials

        msg = (
            "cached Google token is invalid and cannot be refreshed. "
            "Run: engine sources auth --force"
        )
        raise GoogleAuthError(msg)

    def authorise(self, *, port: int = 0) -> Any:
        """Run the interactive consent flow and cache the resulting token.

        Opens a browser, starts a temporary local server to catch the redirect,
        and writes ``token.json``. This is the only code path that requires a
        human, which is why it lives behind its own CLI command rather than
        being triggered lazily mid-pipeline.
        """
        # Imported lazily: the interactive flow is needed once per machine, and
        # importing it at module load would make every CLI command pay for it.
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.has_client_secrets():
            msg = (
                f"OAuth client file not found at {self._credentials_file}. "
                "Download it from Google Cloud Console (Credentials -> "
                "OAuth client ID -> Desktop app) and save it there."
            )
            raise GoogleAuthError(msg)

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._credentials_file), self._scopes
            )
            credentials = flow.run_local_server(port=port, open_browser=True)
        except Exception as exc:
            msg = f"OAuth flow failed: {exc}"
            raise GoogleAuthError(msg) from exc

        self._write_token(credentials)
        logger.info("google.authorised", token_file=str(self._token_file))
        return credentials

    def revoke_local(self) -> bool:
        """Delete the cached token. Returns True if a token was removed.

        Local only -- this does not revoke the grant on Google's side. That is
        done by the user at myaccount.google.com/permissions, and the CLI says so.
        """
        if not self.has_token():
            return False
        self._token_file.unlink()
        logger.info("google.token_removed", token_file=str(self._token_file))
        return True

    # --- internals -------------------------------------------------------

    def _read_token(self) -> Any | None:
        if not self.has_token():
            return None

        from google.oauth2.credentials import Credentials

        try:
            return Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(self._token_file), self._scopes
            )
        except (ValueError, KeyError) as exc:
            msg = (
                f"cached token at {self._token_file} is corrupt ({exc}). "
                "Delete it and run: engine sources auth"
            )
            raise GoogleAuthError(msg) from exc

    def _refresh(self, credentials: Any) -> None:
        from google.auth.transport.requests import Request

        try:
            credentials.refresh(Request())
        except Exception as exc:
            # The overwhelmingly common cause: an app left in "Testing"
            # publishing status, where Google expires refresh tokens after
            # 7 days. Say so, rather than leaving the user to discover it.
            msg = (
                f"could not refresh the Google token: {exc}. "
                "If your OAuth app is still in 'Testing' status, refresh tokens "
                "expire after 7 days -- either re-run 'engine sources auth' or "
                "set the app to 'In production' in Google Cloud Console."
            )
            raise GoogleAuthError(msg) from exc

        self._write_token(credentials)
        logger.info("google.token_refreshed")

    def _write_token(self, credentials: Any) -> None:
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(credentials.to_json(), encoding="utf-8")
        # Best-effort permission tightening; a no-op on Windows, which uses ACLs.
        with contextlib.suppress(OSError):
            self._token_file.chmod(0o600)
