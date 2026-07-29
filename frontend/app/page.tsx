"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Message = { who: "you" | "agent"; text: string; meta?: string };

type Run = {
  run_id: string;
  session_id: string | null;
  role: string | null;
  principal: string | null;
  started_at: string;
  prompt: string;
  text: string;
  num_turns: number | null;
  cost_usd: number | null;
  is_error: boolean;
};

type Artifact = {
  role: string;
  date: string;
  title: string;
  url: string;
  files: number;
};

type Schedule = {
  id: string;
  name: string;
  role: string;
  cron: string;
  prompt: string;
  paused: boolean;
  next_run: string | null;
};

type ScheduleForm = {
  id?: string;
  name: string;
  role: string;
  cron: string;
  prompt: string;
  paused: boolean;
};

type Skill = {
  name: string;
  files: string[];
  roles: string[];
};

type SkillEditor = {
  skill: string;
  path: string;
  content: string;
  isNew: boolean;
  nameLocked: boolean;
};

const SKILL_TEMPLATE = `---
name: my-skill
description: when should the agent reach for this skill?
---

# my-skill
`;

function splitFrontmatter(content: string): { meta: string | null; body: string } {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/);
  return match ? { meta: match[1], body: content.slice(match[0].length) } : { meta: null, body: content };
}

async function detailOf(response: Response): Promise<string> {
  const text = await response.text();
  try {
    return JSON.parse(text).detail ?? (text || response.statusText);
  } catch {
    return text || response.statusText;
  }
}

export default function Page() {
  const [tab, setTab] = useState<"chat" | "history" | "schedules" | "skills" | "artifacts">("chat");
  const [roles, setRoles] = useState<string[]>([]);
  const [role, setRole] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [me, setMe] = useState("");
  const [status, setStatus] = useState("");
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [proposal, setProposal] = useState<ScheduleForm | null>(null);
  const [form, setForm] = useState<ScheduleForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState("");
  const [ranNow, setRanNow] = useState("");
  const [scheduleError, setScheduleError] = useState("");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillEditor, setSkillEditor] = useState<SkillEditor | null>(null);
  const [skillPreview, setSkillPreview] = useState<{ skill: string; path: string; content: string } | null>(null);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillError, setSkillError] = useState("");
  const [confirmSkillDelete, setConfirmSkillDelete] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/roles")
      .then((r) => r.json())
      .then((list) => {
        setRoles(list);
        if (list.length) setRole(list[0]);
      })
      .catch(() => setRoles([]));
    fetch("/api/me")
      .then((r) => r.json())
      .then((data) => setMe(data.email))
      .catch(() => setMe(""));
  }, []);

  async function loadRuns() {
    try {
      setRuns(await (await fetch("/api/runs")).json());
    } catch {
      setRuns([]);
    }
  }

  async function loadArtifacts() {
    try {
      setArtifacts(await (await fetch("/api/artifacts")).json());
    } catch {
      setArtifacts([]);
    }
  }

  async function loadSchedules() {
    setRanNow("");
    try {
      setSchedules(await (await fetch("/api/schedules")).json());
    } catch {
      setSchedules([]);
    }
  }

  async function loadSkills() {
    setConfirmSkillDelete("");
    setSkillPreview(null);
    try {
      setSkills(await (await fetch("/api/skills")).json());
    } catch {
      setSkills([]);
    }
  }

  useEffect(() => {
    if (tab === "history" && runs.length === 0) loadRuns();
    if (tab === "schedules") loadSchedules();
    if (tab === "skills") loadSkills();
    if (tab === "artifacts") loadArtifacts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function statusLabel(event: { event: string; name?: string }): string {
    if (event.event === "tool") return `using ${event.name?.split("__").pop()}…`;
    if (event.event === "proposal") return "proposing a schedule…";
    if (event.event === "text") return "writing…";
    return "thinking…";
  }

  async function send() {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    setMessages((m) => [...m, { who: "you", text: prompt }]);
    setBusy(true);
    setStatus("starting…");
    try {
      const response = await fetch("/api/run/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, role, resume: sessionId }),
      });
      if (!response.ok || !response.body) {
        const detail = (await response.json()).detail ?? response.statusText;
        setMessages((m) => [...m, { who: "agent", text: `error: ${detail}` }]);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let final = "";
      let meta = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.event === "ping") continue;
          if (event.event === "result") {
            setSessionId(event.session_id);
            const cost = event.cost_usd != null ? `$${event.cost_usd.toFixed(4)}` : "";
            final = event.text;
            meta = `${cost} · ${event.num_turns} turns`;
          } else if (event.event === "error") {
            final = `error: ${event.detail}`;
          } else {
            if (event.event === "proposal" && event.kind === "schedule") {
              setProposal({
                name: event.name ?? "",
                role: event.role,
                cron: event.cron,
                prompt: event.prompt,
                paused: false,
              });
            }
            setStatus(statusLabel(event));
          }
        }
      }
      setMessages((m) => [...m, { who: "agent", text: final, meta: meta || undefined }]);
    } catch (e) {
      const text =
        e instanceof TypeError
          ? "connection lost — the run continues on the server; reopen it from History once it finishes"
          : `error: ${e}`;
      setMessages((m) => [...m, { who: "agent", text }]);
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  function openSession(run: Run) {
    if (run.role) setRole(run.role);
    setSessionId(run.session_id);
    setMessages([
      { who: "you", text: run.prompt },
      { who: "agent", text: run.text, meta: "from history" },
    ]);
    setTab("chat");
  }

  function newChat() {
    setSessionId(null);
    setMessages([]);
    setProposal(null);
  }

  function reviewProposal() {
    if (!proposal) return;
    setForm(proposal);
    setProposal(null);
    setScheduleError("");
    setTab("schedules");
  }

  async function saveForm() {
    if (!form) return;
    setSaving(true);
    setScheduleError("");
    try {
      const response = await fetch(form.id ? `/api/schedules/${form.id}` : "/api/schedules", {
        method: form.id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          role: form.role,
          cron: form.cron,
          prompt: form.prompt,
          paused: form.paused,
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        let detail = text || response.statusText;
        try {
          detail = JSON.parse(text).detail ?? detail;
        } catch {}
        setScheduleError(detail);
        return;
      }
      setForm(null);
      await loadSchedules();
    } catch (e) {
      setScheduleError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function deleteSchedule(schedule: Schedule) {
    if (confirmDelete !== schedule.id) {
      setConfirmDelete(schedule.id);
      return;
    }
    setConfirmDelete("");
    setScheduleError("");
    try {
      const response = await fetch(`/api/schedules/${schedule.id}`, { method: "DELETE" });
      if (!response.ok) setScheduleError(await response.text());
      await loadSchedules();
    } catch (e) {
      setScheduleError(String(e));
    }
  }

  async function runSchedule(schedule: Schedule) {
    setScheduleError("");
    try {
      const response = await fetch(`/api/schedules/${schedule.id}/run`, { method: "POST" });
      if (!response.ok) {
        setScheduleError(await response.text());
        return;
      }
      setRanNow(schedule.id);
    } catch (e) {
      setScheduleError(String(e));
    }
  }

  function editForm(patch: Partial<ScheduleForm>) {
    setForm((f) => (f ? { ...f, ...patch } : f));
  }

  function editSkill(patch: Partial<SkillEditor>) {
    setSkillEditor((e) => (e ? { ...e, ...patch } : e));
  }

  async function openSkillFile(name: string, path: string) {
    setSkillError("");
    setConfirmSkillDelete("");
    setSkillPreview(null);
    try {
      const response = await fetch(`/api/skills/${name}/files/${path}`);
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      const data = await response.json();
      setSkillEditor({ skill: name, path, content: data.content, isNew: false, nameLocked: true });
    } catch (e) {
      setSkillError(String(e));
    }
  }

  async function previewSkillFile(name: string, path: string) {
    if (skillPreview?.skill === name && skillPreview.path === path) {
      setSkillPreview(null);
      return;
    }
    setSkillError("");
    setConfirmSkillDelete("");
    try {
      const response = await fetch(`/api/skills/${name}/files/${path}`);
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      const data = await response.json();
      setSkillPreview({ skill: name, path, content: data.content });
    } catch (e) {
      setSkillError(String(e));
    }
  }

  async function saveSkillFile() {
    if (!skillEditor) return;
    setSkillSaving(true);
    setSkillError("");
    try {
      const response = await fetch(`/api/skills/${skillEditor.skill.trim()}/files/${skillEditor.path.trim()}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: skillEditor.content }),
      });
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      setSkillEditor(null);
      await loadSkills();
    } catch (e) {
      setSkillError(String(e));
    } finally {
      setSkillSaving(false);
    }
  }

  async function deleteSkillFile() {
    if (!skillEditor || skillEditor.isNew) return;
    const key = `file:${skillEditor.skill}/${skillEditor.path}`;
    if (confirmSkillDelete !== key) {
      setConfirmSkillDelete(key);
      return;
    }
    setConfirmSkillDelete("");
    setSkillError("");
    try {
      const response = await fetch(`/api/skills/${skillEditor.skill}/files/${skillEditor.path}`, { method: "DELETE" });
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      setSkillEditor(null);
      await loadSkills();
    } catch (e) {
      setSkillError(String(e));
    }
  }

  async function deleteSkill(skill: Skill) {
    const key = `skill:${skill.name}`;
    if (confirmSkillDelete !== key) {
      setConfirmSkillDelete(key);
      return;
    }
    setConfirmSkillDelete("");
    setSkillError("");
    try {
      const response = await fetch(`/api/skills/${skill.name}`, { method: "DELETE" });
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      if (skillEditor?.skill === skill.name) setSkillEditor(null);
      await loadSkills();
    } catch (e) {
      setSkillError(String(e));
    }
  }

  const freshChat = messages.length === 0 && !busy;

  return (
    <main>
      <header>
        <h1>
          <svg width="13" height="16" viewBox="0 0 13 16" fill="none" strokeWidth="2" aria-hidden="true">
            <path d="M2 16V1h9v15" />
          </svg>
          naxos
        </h1>
        <nav>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            chat
          </button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
            history
          </button>
          <button className={tab === "schedules" ? "active" : ""} onClick={() => setTab("schedules")}>
            schedules
          </button>
          <button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>
            skills
          </button>
          <button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")}>
            artifacts
          </button>
        </nav>
        <div className="controls">
          {me && <span className="me">{me}</span>}
          {!(tab === "chat" && freshChat) && (
            <>
              <select value={role} onChange={(e) => setRole(e.target.value)} disabled={sessionId != null}>
                {roles.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              <button onClick={newChat}>new chat</button>
            </>
          )}
        </div>
      </header>

      {tab === "chat" && freshChat && (
        <section className="hero">
          <div className="hero-card">
            <p className="hero-kicker">new session</p>
            <h2>what should {role || "the agent"} work on?</h2>
            <div className="hero-roles" role="radiogroup" aria-label="role">
              {roles.map((r) => (
                <button
                  key={r}
                  type="button"
                  className={r === role ? "active" : ""}
                  aria-pressed={r === role}
                  onClick={() => setRole(r)}
                >
                  {r}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder={`describe the task for ${role || "the agent"}…`}
                rows={5}
                autoFocus
              />
              <div className="hero-foot">
                <span className="hint">enter to send · shift+enter for a new line</span>
                <button type="submit" disabled={!input.trim()}>
                  start
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {tab === "chat" && !freshChat && (
        <section className="chat">
          <div className="messages">
            {messages.map((message, i) => (
              <div key={i} className={`message ${message.who}`}>
                {message.who === "agent" ? (
                  <div className="md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
                  </div>
                ) : (
                  <pre>{message.text}</pre>
                )}
                {message.meta && <span className="meta">{message.meta}</span>}
              </div>
            ))}
            {busy && (
              <div className="message agent thinking">
                <span />
                <span />
                <span />
                <em className="status">{status}</em>
              </div>
            )}
            <div ref={bottom} />
          </div>
          {proposal && (
            <div className="proposal">
              <span>
                proposed: <strong>{proposal.name || "scheduled task"}</strong> — {proposal.role} ·{" "}
                <code>{proposal.cron}</code>
              </span>
              <button onClick={reviewProposal}>review &amp; save</button>
              <button onClick={() => setProposal(null)}>dismiss</button>
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={sessionId ? "continue this session…" : "new session…"}
              disabled={busy}
            />
            <button type="submit" disabled={busy}>
              send
            </button>
          </form>
          {sessionId && <p className="session">session {sessionId}</p>}
        </section>
      )}

      {tab === "schedules" && (
        <section className="schedules">
          <div className="section-head">
            <p className="hint">
              scheduled tasks run the role unattended (cron in Asia/Tokyo). you can also ask the agent in chat to
              propose one.
            </p>
            {!form && (
              <button
                className="primary"
                onClick={() => setForm({ name: "", role, cron: "0 9 * * *", prompt: "", paused: true })}
              >
                new task
              </button>
            )}
          </div>
          {scheduleError && <p className="schedule-error">{scheduleError}</p>}
          {form && (
            <div className="schedule form">
              <div className="schedule-head">
                <strong>{form.id ? "edit task" : "new task"}</strong>
                <label>
                  <input type="checkbox" checked={form.paused} onChange={(e) => editForm({ paused: e.target.checked })} />
                  paused
                </label>
                <div className="schedule-actions">
                  <button onClick={() => setForm(null)}>cancel</button>
                  <button className="primary" onClick={saveForm} disabled={saving || !form.name || !form.prompt}>
                    {saving ? "saving…" : "save"}
                  </button>
                </div>
              </div>
              <div className="schedule-fields">
                <input
                  value={form.name}
                  onChange={(e) => editForm({ name: e.target.value })}
                  placeholder="task name (e.g. daily cost report)"
                />
                <select value={form.role} onChange={(e) => editForm({ role: e.target.value })} disabled={!!form.id}>
                  {roles.map((r) => (
                    <option key={r}>{r}</option>
                  ))}
                </select>
                <input
                  className="cron"
                  value={form.cron}
                  onChange={(e) => editForm({ cron: e.target.value })}
                  placeholder="0 9 * * *"
                />
              </div>
              <textarea
                value={form.prompt}
                onChange={(e) => editForm({ prompt: e.target.value })}
                placeholder="what should the agent do on each run?"
                rows={3}
              />
            </div>
          )}
          {schedules.length === 0 && !form && <p className="empty">no scheduled tasks yet</p>}
          {schedules.map((schedule) => (
            <div key={schedule.id} className="schedule">
              <div className="schedule-head">
                <strong>{schedule.name}</strong>
                <span className="chip">{schedule.role}</span>
                <span className={`chip ${schedule.paused ? "paused" : "active"}`}>
                  {schedule.paused ? "paused" : "active"}
                </span>
                <div className="schedule-actions">
                  <button
                    onClick={() => runSchedule(schedule)}
                    disabled={schedule.paused || ranNow === schedule.id}
                    title={schedule.paused ? "paused tasks can't be run — resume it first" : undefined}
                  >
                    {ranNow === schedule.id ? "started — see history" : "run now"}
                  </button>
                  <button onClick={() => deleteSchedule(schedule)}>
                    {confirmDelete === schedule.id ? "confirm delete?" : "delete"}
                  </button>
                  <button
                    onClick={() => {
                      setForm({ ...schedule });
                      setConfirmDelete("");
                    }}
                  >
                    edit
                  </button>
                </div>
              </div>
              <p className="schedule-when">
                <code>{schedule.cron}</code>
                {schedule.next_run && <span> · next run {new Date(schedule.next_run).toLocaleString()}</span>}
              </p>
              <p className="schedule-prompt">{schedule.prompt}</p>
            </div>
          ))}
        </section>
      )}

      {tab === "skills" && (
        <section className="skills">
          <div className="section-head">
            <p className="hint">
              skills are shared know-how synced into the agent workspace at run start — edits apply from the next run.
              which roles load a skill is set in roles.json.
            </p>
            {!skillEditor && (
              <button
                className="primary"
                onClick={() => {
                  setSkillError("");
                  setSkillEditor({ skill: "", path: "SKILL.md", content: SKILL_TEMPLATE, isNew: true, nameLocked: false });
                }}
              >
                new skill
              </button>
            )}
          </div>
          {skillError && <p className="schedule-error">{skillError}</p>}
          {skillEditor && (
            <div className="schedule form">
              <div className="schedule-head">
                <strong>
                  {skillEditor.isNew
                    ? skillEditor.nameLocked
                      ? `new file in ${skillEditor.skill}`
                      : "new skill"
                    : `${skillEditor.skill}/${skillEditor.path}`}
                </strong>
                <div className="schedule-actions">
                  {!skillEditor.isNew && (
                    <button onClick={deleteSkillFile}>
                      {confirmSkillDelete === `file:${skillEditor.skill}/${skillEditor.path}`
                        ? "confirm delete?"
                        : "delete file"}
                    </button>
                  )}
                  <button onClick={() => setSkillEditor(null)}>cancel</button>
                  <button
                    className="primary"
                    onClick={saveSkillFile}
                    disabled={skillSaving || !skillEditor.skill.trim() || !skillEditor.path.trim()}
                  >
                    {skillSaving ? "saving…" : "save"}
                  </button>
                </div>
              </div>
              {skillEditor.isNew && (
                <div className="schedule-fields">
                  <input
                    value={skillEditor.skill}
                    onChange={(e) => editSkill({ skill: e.target.value })}
                    placeholder="skill name (e.g. incident-triage)"
                    disabled={skillEditor.nameLocked}
                  />
                  <input
                    className="path"
                    value={skillEditor.path}
                    onChange={(e) => editSkill({ path: e.target.value })}
                    placeholder="SKILL.md"
                  />
                </div>
              )}
              <textarea
                className="skill-content"
                value={skillEditor.content}
                onChange={(e) => editSkill({ content: e.target.value })}
                rows={16}
              />
            </div>
          )}
          {skills.length === 0 && !skillEditor && <p className="empty">no skills yet</p>}
          {skills.map((skill) => (
            <div key={skill.name} className="schedule">
              <div className="schedule-head">
                <strong>{skill.name}</strong>
                {skill.roles.map((r) => (
                  <span key={r} className="chip">
                    {r}
                  </span>
                ))}
                {skill.roles.length === 0 && <span className="chip">no roles</span>}
                <div className="schedule-actions">
                  <button
                    onClick={() => {
                      setSkillError("");
                      setSkillEditor({ skill: skill.name, path: "", content: "", isNew: true, nameLocked: true });
                    }}
                  >
                    add file
                  </button>
                  <button onClick={() => deleteSkill(skill)}>
                    {confirmSkillDelete === `skill:${skill.name}` ? "confirm delete?" : "delete"}
                  </button>
                </div>
              </div>
              <div className="skill-files">
                {skill.files.length === 0 && <span className="hint">no files yet — add SKILL.md</span>}
                {skill.files.map((file) => (
                  <button
                    key={file}
                    className={
                      skillPreview?.skill === skill.name && skillPreview.path === file ? "active" : ""
                    }
                    onClick={() => previewSkillFile(skill.name, file)}
                  >
                    {file}
                  </button>
                ))}
              </div>
              {skillPreview?.skill === skill.name && (
                <div className="skill-preview">
                  <div className="skill-preview-head">
                    <code>{skillPreview.path}</code>
                    <button onClick={() => openSkillFile(skillPreview.skill, skillPreview.path)}>edit</button>
                  </div>
                  {(() => {
                    const { meta, body } = splitFrontmatter(skillPreview.content);
                    return (
                      <>
                        {meta && <pre className="frontmatter">{meta}</pre>}
                        <div className="md">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
                        </div>
                      </>
                    );
                  })()}
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {tab === "history" && (
        <section className="history">
          <button className="refresh" onClick={loadRuns}>
            refresh
          </button>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>started</th>
                  <th>role</th>
                  <th>principal</th>
                  <th>prompt</th>
                  <th>cost</th>
                  <th>turns</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan={6}>no runs yet</td>
                  </tr>
                )}
                {runs.map((run) => (
                  <tr key={run.run_id} className={run.is_error ? "error" : ""} onClick={() => openSession(run)}>
                    <td>{run.started_at?.replace("T", " ").slice(0, 16)}</td>
                    <td>{run.role ?? "-"}</td>
                    <td>{run.principal ?? "-"}</td>
                    <td className="prompt">{run.prompt}</td>
                    <td>{run.cost_usd != null ? `$${run.cost_usd.toFixed(3)}` : "-"}</td>
                    <td>{run.num_turns ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === "artifacts" && (
        <section className="artifacts">
          <button className="refresh" onClick={loadArtifacts}>
            refresh
          </button>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>published</th>
                  <th>title</th>
                  <th>role</th>
                  <th>files</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan={4}>no artifacts yet — ask an agent to publish one</td>
                  </tr>
                )}
                {artifacts.map((artifact) => (
                  <tr key={artifact.url} onClick={() => window.open(artifact.url, "_blank", "noopener")}>
                    <td>{artifact.date}</td>
                    <td className="title">{artifact.title}</td>
                    <td>{artifact.role}</td>
                    <td>{artifact.files}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">artifacts open in a new tab; published artifacts are immutable</p>
        </section>
      )}
    </main>
  );
}
