import pytest
from fastapi import HTTPException

from naxos_cp import auth, config


async def test_principal_fails_loud_without_iap_or_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "IAP_AUDIENCE", "")
    monkeypatch.setattr(config, "DEV_MODE", False)
    with pytest.raises(HTTPException) as exc:
        auth.principal_of(None)
    assert exc.value.status_code == 500


async def test_principal_falls_back_to_dev_principal_in_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "IAP_AUDIENCE", "")
    monkeypatch.setattr(config, "DEV_MODE", True)
    assert auth.principal_of(None) == config.DEV_PRINCIPAL
