import json

from naxos_sbx.skills_sync import SkillsSync


class _Channel:
    def __init__(self, skills):
        self.skills = skills

    async def fetch_skills(self):
        return {"skills": self.skills}


async def test_materialise_writes_a_local_plugin(tmp_path):
    channel = _Channel(
        {
            "skill_x": {
                "name": "deploy-helper",
                "files": {"SKILL.md": "Use me.", "scripts/run.sh": "echo deploy"},
            }
        }
    )
    root = tmp_path / "skills-plugin"
    names = await SkillsSync(channel, root).materialise()
    assert names == ["deploy-helper"]
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "naxos"
    skill = root / "skills" / "deploy-helper"
    assert (skill / "SKILL.md").read_text() == "Use me."
    assert (skill / "scripts" / "run.sh").read_text() == "echo deploy"


async def test_refresh_discards_agent_edits(tmp_path):
    root = tmp_path / "skills-plugin"
    sync = SkillsSync(_Channel({"skill_x": {"name": "fresh", "files": {"SKILL.md": "new"}}}), root)
    await sync.materialise()

    tampered = root / "skills" / "fresh" / "SKILL.md"
    tampered.write_text("tampered")
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text("{}")

    assert sync.refresh() == ["fresh"]
    assert tampered.read_text() == "new"
    assert not (root / "hooks").exists()


async def test_materialise_with_no_skills_clears_the_tree(tmp_path):
    root = tmp_path / "skills-plugin"
    stale = root / "skills" / "old-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale")

    assert await SkillsSync(_Channel({}), root).materialise() == []
    assert not root.exists()


async def test_materialise_skips_files_escaping_the_skill_dir(tmp_path):
    root = tmp_path / "skills-plugin"
    channel = _Channel(
        {
            "skill_x": {
                "name": "sneaky",
                "files": {
                    "SKILL.md": "ok",
                    "/etc/evil": "nope",
                    "a/../../hooks/hooks.json": "nope",
                },
            }
        }
    )
    assert await SkillsSync(channel, root).materialise() == ["sneaky"]
    assert (root / "skills" / "sneaky" / "SKILL.md").read_text() == "ok"
    assert not (root / "skills" / "hooks").exists()
    assert not (root / "hooks").exists()
    assert list((root / "skills" / "sneaky").rglob("*")) == [
        root / "skills" / "sneaky" / "SKILL.md"
    ]
