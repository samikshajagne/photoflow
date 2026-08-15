"""
PhotoFlow desktop authentication session.

Stores the customer's refresh token in the Windows Credential Manager
through the ``keyring`` package. Access tokens are kept only in memory.

The desktop client uses:
    POST /api/v1/auth/login
    POST /api/v1/auth/refresh
    POST /api/v1/auth/logout
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import keyring
import requests


SERVICE_NAME = "PhotoFlow"
REFRESH_TOKEN_ACCOUNT = "refresh_token"

# Development backend.
# This will become the production API URL in the customer build.
API_BASE_URL = "https://photoflow-api.onrender.com/api/v1"


@dataclass
class AuthSession:
    access_token: str
    refresh_token: str
    expires_in: int
    user: dict[str, Any]


class AuthError(Exception):
    """Authentication operation failed."""


class AuthManager:
    """Manage a PhotoFlow desktop authentication session."""

    def __init__(self, base_url: str = API_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._access_token: str | None = None
        self._refresh_token: str | None = keyring.get_password(
            SERVICE_NAME,
            REFRESH_TOKEN_ACCOUNT,
        )

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token or self._refresh_token)

    @property
    def user(self) -> dict[str, Any] | None:
        return getattr(self, "_user", None)

    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate with the PhotoFlow backend."""

        try:
            response = requests.post(
                f"{self.base_url}/auth/login",
                json={
                    "email": email.strip().lower(),
                    "password": password,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise AuthError(
                "Could not reach the PhotoFlow server. "
                "Check your internet connection and try again."
            ) from exc

        if response.status_code == 401:
            raise AuthError("Invalid email or password.")

        if response.status_code == 429:
            raise AuthError(
                "Too many login attempts. Please wait a moment and try again."
            )

        if not response.ok:
            raise AuthError(
                f"Login failed ({response.status_code}). Please try again."
            )

        data = response.json()

        self._set_session(data)

        return data["user"]

    def refresh(self) -> bool:
        """
        Refresh the access token.

        The backend rotates refresh tokens, so the newly returned refresh token
        replaces the old one in Windows Credential Manager.
        """

        if not self._refresh_token:
            return False

        try:
            response = requests.post(
                f"{self.base_url}/auth/refresh",
                json={"refresh_token": self._refresh_token},
                timeout=15,
            )
        except requests.RequestException:
            return False

        if not response.ok:
            self.clear_session()
            return False

        try:
            data = response.json()
            self._set_session(data)
        except (ValueError, KeyError, TypeError):
            self.clear_session()
            return False

        return True

    def ensure_access_token(self) -> str | None:
        """Return an access token, refreshing the session if necessary."""

        if self._access_token:
            return self._access_token

        if self.refresh():
            return self._access_token

        return None

    def logout(self) -> None:
        """Revoke the refresh-token session and clear local credentials."""

        token = self._refresh_token

        if token:
            try:
                requests.post(
                    f"{self.base_url}/auth/logout",
                    json={"refresh_token": token},
                    timeout=10,
                )
            except requests.RequestException:
                # Logout must still clear local credentials if the server
                # cannot be reached.
                pass

        self.clear_session()

    def clear_session(self) -> None:
        """Remove the local authentication session."""

        self._access_token = None
        self._refresh_token = None
        self._user = None

        try:
            keyring.delete_password(
                SERVICE_NAME,
                REFRESH_TOKEN_ACCOUNT,
            )
        except keyring.errors.PasswordDeleteError:
            pass

    def _set_session(self, data: dict[str, Any]) -> None:
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")

        if not access_token or not refresh_token:
            raise AuthError("The server returned an incomplete login session.")

        self._access_token = str(access_token)
        self._refresh_token = str(refresh_token)
        self._user = data.get("user") or {}

        keyring.set_password(
            SERVICE_NAME,
            REFRESH_TOKEN_ACCOUNT,
            self._refresh_token,
        )


__all__ = [
    "API_BASE_URL",
    "AuthError",
    "AuthManager",
    "AuthSession",
]