import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from google.auth import jwt as google_jwt
from google.auth.crypt import es256
from google.oauth2 import id_token

from naxos_cp import auth, config


async def test_principal_fails_loud_without_iap_or_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "IAP_AUDIENCE", "")
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(auth, "_derived_audience", "")
    with pytest.raises(HTTPException) as exc:
        auth.principal_of(None)
    assert exc.value.status_code == 500


async def test_principal_falls_back_to_dev_principal_in_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "IAP_AUDIENCE", "")
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(auth, "_derived_audience", "")
    assert auth.principal_of(None) == config.DEV_PRINCIPAL


async def test_audience_derived_from_metadata_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "naxos-api")

    class FakeResponse:
        def read(self):
            return b"projects/450555904/regions/asia-northeast1"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    assert (
        auth._derive_audience()
        == "/projects/450555904/locations/asia-northeast1/services/naxos-api"
    )


async def test_audience_derivation_off_cloud_run_is_empty(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert auth._derive_audience() == ""


async def test_configured_audience_wins_over_derivation(monkeypatch):
    monkeypatch.setattr(config, "IAP_AUDIENCE", "/projects/1/locations/r/services/s")
    monkeypatch.setattr(auth, "_derived_audience", "/projects/2/locations/r/services/s")
    assert auth._iap_audience() == "/projects/1/locations/r/services/s"


AUDIENCE = "/projects/450555904/locations/asia-northeast1/services/naxos-api"


def _iap_style_assertion() -> tuple[str, dict]:
    """ES256 token plus a JWK cert set, the format the real IAP certs URL serves."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_numbers()

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk_set = {
        "keys": [
            {
                "alg": "ES256",
                "crv": "P-256",
                "kid": "testkid",
                "kty": "EC",
                "use": "sig",
                "x": b64url(pub.x.to_bytes(32, "big")),
                "y": b64url(pub.y.to_bytes(32, "big")),
            }
        ]
    }
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = es256.ES256Signer.from_string(pem, key_id="testkid")
    now = int(time.time())
    token = google_jwt.encode(
        signer,
        {
            "aud": AUDIENCE,
            "iss": "https://cloud.google.com/iap",
            "iat": now,
            "exp": now + 600,
            "email": "user@example.com",
            "sub": "accounts.google.com:123",
        },
    )
    return token.decode(), jwk_set


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


async def test_principal_verifies_es256_jwk_assertion(monkeypatch):
    """IAP signs with ES256 and serves JWK-format certs; google-auth needs the
    pyjwt extra to parse them — this fails if that dependency goes missing."""
    token, jwk_set = _iap_style_assertion()
    monkeypatch.setattr(config, "IAP_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(id_token, "_fetch_certs", lambda request, certs_url: jwk_set)
    request = FakeRequest({"x-goog-iap-jwt-assertion": token})
    assert auth.principal_of(request) == "user@example.com"


async def test_principal_rejects_wrong_audience(monkeypatch):
    token, jwk_set = _iap_style_assertion()
    monkeypatch.setattr(config, "IAP_AUDIENCE", "/projects/450555904/locations/r/services/other")
    monkeypatch.setattr(id_token, "_fetch_certs", lambda request, certs_url: jwk_set)
    request = FakeRequest({"x-goog-iap-jwt-assertion": token})
    with pytest.raises(HTTPException) as exc:
        auth.principal_of(request)
    assert exc.value.status_code == 401
