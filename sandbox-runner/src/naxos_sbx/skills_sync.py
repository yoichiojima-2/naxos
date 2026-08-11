import logging
import shutil
from pathlib import Path

from .control import ControlChannel

log = logging.getLogger(__name__)


class SkillsSync:
    """Materialise the session's skills under ws/.claude/skills/{name}/ where
    the SDK discovers them. Skills are read-only from the sandbox: the tree is
    rebuilt from the control plane on every wake, so agent edits are discarded
    and there is no writeback path an injected agent could poison."""

    def __init__(self, channel: ControlChannel, ws: Path) -> None:
        self.channel = channel
        self.root = ws / ".claude" / "skills"

    async def materialise(self) -> list[str]:
        payload = await self.channel.fetch_skills()
        if self.root.exists():
            shutil.rmtree(self.root)
        names: list[str] = []
        for skill in payload.get("skills", {}).values():
            directory = self.root / skill["name"]
            for path, content in skill["files"].items():
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            names.append(skill["name"])
        if names:
            log.info("materialised skills: %s", ", ".join(sorted(names)))
        return names
