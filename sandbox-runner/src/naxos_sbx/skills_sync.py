import json
import logging
import shutil
from pathlib import Path

from .control import ControlChannel

PLUGIN_NAME = "naxos"

log = logging.getLogger(__name__)


class SkillsSync:
    """Materialise the session's skills as a local Claude Code plugin.

    The plugin lives outside the checkpointed workspace and is rebuilt from
    the control plane on every wake — and refreshed from the same payload
    before every SDK turn — so skills are read-only from the sandbox: agent
    edits never take effect and never persist, and there is no writeback path
    an injected agent could poison."""

    def __init__(self, channel: ControlChannel, root: Path) -> None:
        self.channel = channel
        self.root = root
        self._payload: dict = {}

    async def materialise(self) -> list[str]:
        self._payload = await self.channel.fetch_skills()
        names = self.refresh()
        if names:
            log.info("materialised skills: %s", ", ".join(names))
        return names

    def refresh(self) -> list[str]:
        if self.root.exists():
            shutil.rmtree(self.root)
        skills = self._payload.get("skills", {})
        if not skills:
            return []
        manifest = self.root / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"name": PLUGIN_NAME}))
        names: list[str] = []
        for skill in skills.values():
            directory = self.root / "skills" / skill["name"]
            for path, content in skill["files"].items():
                target = directory / path
                if not target.resolve().is_relative_to(directory.resolve()):
                    log.warning("skill file escapes its skill dir, skipped: %s", path)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            names.append(skill["name"])
        return sorted(names)
