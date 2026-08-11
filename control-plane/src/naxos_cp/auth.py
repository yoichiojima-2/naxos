import logging

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import config

log = logging.getLogger(__name__)

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key-jwk"
_request = google_requests.Request()


def principal_of(request: Request) -> str:
    """Verified IAP identity, or the dev principal in explicit dev mode."""
    if not config.IAP_AUDIENCE:
        if config.DEV_MODE:
            return config.DEV_PRINCIPAL
        raise HTTPException(500, "IAP_AUDIENCE is not configured and NAXOS_DEV_MODE is not set")
    assertion = request.headers.get("x-goog-iap-jwt-assertion")
    if not assertion:
        raise HTTPException(401, "missing IAP assertion")
    try:
        claims = id_token.verify_token(
            assertion, _request, audience=config.IAP_AUDIENCE, certs_url=IAP_CERTS
        )
    except Exception as exc:
        log.warning("IAP verification failed: %s", exc)
        raise HTTPException(401, "invalid IAP assertion") from exc
    return claims["email"]


def caller_service_account(request: Request) -> str:
    """Service account of a sandbox calling the internal surface.

    Cloud Run IAM has already rejected unauthenticated callers; this reads the
    identity so the caller can be matched against the session's environment.
    """
    if not config.ENFORCE_CALLER_AUTH:
        return request.headers.get("x-naxos-dev-sa", config.DEV_PRINCIPAL)
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = id_token.verify_oauth2_token(token, _request)
    except Exception as exc:
        raise HTTPException(401, "invalid identity token") from exc
    email = claims.get("email")
    if not email:
        raise HTTPException(401, "identity token has no email claim")
    return email
