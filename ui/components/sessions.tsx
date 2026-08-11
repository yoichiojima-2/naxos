"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown, { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentName, api, Agent, EVENT_TYPES, Session, SessionEvent, WorkspaceFile } from "@/lib/api";
import { BackIcon } from "@/components/icons";
import CountHeader from "@/components/list-header";

const MD_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

export default function Sessions({ agents }: { agents: Agent[] }) {
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [open, setOpen] = useState<Session | null>(null);
  const [agentId, setAgentId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    const result = await api<{ data: Session[] }>("/v1/sessions");
    setSessions(result.data);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function createSession() {
    const target = agentId || agents[0]?.id;
    if (!target) return;
    const session = await api<Session>("/v1/sessions", {
      json: { agent: { id: target } },
    });
    await refresh();
    setOpen(session);
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allSelected = !!sessions?.length && sessions.every((s) => selected.has(s.id));

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set((sessions ?? []).map((s) => s.id)));
  }

  const noun = selected.size === 1 ? "session" : "sessions";

  async function bulkApply(run: (id: string) => Promise<unknown>) {
    const ids = [...selected];
    const results = await Promise.allSettled(ids.map(run));
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    await refresh();
    if (failed.length) {
      window.alert(`${failed.length} of ${ids.length} requests failed; the failed ${
        failed.length === 1 ? "session stays" : "sessions stay"} selected.`);
    }
  }

  async function bulkSetBudget() {
    const raw = window.prompt(`New budget in USD for ${selected.size} ${noun}:`);
    if (raw === null || !raw.trim()) return;
    const budget = Number(raw);
    if (!Number.isFinite(budget) || budget < 0) {
      window.alert(`"${raw}" is not a valid budget`);
      return;
    }
    await bulkApply((id) =>
      api(`/v1/sessions/${id}`, { method: "PATCH", json: { budget_usd: budget } }),
    );
  }

  async function bulkTerminate() {
    if (!window.confirm(`Terminate ${selected.size} ${noun}?`)) return;
    await bulkApply((id) => api(`/v1/sessions/${id}/terminate`, { json: {} }));
  }

  if (open) {
    return <Timeline session={open} onBack={() => { setOpen(null); refresh(); }} />;
  }

  return (
    <>
      <CountHeader count={sessions === null ? null : sessions.length} noun="session">
        {selected.size > 0 ? (
          <>
            <span className="muted">{selected.size} selected</span>
            <button className="ghost" onClick={bulkSetBudget}>Set budget</button>
            <button className="danger" onClick={bulkTerminate}>Terminate</button>
            <button className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
          </>
        ) : (
          <>
            <select value={agentId} onChange={(e) => setAgentId(e.target.value)} style={{ width: 220 }}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <button className="primary" onClick={createSession} disabled={!agents.length}>
              New session
            </button>
          </>
        )}
      </CountHeader>
      <div className="panel flush">
        <div className="table-wrap">
          <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="select all sessions"
                />
              </th>
              <th>title</th><th>agent</th><th>status</th><th>principal</th><th>cost</th><th>created</th>
            </tr>
          </thead>
          <tbody>
            {sessions === null && (
              <tr><td className="empty" colSpan={7}>loading…</td></tr>
            )}
            {sessions?.length === 0 && (
              <tr><td className="empty" colSpan={7}>no sessions yet — pick an agent above and start one.</td></tr>
            )}
            {(sessions ?? []).map((s) => {
              const needsAction = s.status === "idle" && s.stop_reason === "requires_action";
              return (
                <tr key={s.id} className="click" onClick={() => setOpen(s)}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.has(s.id)}
                      onChange={() => toggle(s.id)}
                      aria-label={`select ${s.title ?? s.id}`}
                    />
                  </td>
                  <td>{s.title ?? s.id}</td>
                  <td className="muted">{agentName(agents, s.agent_id)}</td>
                  <td>
                    <span className={`badge ${needsAction ? "requires_action" : s.status}`}>
                      {needsAction ? "needs approval" : s.status}
                      {s.stop_reason && !needsAction ? `:${s.stop_reason}` : ""}
                    </span>
                  </td>
                  <td className="muted">{s.created_by}</td>
                  <td className="mono">${Number(s.cost_usd).toFixed(4)}</td>
                  <td className="muted">{new Date(s.created_at).toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </div>
    </>
  );
}

function Timeline({ session, onBack }: { session: Session; onBack: () => void }) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState(session.status);
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const source = useRef<EventSource | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const followed = useRef(false);

  useEffect(() => {
    if (!events.length) return;
    const nearBottom =
      window.innerHeight + window.scrollY >= document.body.offsetHeight - 240;
    if (!followed.current || nearBottom) {
      endRef.current?.scrollIntoView({ block: "end" });
      followed.current = true;
    }
  }, [events.length]);

  async function loadFiles() {
    if (files) { setFiles(null); return; }
    const result = await api<{ data: WorkspaceFile[] }>(`/v1/sessions/${session.id}/workspace`);
    setFiles(result.data);
  }

  useEffect(() => {
    const es = new EventSource(`/v1/sessions/${session.id}/events?stream=sse`);
    source.current = es;
    const push = (raw: MessageEvent) => {
      const event = JSON.parse(raw.data) as SessionEvent;
      setEvents((prev) => (prev.some((e) => e.seq === event.seq) ? prev : [...prev, event]));
      if (event.type === "session.status_running") setStatus("running");
      if (event.type === "session.status_idle") setStatus("idle");
      if (event.type === "session.status_terminated") setStatus("terminated");
    };
    // Named SSE events require a listener per type; a catch-all keeps this simple.
    EVENT_TYPES.forEach((type) => es.addEventListener(type, push));
    return () => es.close();
  }, [session.id]);

  async function send() {
    if (!message.trim()) return;
    await api(`/v1/sessions/${session.id}/events`, {
      json: { events: [{ type: "user.message", content: [{ type: "text", text: message }] }] },
    });
    setMessage("");
  }

  async function interrupt() {
    await api(`/v1/sessions/${session.id}/events`, {
      json: { events: [{ type: "user.interrupt" }] },
    });
  }

  async function confirm(callHash: string, result: "allow" | "deny") {
    await api(`/v1/sessions/${session.id}/events`, {
      json: { events: [{ type: "user.tool_confirmation", call_hash: callHash, result }] },
    });
  }

  const decided = new Set(
    events
      .filter((e) => e.type === "user.tool_confirmation")
      .map((e) => String(e.payload.call_hash)),
  );

  return (
    <div className="panel">
      <div className="row between mb12">
        <div className="row">
          <button className="ghost flex-inline" onClick={onBack} aria-label="back">
            <BackIcon />
          </button>
          <strong>{session.title ?? session.id}</strong>
          <span className={`badge ${status}`}>{status}</span>
        </div>
        <div className="row">
          <button className="ghost" onClick={loadFiles}>Files</button>
          <button className="ghost" onClick={interrupt}>Interrupt</button>
        </div>
      </div>
      {files && (
        <div className="panel mb12">
          {files.length === 0 && <span className="muted">workspace is empty</span>}
          {files.map((f) => (
            <div className="row between" key={f.path}>
              <a
                className="mono"
                href={`/v1/sessions/${session.id}/workspace/${f.path}`}
                target="_blank"
                rel="noreferrer"
              >
                {f.path}
              </a>
              <span className="muted mono">{f.size} B</span>
            </div>
          ))}
        </div>
      )}
      <div className="timeline">
        {events.length === 0 && status !== "rescheduling" && (
          <span className="muted">no messages yet — say something below to begin.</span>
        )}
        {events.map((event) => (
          <Event
            key={event.seq}
            event={event}
            decided={decided}
            onConfirm={confirm}
          />
        ))}
        {status === "rescheduling" && (
          <div className="event system"><span className="muted">session is waking up…</span></div>
        )}
      </div>
      <div className="composer">
        <input
          value={message}
          placeholder="Message the agent…"
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          onFocus={(e) => e.target.scrollIntoView({ block: "center" })}
          disabled={status === "terminated"}
        />
        <button className="primary" onClick={send} disabled={status === "terminated"}>Send</button>
      </div>
      <div ref={endRef} />
    </div>
  );
}

function Event({
  event,
  decided,
  onConfirm,
}: {
  event: SessionEvent;
  decided: Set<string>;
  onConfirm: (hash: string, result: "allow" | "deny") => void;
}) {
  if (event.type === "agent.tool_use" && event.payload.decision === "awaiting_confirmation") {
    return <ApprovalEvent event={event} decided={decided} onConfirm={onConfirm} />;
  }
  if (
    event.type === "agent.thinking" ||
    event.type === "agent.tool_use" ||
    event.type === "agent.tool_result"
  ) {
    return <FoldedEvent event={event} />;
  }
  if (event.type === "agent.artifact") {
    return <ArtifactEvent event={event} />;
  }
  return <MessageEvent event={event} />;
}

function ArtifactEvent({ event }: { event: SessionEvent }) {
  const { artifact_id, name, action, version, share_url } = event.payload as {
    artifact_id?: string;
    name?: string;
    action?: string;
    version?: number;
    share_url?: string;
  };
  return (
    <div className="event agent fold">
      <span className="fold-line">
        artifact {action}
        {action === "deleted" ? (
          <span className="mono">{name}</span>
        ) : (
          <a className="mono" href={`/v1/artifacts/${artifact_id}/content`} target="_blank" rel="noreferrer">
            {name}
          </a>
        )}
        {version != null && action !== "deleted" && <span className="muted">v{version}</span>}
        {share_url && action === "shared" && (
          <a href={share_url} target="_blank" rel="noreferrer">link</a>
        )}
      </span>
    </div>
  );
}

function ApprovalEvent({
  event,
  decided,
  onConfirm,
}: {
  event: SessionEvent;
  decided: Set<string>;
  onConfirm: (hash: string, result: "allow" | "deny") => void;
}) {
  const hash = String(event.payload.call_hash);
  const pending = !decided.has(hash);
  return (
    <div className="event ask">
      <div className="row between">
        <span>
          <span className="badge requires_action">approval</span>{" "}
          <strong>{String(event.payload.tool_name ?? "")}</strong>
        </span>
        {pending && (
          <span className="row">
            <button className="primary" onClick={() => onConfirm(hash, "allow")}>Allow</button>
            <button className="danger" onClick={() => onConfirm(hash, "deny")}>Deny</button>
          </span>
        )}
      </div>
      <pre>{JSON.stringify(event.payload.input, null, 2)}</pre>
    </div>
  );
}

function FoldedEvent({ event }: { event: SessionEvent }) {
  const payload = event.payload;
  let label = "thinking";
  let name = "";
  let detail = "";
  let flag = "";
  if (event.type === "agent.tool_use") {
    label = "tool";
    name = String(payload.tool_name ?? "");
    detail = JSON.stringify(payload.input ?? {}, null, 2);
    if (payload.decision === "user_denied") flag = "denied";
    if (payload.decision === "killed") flag = "killed";
  } else if (event.type === "agent.tool_result") {
    label = "result";
    detail = String(payload.content ?? "");
    if (payload.is_error) flag = "error";
  } else {
    detail = String(payload.text ?? "");
  }
  const summary = (
    <>
      {label}
      {name && <span className="mono">{name}</span>}
      {flag && <span className="badge terminated">{flag}</span>}
    </>
  );
  return (
    <div className="event agent fold">
      {detail ? (
        <details>
          <summary>{summary}</summary>
          <pre>{detail}</pre>
        </details>
      ) : (
        <span className="fold-line">{summary}</span>
      )}
    </div>
  );
}

function MessageEvent({ event }: { event: SessionEvent }) {
  const kind = event.type.split(".")[0];
  const payload = event.payload;
  let body = "";
  let markdown = "";
  if (event.type === "user.message") {
    body = (payload.content as { text?: string }[] | undefined)
      ?.map((b) => b.text)
      .join("\n") ?? "";
  } else if (event.type === "agent.message") {
    markdown = String(payload.text ?? "");
  } else if (event.type === "session.error") {
    body = String(payload.error ?? "");
  }

  return (
    <div
      className={[
        "event",
        kind === "user" ? "user" : kind === "agent" ? "agent" : "system",
        event.type === "agent.message" ? "prose" : "",
      ].join(" ").trim()}
    >
      <span className="muted">{event.type}{event.principal ? ` · ${event.principal}` : ""}</span>
      {markdown && (
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
            {markdown}
          </ReactMarkdown>
        </div>
      )}
      {body && <pre>{body}</pre>}
    </div>
  );
}
