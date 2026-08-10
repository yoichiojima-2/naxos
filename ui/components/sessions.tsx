"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, Agent, Session, SessionEvent, WorkspaceFile } from "@/lib/api";

const BADGE: Record<string, string> = {
  idle: "idle",
  running: "running",
  rescheduling: "rescheduling",
  terminated: "terminated",
};

export default function Sessions({ agents }: { agents: Agent[] }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [open, setOpen] = useState<Session | null>(null);
  const [agentId, setAgentId] = useState("");

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

  if (open) {
    return <Timeline session={open} onBack={() => { setOpen(null); refresh(); }} />;
  }

  return (
    <div className="panel">
      <div className="row between" style={{ marginBottom: 12 }}>
        <strong>Sessions</strong>
        <div className="row">
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)} style={{ width: 220 }}>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <button className="primary" onClick={createSession} disabled={!agents.length}>
            New session
          </button>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>title</th><th>status</th><th>principal</th><th>cost</th><th>created</th></tr>
          </thead>
          <tbody>
            {sessions.map((s) => {
              const needsAction = s.status === "idle" && s.stop_reason === "requires_action";
              return (
                <tr key={s.id} className="click" onClick={() => setOpen(s)}>
                  <td>{s.title ?? s.id}</td>
                  <td>
                    <span className={`badge ${needsAction ? "requires_action" : BADGE[s.status]}`}>
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
  );
}

function Timeline({ session, onBack }: { session: Session; onBack: () => void }) {
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState(session.status);
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const source = useRef<EventSource | null>(null);

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
    [
      "user.message", "user.interrupt", "user.tool_confirmation", "user.custom_tool_result",
      "agent.message", "agent.thinking", "agent.tool_use", "agent.tool_result",
      "session.status_running", "session.status_idle", "session.status_terminated",
      "session.error", "span.model_request_start", "span.model_request_end",
    ].forEach((type) => es.addEventListener(type, push));
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
      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="row">
          <button className="ghost" onClick={onBack}>&larr;</button>
          <strong>{session.title ?? session.id}</strong>
          <span className={`badge ${BADGE[status]}`}>{status}</span>
        </div>
        <div className="row">
          <button className="ghost" onClick={loadFiles}>Files</button>
          <button className="ghost" onClick={interrupt}>Interrupt</button>
        </div>
      </div>
      {files && (
        <div className="panel" style={{ background: "var(--panel2)", marginBottom: 12 }}>
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
          disabled={status === "terminated"}
        />
        <button className="primary" onClick={send} disabled={status === "terminated"}>Send</button>
      </div>
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
  const kind = event.type.split(".")[0];
  const payload = event.payload as Record<string, any>;

  if (event.type === "agent.tool_use" && payload.decision === "awaiting_confirmation") {
    const hash = String(payload.call_hash);
    const pending = !decided.has(hash);
    return (
      <div className="event ask">
        <div className="row between">
          <span>
            <span className="badge requires_action">approval</span>{" "}
            <strong>{payload.tool_name}</strong>
          </span>
          {pending && (
            <span className="row">
              <button className="primary" onClick={() => onConfirm(hash, "allow")}>Allow</button>
              <button className="danger" onClick={() => onConfirm(hash, "deny")}>Deny</button>
            </span>
          )}
        </div>
        <pre>{JSON.stringify(payload.input, null, 2)}</pre>
      </div>
    );
  }

  let body = "";
  if (event.type === "user.message") {
    body = (payload.content as { text?: string }[] | undefined)
      ?.map((b) => b.text)
      .join("\n") ?? "";
  } else if (event.type === "agent.message") {
    body = String(payload.text ?? "");
  } else if (event.type === "agent.tool_use") {
    body = `${payload.tool_name} ${JSON.stringify(payload.input ?? {})}`;
  } else if (event.type === "agent.tool_result") {
    body = String(payload.content ?? "").slice(0, 600);
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
      {body && <pre>{body}</pre>}
    </div>
  );
}
