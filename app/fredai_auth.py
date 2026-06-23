from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.config import AppConfig


class FredAIAuthError(RuntimeError):
    """Raised when the FredAI workspace token cannot be resolved."""


@dataclass
class FredAIToken:
    access_token: str
    expires_at: float = 0.0
    source: str = "oauth"

    @property
    def expires_in_seconds(self) -> int:
        if not self.expires_at:
            return -1
        return int(self.expires_at - time.time())

    def is_expiring(self, skew_seconds: int = 120) -> bool:
        return bool(self.expires_at and time.time() >= self.expires_at - skew_seconds)


class FredAIAuth:
    """Password-grant OAuth helper for the internal FredAI gateway."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._cached: Optional[FredAIToken] = None

    def token(self, *, force_refresh: bool = False) -> FredAIToken:
        if self._cached and not force_refresh and not self._cached.is_expiring():
            return self._cached
        self._cached = self._fetch_token()
        return self._cached

    def jwt_token(self) -> str:
        return self._config.fredai_jwt_token

    def _fetch_token(self) -> FredAIToken:
        cfg = self._config
        missing = [
            name
            for name, value in {
                "FREDAI_OAUTH_URL": cfg.fredai_oauth_url,
                "FREDAI_CLIENT_ID or CLIENT_ID": cfg.fredai_client_id,
                "FREDAI_CLIENT_SECRET or CLIENT_SECRET": cfg.fredai_client_secret,
                "FREDAI_OAUTH_USERNAME or OAUTH_USERNAME": cfg.fredai_oauth_username,
                "FREDAI_OAUTH_PASSWORD_B64 or OAUTH_PASSWORD": cfg.fredai_oauth_password_b64,
            }.items()
            if not value
        ]
        if missing:
            raise FredAIAuthError(f"Missing FredAI auth configuration: {', '.join(missing)}")

        try:
            password = base64.b64decode(cfg.fredai_oauth_password_b64).decode()
        except Exception as exc:
            raise FredAIAuthError("FREDAI_OAUTH_PASSWORD_B64 is not valid base64.") from exc

        data = {
            "grant_type": "password",
            "client_id": cfg.fredai_client_id,
            "client_secret": cfg.fredai_client_secret,
            "username": cfg.fredai_oauth_username,
            "password": password,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            with httpx.Client(
                verify=cfg.fredai_verify_ssl,
                timeout=cfg.fredai_timeout_seconds,
            ) as client:
                response = client.post(cfg.fredai_oauth_url, data=data, headers=headers)
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
        except Exception as exc:
            raise FredAIAuthError(f"FredAI OAuth token request failed: {exc}") from exc

        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise FredAIAuthError("FredAI OAuth response did not include access_token.")

        expires_in = payload.get("expires_in")
        expires_at = 0.0
        if isinstance(expires_in, (int, float)):
            expires_at = time.time() + float(expires_in)

        return FredAIToken(access_token=access_token, expires_at=expires_at)
