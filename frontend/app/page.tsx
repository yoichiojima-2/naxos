"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
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
  viewing: boolean;
};

const SKILL_TEMPLATE = `---
name: my-skill
description: when should the agent reach for this skill?
---

# my-skill
`;

function splitFrontmatter(content: string): { frontmatter: string | null; body: string } {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  return match ? { frontmatter: match[1], body: content.slice(match[0].length) } : { frontmatter: null, body: content };
}

function SkillDoc({ content }: { content: string }) {
  const { frontmatter, body } = splitFrontmatter(content);
  return (
    <div className="md skill-view">
      {frontmatter && <pre className="frontmatter">{frontmatter}</pre>}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
    </div>
  );
}

const ICONS = {
  plus: "M5 12h14 M12 5v14",
  trash: "M3 6h18 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M10 11v6 M14 11v6",
  pencil: "M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z",
  play: "m6 3 14 9-14 9V3z",
  refresh: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16 M3 21v-5h5",
  x: "M18 6 6 18 M6 6l12 12",
  arrowUp: "m5 12 7-7 7 7 M12 19V5",
  file: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z M14 2v4a2 2 0 0 0 2 2h4",
  filePlus: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z M14 2v4a2 2 0 0 0 2 2h4 M9 15h6 M12 12v6",
} as const;

function Icon({ name, size = 14 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICONS[name]} />
    </svg>
  );
}

const TABS = ["chat", "history", "schedules", "skills", "artifacts"] as const;
type Tab = (typeof TABS)[number];

async function detailOf(response: Response): Promise<string> {
  const text = await response.text();
  try {
    return JSON.parse(text).detail ?? (text || response.statusText);
  } catch {
    return text || response.statusText;
  }
}

export default function Page() {
  const [tab, setTab] = useState<Tab>("chat");
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
  const [scheduleDelete, setScheduleDelete] = useState<Schedule | null>(null);
  const [ranNow, setRanNow] = useState("");
  const [scheduleError, setScheduleError] = useState("");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillEditor, setSkillEditor] = useState<SkillEditor | null>(null);
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillError, setSkillError] = useState("");
  const [skillDelete, setSkillDelete] = useState<{ skill: string; path?: string } | null>(null);
  const [deleteTyped, setDeleteTyped] = useState("");
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

  async function fetchInto<T>(url: string, set: (items: T[]) => void) {
    try {
      set(await (await fetch(url)).json());
    } catch {
      set([]);
    }
  }

  const loadRuns = () => fetchInto<Run>("/api/runs", setRuns);
  const loadArtifacts = () => fetchInto<Artifact>("/api/artifacts", setArtifacts);

  async function loadSchedules() {
    setRanNow("");
    setScheduleDelete(null);
    await fetchInto<Schedule>("/api/schedules", setSchedules);
  }

  async function loadSkills() {
    setSkillDelete(null);
    await fetchInto<Skill>("/api/skills", setSkills);
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

  useEffect(() => {
    if (!skillEditor?.viewing && !skillDelete && !scheduleDelete) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (skillDelete) setSkillDelete(null);
      else if (scheduleDelete) setScheduleDelete(null);
      else setSkillEditor(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skillEditor?.viewing, skillDelete, scheduleDelete]);

  function statusLabel(event: { event: string; name?: string }): string {
    if (event.event === "tool") return `using ${event.name?.split("__").pop()}…`;
    if (event.event === "proposal") return "proposing a schedule…";
    if (event.event === "text") return "writing…";
    return "thinking…";
  }

  const LOST = "connection lost — the run continues on the server; reopen it from History once it finishes";

  type StreamState = { streamId: string | null; offset: number; final: string; meta: string; done: boolean };

  async function consume(response: Response, state: StreamState) {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
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
        state.offset += 1;
        if (event.event === "stream") {
          state.streamId = event.id;
        } else if (event.event === "result") {
          setSessionId(event.session_id);
          const cost = event.cost_usd != null ? `$${event.cost_usd.toFixed(4)}` : "";
          state.final = event.text;
          state.meta = `${cost} · ${event.num_turns} turns`;
          state.done = true;
        } else if (event.event === "error") {
          state.final = `error: ${event.detail}`;
          state.done = true;
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
  }

  async function send() {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    setMessages((m) => [...m, { who: "you", text: prompt }]);
    setBusy(true);
    setStatus("starting…");
    const state: StreamState = { streamId: null, offset: 0, final: "", meta: "", done: false };
    try {
      try {
        const response = await fetch("/api/run/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, role, resume: sessionId }),
        });
        if (!response.ok || !response.body) {
          const detail = await detailOf(response);
          setMessages((m) => [...m, { who: "agent", text: `error: ${detail}` }]);
          return;
        }
        await consume(response, state);
      } catch (e) {
        if (!(e instanceof TypeError) || !state.streamId) throw e;
      }
      // the connection drops easily on mobile (screen lock, backgrounding, NAT);
      // the run keeps going server-side, so re-attach and replay from our offset
      for (let attempt = 0; !state.done && state.streamId; attempt++) {
        if (attempt >= 5) {
          state.final = LOST;
          break;
        }
        setStatus("reconnecting…");
        await new Promise((resolve) => setTimeout(resolve, 1500 * (attempt + 1)));
        try {
          const response = await fetch(`/api/run/stream/${state.streamId}?offset=${state.offset}`);
          if (!response.ok || !response.body) {
            state.final = LOST;
            break;
          }
          const before = state.offset;
          await consume(response, state);
          if (state.offset > before) attempt = -1;
        } catch (e) {
          if (!(e instanceof TypeError)) throw e;
        }
      }
      if (!state.done && !state.final) state.final = LOST;
      setMessages((m) => [...m, { who: "agent", text: state.final, meta: state.meta || undefined }]);
    } catch (e) {
      setMessages((m) => [...m, { who: "agent", text: e instanceof TypeError ? LOST : `error: ${e}` }]);
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    send();
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
        setScheduleError(await detailOf(response));
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

  async function performScheduleDelete() {
    if (!scheduleDelete) return;
    const schedule = scheduleDelete;
    setScheduleDelete(null);
    setScheduleError("");
    try {
      const response = await fetch(`/api/schedules/${schedule.id}`, { method: "DELETE" });
      if (!response.ok) setScheduleError(await detailOf(response));
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
        setScheduleError(await detailOf(response));
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
    setSkillDelete(null);
    try {
      const response = await fetch(`/api/skills/${name}/files/${path}`);
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      const data = await response.json();
      setSkillEditor({ skill: name, path, content: data.content, isNew: false, nameLocked: true, viewing: true });
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
      setSkillEditor((e) =>
        e ? { ...e, skill: e.skill.trim(), path: e.path.trim(), isNew: false, nameLocked: true, viewing: true } : e,
      );
      await loadSkills();
    } catch (e) {
      setSkillError(String(e));
    } finally {
      setSkillSaving(false);
    }
  }

  async function performSkillDelete() {
    if (!skillDelete) return;
    const { skill, path } = skillDelete;
    setSkillDelete(null);
    setSkillError("");
    try {
      const url = path ? `/api/skills/${skill}/files/${path}` : `/api/skills/${skill}`;
      const response = await fetch(url, { method: "DELETE" });
      if (!response.ok) {
        setSkillError(await detailOf(response));
        return;
      }
      if (path || skillEditor?.skill === skill) setSkillEditor(null);
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
          {TABS.map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
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
              <button onClick={newChat}>
                <Icon name="plus" />
                new chat
              </button>
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
            <form onSubmit={submit}>
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
              <button onClick={() => setProposal(null)}>
                <Icon name="x" />
                dismiss
              </button>
            </div>
          )}
          <form onSubmit={submit}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={sessionId ? "continue this session…" : "new session…"}
              disabled={busy}
            />
            <button type="submit" className="send" disabled={busy} aria-label="send">
              <Icon name="arrowUp" size={16} />
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
                <Icon name="plus" />
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
          {scheduleDelete && (
            <div className="modal-backdrop" onClick={() => setScheduleDelete(null)}>
              <div className="modal schedule confirm-delete" onClick={(e) => e.stopPropagation()}>
                <strong>delete schedule {scheduleDelete.name}?</strong>
                <p className="hint">the scheduled task and its cron settings are removed — there is no undo.</p>
                <div className="schedule-actions">
                  <button onClick={() => setScheduleDelete(null)}>cancel</button>
                  <button className="danger" onClick={performScheduleDelete}>
                    <Icon name="trash" />
                    delete
                  </button>
                </div>
              </div>
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
                    <Icon name="play" />
                    {ranNow === schedule.id ? "started — see history" : "run now"}
                  </button>
                  <button className="compact" onClick={() => setScheduleDelete(schedule)}>
                    <Icon name="trash" size={12} />
                    delete
                  </button>
                  <button
                    onClick={() => {
                      setForm({ ...schedule });
                      setScheduleDelete(null);
                    }}
                  >
                    <Icon name="pencil" />
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
                  setSkillEditor({
                    skill: "",
                    path: "SKILL.md",
                    content: SKILL_TEMPLATE,
                    isNew: true,
                    nameLocked: false,
                    viewing: false,
                  });
                }}
              >
                <Icon name="plus" />
                new skill
              </button>
            )}
          </div>
          {skillError && !skillEditor && <p className="schedule-error">{skillError}</p>}
          {skillEditor && (
            <div
              className="modal-backdrop"
              onClick={() => {
                if (skillEditor.viewing) setSkillEditor(null);
              }}
            >
              <div className="modal schedule form" onClick={(e) => e.stopPropagation()}>
                <div className="schedule-head">
                  <strong className={skillEditor.isNew ? undefined : "file-path"}>
                    {skillEditor.isNew
                      ? skillEditor.nameLocked
                        ? `new file in ${skillEditor.skill}`
                        : "new skill"
                      : `${skillEditor.skill}/${skillEditor.path}`}
                  </strong>
                  <div className="schedule-actions">
                    {!skillEditor.isNew && (
                      <button
                        className="compact"
                        onClick={() => {
                          setDeleteTyped("");
                          setSkillDelete({ skill: skillEditor.skill, path: skillEditor.path });
                        }}
                      >
                        <Icon name="trash" size={12} />
                        delete file
                      </button>
                    )}
                    {skillEditor.viewing ? (
                      <>
                        <button onClick={() => setSkillEditor(null)}>
                          <Icon name="x" />
                          close
                        </button>
                        <button className="primary" onClick={() => editSkill({ viewing: false })}>
                          <Icon name="pencil" />
                          edit
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() =>
                            skillEditor.isNew
                              ? setSkillEditor(null)
                              : openSkillFile(skillEditor.skill, skillEditor.path)
                          }
                        >
                          cancel
                        </button>
                        <button
                          className="primary"
                          onClick={saveSkillFile}
                          disabled={skillSaving || !skillEditor.skill.trim() || !skillEditor.path.trim()}
                        >
                          {skillSaving ? "saving…" : "save"}
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {skillError && <p className="schedule-error">{skillError}</p>}
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
                {skillEditor.viewing ? (
                  skillEditor.path.endsWith(".md") ? (
                    <SkillDoc content={skillEditor.content} />
                  ) : (
                    <pre className="skill-view">{skillEditor.content}</pre>
                  )
                ) : (
                  <textarea
                    className="skill-content"
                    value={skillEditor.content}
                    onChange={(e) => editSkill({ content: e.target.value })}
                    rows={16}
                  />
                )}
              </div>
            </div>
          )}
          {skillDelete && (
            <div className="modal-backdrop" onClick={() => setSkillDelete(null)}>
              <div className="modal schedule confirm-delete" onClick={(e) => e.stopPropagation()}>
                <strong>
                  {skillDelete.path
                    ? `delete ${skillDelete.skill}/${skillDelete.path}?`
                    : `delete skill ${skillDelete.skill}?`}
                </strong>
                <p className="hint">
                  {skillDelete.path
                    ? "the file is removed from the skill — there is no undo."
                    : `every file in this skill is removed — there is no undo. type "${skillDelete.skill}" to confirm.`}
                </p>
                {!skillDelete.path && (
                  <input
                    value={deleteTyped}
                    onChange={(e) => setDeleteTyped(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && deleteTyped === skillDelete.skill) performSkillDelete();
                    }}
                    placeholder={skillDelete.skill}
                    autoFocus
                  />
                )}
                <div className="schedule-actions">
                  <button onClick={() => setSkillDelete(null)}>cancel</button>
                  <button
                    className="danger"
                    disabled={!skillDelete.path && deleteTyped !== skillDelete.skill}
                    onClick={performSkillDelete}
                  >
                    <Icon name="trash" />
                    delete
                  </button>
                </div>
              </div>
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
                      setSkillEditor({
                        skill: skill.name,
                        path: "",
                        content: "",
                        isNew: true,
                        nameLocked: true,
                        viewing: false,
                      });
                    }}
                  >
                    <Icon name="filePlus" />
                    add file
                  </button>
                  <button
                    className="compact"
                    onClick={() => {
                      setDeleteTyped("");
                      setSkillDelete({ skill: skill.name });
                    }}
                  >
                    <Icon name="trash" size={12} />
                    delete
                  </button>
                </div>
              </div>
              <div className="skill-files">
                {skill.files.length === 0 && <span className="hint">no files yet — add SKILL.md</span>}
                {skill.files.map((file) => (
                  <button key={file} onClick={() => openSkillFile(skill.name, file)}>
                    <Icon name="file" size={12} />
                    {file}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}

      {tab === "history" && (
        <section className="history">
          <button className="refresh" onClick={loadRuns}>
            <Icon name="refresh" size={12} />
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
            <Icon name="refresh" size={12} />
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
