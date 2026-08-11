"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiConfirm, listFor, Skill, SkillFile } from "@/lib/api";
import { BackIcon } from "@/components/icons";
import FavoriteStar, { FavoriteProps, useFavoriteFilter } from "@/components/favorite-star";
import CountHeader from "@/components/list-header";
import FileList from "@/components/file-list";

const SKILL_MD_TEMPLATE = (name: string) =>
  `---\nname: ${name}\ndescription: When and how to use this skill.\n---\n\nInstructions the agent loads when it uses this skill.\n`;

const byPath = (a: SkillFile, b: SkillFile) =>
  Number(b.path === "SKILL.md") - Number(a.path === "SKILL.md") || a.path.localeCompare(b.path);

export default function Skills({ favorites, onToggleFavorite }: FavoriteProps) {
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [files, setFiles] = useState<Record<string, SkillFile[]>>({});
  const [skillName, setSkillName] = useState("");
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<
    { skillId: string; path: string; content: string; isNew: boolean } | null
  >(null);
  const opened = useRef<Set<string>>(new Set());
  const favFilter = useFavoriteFilter("skill", skills ?? [], favorites);

  // One request per skill on load costs a round trip each for lists nobody has
  // opened; load a skill's files the first time it is expanded instead, and
  // reload only those on refresh.
  const loadFiles = useCallback(async (ids: string[]) => {
    if (!ids.length) return;
    ids.forEach((id) => opened.current.add(id));
    const loaded = await listFor<SkillFile>(ids, (id) => `/v1/skills/${id}/files`);
    setFiles((prev) => ({ ...prev, ...loaded }));
  }, []);

  const refresh = useCallback(async () => {
    const result = await api<{ data: Skill[] }>("/v1/skills");
    setSkills(result.data);
    await loadFiles(
      [...opened.current].filter((id) => result.data.some((s) => s.id === id)),
    );
  }, [loadFiles]);

  useEffect(() => { refresh(); }, [refresh]);

  async function createSkill() {
    await api("/v1/skills", { json: { name: skillName, description: description || null } });
    setSkillName("");
    setDescription("");
    refresh();
  }

  async function openFile(skillId: string, file: SkillFile) {
    const full = await api<SkillFile>(`/v1/skills/${skillId}/files/${file.id}`);
    setEditing({ skillId, path: full.path, content: full.content ?? "", isNew: false });
  }

  async function save() {
    if (!editing) return;
    const clash =
      editing.isNew && (files[editing.skillId] ?? []).some((f) => f.path === editing.path);
    if (clash && !window.confirm(`"${editing.path}" already exists. Overwrite it?`)) return;
    await api(`/v1/skills/${editing.skillId}/files`, {
      json: { path: editing.path, content: editing.content },
    });
    setEditing(null);
    refresh();
  }

  async function deleteFile(skillId: string, file: SkillFile) {
    if (
      await apiConfirm(
        `Delete "${file.path}"?`,
        `/v1/skills/${skillId}/files/${file.id}`,
        { method: "DELETE" },
      )
    ) {
      refresh();
    }
  }

  async function archiveSkill(skill: Skill) {
    if (
      await apiConfirm(
        `Archive "${skill.name}"? Agents referencing it will stop loading it.`,
        `/v1/skills/${skill.id}/archive`,
      )
    ) {
      refresh();
    }
  }

  if (editing) {
    return (
      <div className="panel">
        <div className="row between mb12">
          <div className="row">
            <button
              className="ghost flex-inline"
              onClick={() => setEditing(null)}
              aria-label="back"
            >
              <BackIcon />
            </button>
            {editing.isNew ? (
              <input
                className="mono"
                value={editing.path}
                style={{ width: 260 }}
                onChange={(e) => setEditing({ ...editing, path: e.target.value })}
              />
            ) : (
              <span className="mono">{editing.path}</span>
            )}
          </div>
          <button
            className="primary"
            onClick={save}
            disabled={!/^[a-zA-Z0-9._/-]{1,200}$/.test(editing.path)}
          >
            Save
          </button>
        </div>
        <textarea
          style={{ minHeight: 360 }}
          value={editing.content}
          onChange={(e) => setEditing({ ...editing, content: e.target.value })}
        />
      </div>
    );
  }

  return (
    <>
      <CountHeader count={skills === null ? null : skills.length} noun="skill">
        {favFilter.chip}
        <input
          placeholder="skill-name (lowercase, dashes)"
          value={skillName}
          onChange={(e) => setSkillName(e.target.value)}
          style={{ width: 220 }}
        />
        <input
          placeholder="description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ width: 260 }}
        />
        <button
          className="primary"
          onClick={createSkill}
          disabled={!/^[a-z0-9][a-z0-9-]{0,63}$/.test(skillName)}
        >
          Create
        </button>
      </CountHeader>
      {skills === null && <div className="panel"><span className="muted">loading…</span></div>}
      {skills?.length === 0 && (
        <div className="panel">
          <span className="muted">no skills yet — create one above to share know-how across agents.</span>
        </div>
      )}
      {favFilter.apply(skills ?? []).map((skill) => (
        <div className="panel" key={skill.id}>
          <div className="row between">
            <div className="row">
              <FavoriteStar
                type="skill"
                id={skill.id}
                favorites={favorites}
                onToggleFavorite={onToggleFavorite}
              />
              <strong>{skill.name}</strong>
              {!skill.ready && (
                <span className="muted" title="Add a SKILL.md file so agents can load this skill">
                  needs SKILL.md
                </span>
              )}
            </div>
            <div className="row">
              <button
                className="ghost"
                onClick={() => {
                  const hasEntry = (files[skill.id] ?? []).some((f) => f.path === "SKILL.md");
                  setEditing({
                    skillId: skill.id,
                    path: hasEntry ? "reference.md" : "SKILL.md",
                    content: hasEntry ? "" : SKILL_MD_TEMPLATE(skill.name),
                    isNew: true,
                  });
                }}
              >
                New file
              </button>
              <button className="danger" onClick={() => archiveSkill(skill)}>Archive</button>
            </div>
          </div>
          {skill.description && <p className="muted">{skill.description}</p>}
          {!!skill.file_count && (
            <FileList
              count={skill.file_count}
              onOpen={() => { if (!files[skill.id]) loadFiles([skill.id]); }}
            >
              <table>
                <tbody>
                  {[...(files[skill.id] ?? [])].sort(byPath).map((file) => (
                    <tr key={file.id} className="click" onClick={() => openFile(skill.id, file)}>
                      <td className="mono">{file.path}</td>
                      <td className="muted ta-right">{file.size} B</td>
                      <td className="ta-right" style={{ width: 1 }}>
                        <button
                          className="danger"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteFile(skill.id, file);
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </FileList>
          )}
        </div>
      ))}
    </>
  );
}
