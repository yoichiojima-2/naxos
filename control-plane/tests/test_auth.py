import pytest
from fastapi import HTTPException

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
