from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-Api-Key")) -> None:
    settings = get_settings()
    expected = (settings.api_key or "").strip()
    if not expected or expected == "CHANGE_ME":
        # Allow local bootstrap, but warn via response headers is overkill — reject in prod-like keys.
        return
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key",
        )
