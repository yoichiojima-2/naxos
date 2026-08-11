import logging
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from naxos_shared.ids import new_id
from naxos_shared.paths import unsafe_relpath
from pydantic import BaseModel, Field

from . import config, db
from .auth import principal_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")

SKILL_ENTRY = "SKILL.md"
SEED_DIR = Path(__file__).resolve().parents[3] / "docs" / "skills"
SEED_PRINCIPAL = "system:seed"
READY = (
    f"EXISTS (SELECT 1 FROM skill_files f "
    f"  WHERE f.skill_id = s.id AND f.path = '{SKILL_ENTRY}') AS ready"
)


class SkillIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    description: str | None = None


@router.post("/skills", status_code=201)
async def create_skill(body: SkillIn, principal: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO skills (id, name, description, created_by) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                new_id("skill"),
                body.name,
                body.description,
                principal,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "an active skill with that name already exists") from None
    return dict(row)


@router.get("/skills")
async def list_skills(_: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            f"SELECT s.*, {READY} FROM skills s WHERE s.archived_at IS NULL ORDER BY s.name"
        )
    return {"data": [dict(r) for r in rows]}


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(f"SELECT s.*, {READY} FROM skills s WHERE s.id = $1", skill_id)
    if row is None:
        raise HTTPException(404, "skill not found")
    return dict(row)


@router.post("/skills/{skill_id}/archive")
async def archive_skill(skill_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE skills SET archived_at = now() WHERE id = $1 AND archived_at IS NULL",
            skill_id,
        )
    return {"id": skill_id, "archived": True}


class SkillFileIn(BaseModel):
    path: str = Field(pattern=r"^[a-zA-Z0-9._/-]{1,200}$")
    content: str


@router.post("/skills/{skill_id}/files", status_code=201)
async def put_file(
    skill_id: str, body: SkillFileIn, principal: str = Depends(principal_of)
) -> dict:
    if len(body.content.encode()) > config.MAX_SKILL_FILE_BYTES:
        raise HTTPException(413, f"skill file exceeds {config.MAX_SKILL_FILE_BYTES // 1024}KB")
    if unsafe_relpath(body.path):
        raise HTTPException(400, "path must be relative with no empty or .. segments")
    async with db.transaction() as conn:
        skill = await conn.fetchval(
            "SELECT 1 FROM skills WHERE id = $1 AND archived_at IS NULL", skill_id
        )
        if not skill:
            raise HTTPException(404, "skill not found or archived")
        row = await conn.fetchrow(
            "INSERT INTO skill_files (id, skill_id, path, content, updated_by) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (skill_id, path) DO UPDATE SET content = EXCLUDED.content, "
            "  updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
            new_id("skill_file"),
            skill_id,
            body.path,
            body.content,
            principal,
        )
        await conn.execute("UPDATE skills SET updated_at = now() WHERE id = $1", skill_id)
    return dict(row)


@router.get("/skills/{skill_id}/files")
async def list_files(skill_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        rows = await conn.fetch(
            "SELECT id, skill_id, path, octet_length(content) AS size, updated_by, updated_at "
            "FROM skill_files WHERE skill_id = $1 ORDER BY path",
            skill_id,
        )
    return {"data": [dict(r) for r in rows]}


@router.get("/skills/{skill_id}/files/{file_id}")
async def get_file(skill_id: str, file_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM skill_files WHERE id = $1 AND skill_id = $2", file_id, skill_id
        )
    if row is None:
        raise HTTPException(404, "skill file not found")
    return dict(row)


async def seed_samples(conn: asyncpg.Connection, root: Path = SEED_DIR) -> list[str]:
    """Create-once import of the bundled sample skills.

    A folder is seeded only while no skill — active or archived — has ever
    used its name, so operator edits and archivals are never overridden;
    updates after creation flow through the API like any other skill."""
    if not root.is_dir():
        return []
    seeded: list[str] = []
    for entry in sorted(p for p in root.iterdir() if (p / SKILL_ENTRY).is_file()):
        taken = await conn.fetchval("SELECT 1 FROM skills WHERE name = $1 LIMIT 1", entry.name)
        if taken:
            continue
        try:
            description = _frontmatter_description((entry / SKILL_ENTRY).read_text())
            async with conn.transaction():
                skill_id = new_id("skill")
                await conn.execute(
                    "INSERT INTO skills (id, name, description, created_by) "
                    "VALUES ($1, $2, $3, $4)",
                    skill_id,
                    entry.name,
                    description,
                    SEED_PRINCIPAL,
                )
                for file in sorted(f for f in entry.rglob("*") if f.is_file()):
                    rel = file.relative_to(entry).as_posix()
                    if any(part.startswith(".") for part in file.relative_to(entry).parts):
                        continue
                    try:
                        content = file.read_text()
                    except UnicodeDecodeError:
                        log.warning("%s/%s is not text, not seeded", entry.name, rel)
                        continue
                    if len(content.encode()) > config.MAX_SKILL_FILE_BYTES:
                        log.warning("%s/%s exceeds the file cap, not seeded", entry.name, rel)
                        continue
                    await conn.execute(
                        "INSERT INTO skill_files (id, skill_id, path, content, updated_by) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        new_id("skill_file"),
                        skill_id,
                        rel,
                        content,
                        SEED_PRINCIPAL,
                    )
        except asyncpg.UniqueViolationError:
            continue
        except UnicodeDecodeError:
            log.warning("sample skill %s has a non-text SKILL.md, not seeded", entry.name)
            continue
        seeded.append(entry.name)
    if seeded:
        log.info("seeded sample skills: %s", ", ".join(seeded))
    return seeded


def _frontmatter_description(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return None
        if line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if value in ("|", "|-", ">", ">-"):
                block: list[str] = []
                for cont in lines[i + 1 :]:
                    if cont.strip() == "---" or (cont.strip() and not cont[0].isspace()):
                        break
                    if cont.strip():
                        block.append(cont.strip())
                return " ".join(block) or None
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


@router.delete("/skills/{skill_id}/files/{file_id}")
async def delete_file(skill_id: str, file_id: str, _: str = Depends(principal_of)) -> dict:
    async with db.transaction() as conn:
        result = await conn.execute(
            "DELETE FROM skill_files WHERE id = $1 AND skill_id = $2", file_id, skill_id
        )
    if db.rowcount(result) != 1:
        raise HTTPException(404, "skill file not found")
    return {"id": file_id, "deleted": True}
