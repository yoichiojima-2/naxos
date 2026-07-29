from unittest.mock import Mock

import pytest

from naxos.skills import Skills


def make(objects: list[str] | None = None) -> Skills:
    cs = Mock()
    cs.list_all.return_value = objects or []
    return Skills(cs, bucket="b")


def test_list_groups_files_by_skill_and_maps_roles():
    skills = make(
        [
            "skills/bigquery/SKILL.md",
            "skills/bigquery/references/tables.md",
            "skills/custom/SKILL.md",
        ]
    )

    result = skills.list()

    by_name = {s["name"]: s for s in result}
    assert by_name["bigquery"]["files"] == ["SKILL.md", "references/tables.md"]
    assert by_name["bigquery"]["roles"] == ["analyst", "ops"]
    assert by_name["custom"]["roles"] == []
    assert skills.cs.list_all.call_args.args == ("b", "skills/")


def test_list_includes_configured_skill_with_no_files():
    result = make([]).list()

    by_name = {s["name"]: s for s in result}
    assert by_name["cloud-storage"]["files"] == []
    assert by_name["cloud-storage"]["roles"] == ["ops"]


def test_list_ignores_prefix_placeholder_objects():
    result = make(["skills/bigquery/"]).list()

    by_name = {s["name"]: s for s in result}
    assert by_name["bigquery"]["files"] == []


def test_read():
    skills = make()
    skills.cs.read_text.return_value = "content"

    assert skills.read("bigquery", "SKILL.md") == "content"
    assert skills.cs.read_text.call_args.args == ("b", "skills/bigquery/SKILL.md")


def test_write():
    skills = make()

    skills.write("bigquery", "references/tables.md", "hello")

    assert skills.cs.write_text.call_args.args == ("b", "skills/bigquery/references/tables.md", "hello")


@pytest.mark.parametrize("name", ["", "UPPER", "has space", "a/b", "..", "-leading"])
def test_write_rejects_bad_name(name):
    with pytest.raises(ValueError, match="invalid skill name"):
        make().write(name, "SKILL.md", "x")


@pytest.mark.parametrize("path", ["", "/abs", "a//b", "../up", "a/../b", "a/.", "."])
def test_write_rejects_bad_path(path):
    with pytest.raises(ValueError, match="invalid file path"):
        make().write("bigquery", path, "x")


def test_write_rejects_oversized_content():
    with pytest.raises(ValueError, match="too large"):
        make().write("bigquery", "SKILL.md", "x" * (1024**2 + 1))


def test_delete_file():
    skills = make()

    skills.delete_file("bigquery", "SKILL.md")

    assert skills.cs.delete_object.call_args.args == ("b", "skills/bigquery/SKILL.md")


def test_delete_skill_removes_all_files():
    skills = make(["skills/custom/SKILL.md", "skills/custom/notes.md"])

    assert skills.delete("custom") == 2
    assert skills.cs.list_all.call_args.args == ("b", "skills/custom/")
    deleted = [c.args for c in skills.cs.delete_object.call_args_list]
    assert deleted == [("b", "skills/custom/SKILL.md"), ("b", "skills/custom/notes.md")]


def test_delete_missing_skill_raises():
    with pytest.raises(FileNotFoundError):
        make([]).delete("ghost")
