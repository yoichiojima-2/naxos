from naxos_sbx.skills_sync import SkillsSync


class _Channel:
    def __init__(self, skills):
        self.skills = skills

    async def fetch_skills(self):
        return {"skills": self.skills}


async def test_materialise_writes_skill_tree(tmp_path):
    channel = _Channel(
        {
            "skill_x": {
                "name": "deploy-helper",
                "files": {"SKILL.md": "Use me.", "scripts/run.sh": "echo deploy"},
            }
        }
    )
    names = await SkillsSync(channel, tmp_path).materialise()
    assert names == ["deploy-helper"]
    root = tmp_path / ".claude" / "skills" / "deploy-helper"
    assert (root / "SKILL.md").read_text() == "Use me."
    assert (root / "scripts" / "run.sh").read_text() == "echo deploy"


async def test_materialise_replaces_stale_skills(tmp_path):
    stale = tmp_path / ".claude" / "skills" / "old-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale")

    names = await SkillsSync(
        _Channel({"skill_x": {"name": "fresh", "files": {"SKILL.md": "new"}}}), tmp_path
    ).materialise()
    assert names == ["fresh"]
    assert not stale.exists()
    assert (tmp_path / ".claude" / "skills" / "fresh" / "SKILL.md").read_text() == "new"


async def test_materialise_with_no_skills_clears_the_tree(tmp_path):
    stale = tmp_path / ".claude" / "skills" / "old-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale")

    assert await SkillsSync(_Channel({}), tmp_path).materialise() == []
    assert not (tmp_path / ".claude" / "skills").exists()
