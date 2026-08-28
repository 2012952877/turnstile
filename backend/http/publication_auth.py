from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

from ..config import Settings, get_settings
from ..persistence.auth_store import AuthStore
from ..services.auth_service import hash_session_token


def publication_auth_store() -> Iterator[AuthStore]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for publication authorization")
    store = AuthStore(settings.database_url)
    try:
        yield store
    finally:
        store.close()


PublicationStore = Annotated[AuthStore, Depends(publication_auth_store)]
PublicationSettings = Annotated[Settings, Depends(get_settings)]


def require_publication_owner(
    request: Request,
    store: PublicationStore,
    settings: PublicationSettings,
) -> str:
    session = request.cookies.get(settings.session_cookie_name)
    owner = store.session_owner(hash_session_token(session)) if session else None
    if owner is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if owner.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner role is required")

    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is not None:
        normalized = origin.rstrip("/")
        allowed = {value.rstrip("/") for value in settings.cors_origins}
        same_host = urlsplit(normalized).netloc == request.headers.get("host")
        if normalized not in allowed and not same_host:
            raise HTTPException(status_code=403, detail="Origin is not allowed")
    return str(owner["email"])


PublicationOwner = Annotated[str, Depends(require_publication_owner)]