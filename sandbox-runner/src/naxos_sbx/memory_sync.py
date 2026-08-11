import hashlib
import logging
from pathlib import Path

from .control import ControlChannel

log = logging.getLogger(__name__)


class MemorySync:
    """Materialise memory stores under ws/memory/{store-name}/ and write
    changes back at checkpoint. Deletion of a materialised file deletes the
    memory; last write wins (versioning is deferred)."""

    def __init__(self, channel: ControlChannel, ws: Path) -> None:
        self.channel = channel
        self.root = ws / "memory"
        self.baseline: dict[str, dict[str, str]] = {}
        self.store_dirs: dict[str, Path] = {}

    async def materialise(self) -> None:
        payload = await self.channel.fetch_memory()
        for store_id, store in payload.get("stores", {}).items():
            directory = self.root / store["name"]
            directory.mkdir(parents=True, exist_ok=True)
            self.store_dirs[store_id] = directory
            self.baseline[store_id] = {}
            for path, content in store["files"].items():
                target = directory / path
                if not target.resolve().is_relative_to(directory.resolve()):
                    log.warning("memory file escapes its store dir, skipped: %s", path)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                self.baseline[store_id][path] = _digest(content)

    async def writeback(self) -> None:
        changes: dict[str, dict[str, str | None]] = {}
        for store_id, directory in self.store_dirs.items():
            seen: set[str] = set()
            files: dict[str, str | None] = {}
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(directory))
                seen.add(rel)
                content = path.read_text(errors="replace")
                if self.baseline[store_id].get(rel) != _digest(content):
                    files[rel] = content
            for rel in set(self.baseline[store_id]) - seen:
                files[rel] = None
            if files:
                changes[store_id] = files
        if changes:
            await self.channel.writeback_memory(changes)
            log.info("memory writeback: %s stores", len(changes))


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
