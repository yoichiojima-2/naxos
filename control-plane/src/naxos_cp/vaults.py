import functools
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.ids import new_id
from pydantic import BaseModel, Field

from . import config, db
from .auth import principal_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")


@functools.cache
def _secrets_client():
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceAsyncClient()


def _secret_name(credential_id: str) -> str:
    return f"projects/{config.PROJECT_ID}/secrets/vault-{credential_id}"


async def _store_secret(credential_id: str, value: str) -> None:
    """Write the credential value to Secret Manager.

    The value never touches Postgres; without a project (local dev) it is
    refused rather than stored somewhere weaker.
    """
    if not config.PROJECT_ID:
        raise HTTPException(503, "Secret Manager is not configured")
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import secretmanager

    client = _secrets_client()
    secret_id = f"vault-{credential_id}"
    try:
        await client.create_secret(
            request=secretmanager.CreateSecretRequest(
                parent=f"projects/{config.PROJECT_ID}",
                secret_id=secret_id,
                secret=secretmanager.Secret(
                    replication=secretmanager.Replication(
                        automatic=secretmanager.Replication.Automatic()
                    )
                ),
            )
        )
    except AlreadyExists:
        log.warning("secret %s already exists; adding a new version", secret_id)
    name = _secret_name(credential_id)
    await client.add_secret_version(
        request=secretmanager.AddSecretVersionRequest(
            parent=name,
            payload=secretmanager.SecretPayload(data=value.encode()),
        )
    )
    if config.EGRESS_SA:
        policy = await client.get_iam_policy(request={"resource": name})
        policy.bindings.add(
            role="roles/secretmanager.secretAccessor",
            members=[f"serviceAccount:{config.EGRESS_SA}"],
        )
        await client.set_iam_policy(request={"resource": name, "policy": policy})


async def _delete_secret(secret_ref: str) -> None:
    try:
        await _secrets_client().delete_secret(name=secret_ref)
    except Exception:
        log.exception("failed to delete secret %s", secret_ref)


class VaultIn(BaseModel):
    name: str


@router.post("/vaults", status_code=201)
async def create_vault(body: VaultIn, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        existing = await conn.fetchval("SELECT 1 FROM vaults WHERE name = $1", body.name)
        if existing:
            raise HTTPException(409, "vault name already exists")
        row = await conn.fetchrow(
            "INSERT INTO vaults (id, name) VALUES ($1, $2) RETURNING *",
            new_id("vault"),
            body.name,
        )
    return dict(row)


@router.get("/vaults")
async def list_vaults(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch("SELECT * FROM vaults WHERE archived_at IS NULL ORDER BY name")
    return {"data": [dict(r) for r in rows]}


@router.post("/vaults/{vault_id}/archive")
async def archive_vault(vault_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE vaults SET archived_at = now() WHERE id = $1 AND archived_at IS NULL",
            vault_id,
        )
    return {"id": vault_id, "archived": True}


class CredentialIn(BaseModel):
    name: str
    type: str = Field(pattern="^(env|header)$")
    value: str
    target: dict[str, Any] = Field(default_factory=dict)
    # env:    {"env_var": "GITHUB_TOKEN"} — placeholder exported into the sandbox
    # header: {"host": "api.github.com", "header": "authorization", "prefix": "Bearer "}


@router.post("/vaults/{vault_id}/credentials", status_code=201)
async def create_credential(
    vault_id: str, body: CredentialIn, _: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        vault = await conn.fetchval(
            "SELECT 1 FROM vaults WHERE id = $1 AND archived_at IS NULL", vault_id
        )
        if not vault:
            raise HTTPException(404, "vault not found or archived")
        credential_id = new_id("credential")
        secret_ref = _secret_name(credential_id)
        row = await conn.fetchrow(
            "INSERT INTO vault_credentials (id, vault_id, name, type, secret_ref, target) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, vault_id, name, type, target, "
            "created_at",
            credential_id,
            vault_id,
            body.name,
            body.type,
            secret_ref,
            body.target,
        )
        await _store_secret(credential_id, body.value)
    return dict(row)


@router.get("/vaults/{vault_id}/credentials")
async def list_credentials(vault_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT id, vault_id, name, type, target, created_at FROM vault_credentials "
            "WHERE vault_id = $1 ORDER BY name",
            vault_id,
        )
    return {"data": [dict(r) for r in rows]}


@router.delete("/vaults/{vault_id}/credentials/{credential_id}")
async def delete_credential(
    vault_id: str, credential_id: str, _: str = Depends(principal_of)
) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "DELETE FROM vault_credentials WHERE id = $1 AND vault_id = $2 RETURNING secret_ref",
            credential_id,
            vault_id,
        )
        if row is None:
            raise HTTPException(404, "credential not found")
    await _delete_secret(row["secret_ref"])
    return {"id": credential_id, "deleted": True}
