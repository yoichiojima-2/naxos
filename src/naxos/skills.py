import logging
import re
from dataclasses import dataclass

from naxos.config import BUCKET, ROLES
from naxos.gcs import CloudStorage

logger = logging.getLogger(__name__)

PREFIX = "skills/"
MAX_FILE_BYTES = 1024**2  # matches CloudStorage.max_read_bytes so saved files always round-trip


def check_name(name: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError(f"invalid skill name: {name!r} (lowercase letters, digits, hyphens)")


def check_path(path: str) -> None:
    segments = path.split("/")
    if not all(re.fullmatch(r"[A-Za-z0-9._-]+", s) and s not in (".", "..") for s in segments):
        raise ValueError(f"invalid file path: {path!r}")


@dataclass
class Skills:
    """Editor for the live skill store at gs://$BUCKET/skills.

    The runtime syncs a role's skills into the workspace at run start,
    so edits here apply from the next run — no deploy involved. Name and
    path validation lives in this class, not the API layer, so no caller
    can write outside the skills/ prefix.
    """

    cs: CloudStorage
    bucket: str = BUCKET

    def object_path(self, name: str, path: str) -> str:
        check_name(name)
        check_path(path)
        return f"{PREFIX}{name}/{path}"

    def list(self) -> list[dict]:
        files: dict[str, list[str]] = {name: [] for config in ROLES.values() for name in config["skills"]}
        for obj in self.cs.list_all(self.bucket, PREFIX):
            name, _, path = obj.removeprefix(PREFIX).partition("/")
            if name and path:
                files.setdefault(name, []).append(path)
        return [
            {
                "name": name,
                "files": sorted(paths),
                "roles": sorted(role for role, config in ROLES.items() if name in config["skills"]),
            }
            for name, paths in sorted(files.items())
        ]

    def read(self, name: str, path: str) -> str:
        return self.cs.read_text(self.bucket, self.object_path(name, path))

    def write(self, name: str, path: str, content: str) -> None:
        target = self.object_path(name, path)
        if len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError(f"file too large: max {MAX_FILE_BYTES} bytes")
        self.cs.write_text(self.bucket, target, content)
        logger.info(f"skill file saved: gs://{self.bucket}/{target}")

    def delete_file(self, name: str, path: str) -> None:
        target = self.object_path(name, path)
        self.cs.delete_object(self.bucket, target)
        logger.info(f"skill file deleted: gs://{self.bucket}/{target}")

    def delete(self, name: str) -> int:
        check_name(name)
        paths = self.cs.list_all(self.bucket, f"{PREFIX}{name}/")
        if not paths:
            raise FileNotFoundError(f"skill not found: {name}")
        for path in paths:
            self.cs.delete_object(self.bucket, path)
        logger.info(f"skill deleted: {name} ({len(paths)} files)")
        return len(paths)
