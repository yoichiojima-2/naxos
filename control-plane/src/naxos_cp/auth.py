import logging
import os
import urllib.request

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import config

log = logging.getLogger(__name__)

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key-jwk"
METADATA_REGION_URL = "http://metadata.google.internal/computeMetadata/v1/instance/region"
_request = google_requests.Request()
_derived_audience: str | None = None


def _derive_audience() -> str:
    """IAP JWT audience for IAP enabled directly on this Cloud Run service,
    derived from the metadata server so it survives env-var drift."""
    service = os.environ.get("K_SERVICE", "")
    if not service:
        return ""
    try:
        req = urllib.request.Request(METADATA_REGION_URL, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            # "projects/<project-number>/regions/<region>"
            _, number, _, region = resp.read().decode().strip().split("/")
    except (OSError, ValueError) as exc:
        log.warning("could not derive IAP audience from metadata server: %s", exc)
        return ""
    return f"/projects/{number}/locations/{region}/services/{service}"


def _iap_audience() -> str:
    global _derived_audience
    if config.IAP_AUDIENCE:
        return config.IAP_AUDIENCE
    if _derived_audience is None:
        _derived_audience = _derive_audience()
    return _derived_audience


def principal_of(request: Request) -> str:
    """Verified IAP identity, or the dev principal in explicit dev mode."""
    audience = _iap_audience()
    if not audience:
        if config.DEV_MODE:
            return config.DEV_PRINCIPAL
        raise HTTPException(500, "IAP_AUDIENCE is not configured and NAXOS_DEV_MODE is not set")
    assertion = request.headers.get("x-goog-iap-jwt-assertion")
    if not assertion:
        raise HTTPException(401, "missing IAP assertion")
    try:
        claims = id_token.verify_token(assertion, _request, audience=audience, certs_url=IAP_CERTS)
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
