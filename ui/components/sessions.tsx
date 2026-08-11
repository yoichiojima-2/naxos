"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { agentName, api, Agent, EVENT_TYPES, Session, SessionEvent, WorkspaceFile } from "@/lib/api";
import { fullTime, relativeTime, shortId } from "@/lib/format";
import { BackIcon } from "@/components/icons";
import CountHeader from "@/components/list-header";
import Markdown from "@/components/markdown";
import FilterInput from "@/components/filter-input";

const STATUS_FILTERS = ["needs approval", "running", "idle", "rescheduling", "terminated"] as const;

const statusOf = (s: Session) =>
  s.status === "idle" && s.stop_reason === "requires_action" ? "needs approval" : s.status;

const openSession = (id: string) => { window.location.hash = `#sessions/${id}`; };

export default function Sessions({ agents, sessionId }: { agents: Agent[]; sessionId?: string }) {
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [agentId, setAgentId] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const refresh = useCallback(async () => {
    const result = await api<{ data: Session[] }>("/v1/sessions?limit=200");
    setSessions(result.data);
    setSelected((prev) => {
      const ids = new Set(result.data.map((s) => s.id));
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, []);

  useEffect(() => {
    if (sessionId) return;
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh, sessionId]);

  async function createSession() {
    const target = agentId || agents[0]?.id;
    if (!target) return;
    const session = await api<Session>("/v1/sessions", {
      json: { agent: { id: target } },
    });
    openSession(session.id);
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const q = query.trim().toLowerCase();
  const base = (sessions ?? []).filter((s) => {
    if (agentFilter && s.agent_id !== agentFilter) return false;
    if (!q) return true;
    return `${s.title ?? ""} ${s.id} ${s.created_by ?? ""} ${agentName(agents, s.agent_id)}`
      .toLowerCase()
      .includes(q);
  });
  const filtered = base.filter((s) => !statusFilter || statusOf(s) === statusFilter);
  const hasFilters = !!(q || agentFilter || statusFilter);

  const statusCounts = new Map<string, number>();
  for (const s of base) {
    const status = statusOf(s);
    statusCounts.set(status, (statusCounts.get(status) ?? 0) + 1);
  }

  function clearFilters() {
    setQuery("");
    setAgentFilter("");
    setStatusFilter("");
  }

  const allSelected = !!filtered.length && filtered.every((s) => selected.has(s.id));
  const hiddenSelected = selected.size - filtered.filter((s) => selected.has(s.id)).length;

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const s of filtered) {
        if (allSelected) next.delete(s.id);
        else next.add(s.id);
      }
      return next;
    });
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

  async function bulkDelete() {
    const ok = window.confirm(
      `Permanently delete ${selected.size} ${noun}, including events, workspace files, and ` +
        "artifacts (shared artifact links stop working)? Sessions with a live sandbox are refused.",
    );
    if (!ok) return;
    await bulkApply((id) => api(`/v1/sessions/${id}`, { method: "DELETE" }));
  }

  if (sessionId) {
    return (
      <Timeline
        key={sessionId}
        sessionId={sessionId}
        known={sessions?.find((s) => s.id === sessionId)}
      />
    );
  }

  return (
    <>
      <CountHeader count={sessions === null ? null : filtered.length} of={sessions?.length} noun="session">
        {selected.size > 0 ? (
          <>
            <span className="muted">
              {selected.size} selected
              {hiddenSelected > 0 && ` (${hiddenSelected} hidden by filters)`}
            </span>
            <button className="ghost" onClick={bulkSetBudget}>Set budget</button>
            <button className="danger" onClick={bulkTerminate}>Terminate</button>
            <button className="danger" onClick={bulkDelete}>Delete</button>
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
      {!!sessions?.length && (
        <div className="row mb12">
          <FilterInput
            placeholder="Filter by title, id, or principal…"
            value={query}
            onChange={setQuery}
          />
          <select
            className="filter-input"
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            aria-label="filter by agent"
          >
            <option value="">all agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          {STATUS_FILTERS.filter(
            (status) => statusCounts.get(status) || status === statusFilter,
          ).map((status) => (
            <button
              key={status}
              className={`chip ${statusFilter === status ? "on" : ""}`}
              onClick={() => setStatusFilter(statusFilter === status ? "" : status)}
            >
              {status} {statusCounts.get(status) ?? 0}
            </button>
          ))}
          {hasFilters && (
            <button className="ghost" onClick={clearFilters}>Clear filters</button>
          )}
          {sessions?.length === 200 && (
            <span className="muted">showing the newest 200 sessions</span>
          )}
        </div>
      )}
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
              <th>title</th><th>agent</th><th>status</th><th>principal</th>
              <th className="ta-right">cost</th><th>created</th>
            </tr>
          </thead>
          <tbody>
            {sessions === null && (
              <tr><td className="empty" colSpan={7}>loading…</td></tr>
            )}
            {sessions?.length === 0 && (
              <tr><td className="empty" colSpan={7}>no sessions yet — pick an agent above and start one.</td></tr>
            )}
            {!!sessions?.length && filtered.length === 0 && (
              <tr><td className="empty" colSpan={7}>no sessions match the current filters.</td></tr>
            )}
            {filtered.map((s) => {
              const needsAction = s.status === "idle" && s.stop_reason === "requires_action";
              return (
                <tr key={s.id} className="click" onClick={() => openSession(s.id)}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selected.has(s.id)}
                      onChange={() => toggle(s.id)}
                      aria-label={`select ${s.title ?? s.id}`}
                    />
                  </td>
                  <td>{s.title ?? <span className="muted mono" title={s.id}>{shortId(s.id)}</span>}</td>
                  <td className="muted">{agentName(agents, s.agent_id)}</td>
                  <td>
                    <span className={`badge ${needsAction ? "requires_action" : s.status}`}>
                      {needsAction ? "needs approval" : s.status}
                      {s.stop_reason && !needsAction ? `:${s.stop_reason}` : ""}
                    </span>
                  </td>
                  <td className="muted">{s.created_by}</td>
                  <td className="mono ta-right">${Number(s.cost_usd).toFixed(4)}</td>
                  <td className="muted" title={fullTime(s.created_at)}>{relativeTime(s.created_at)}</td>
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

function Timeline({ sessionId, known }: { sessionId: string; known?: Session }) {
  const [session, setSession] = useState<Session | null>(known ?? null);
  const [missing, setMissing] = useState(false);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState(known?.status ?? "idle");
  const [connected, setConnected] = useState(true);
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const source = useRef<EventSource | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const followed = useRef(false);

  useEffect(() => {
    if (known) return;
    api<Session>(`/v1/sessions/${sessionId}`)
      .then((s) => {
        setSession(s);
        setStatus(s.status);
      })
      .catch(() => setMissing(true));
  }, [sessionId, known]);

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
    const result = await api<{ data: WorkspaceFile[] }>(`/v1/sessions/${sessionId}/workspace`);
    setFiles(result.data);
  }

  useEffect(() => {
    const es = new EventSource(`/v1/sessions/${sessionId}/events?stream=sse`);
    source.current = es;
    const push = (raw: MessageEvent) => {
      setConnected(true);
      const event = JSON.parse(raw.data) as SessionEvent;
      setEvents((prev) => (prev.some((e) => e.seq === event.seq) ? prev : [...prev, event]));
      if (event.type === "session.status_running") setStatus("running");
      if (event.type === "session.status_idle") setStatus("idle");
      if (event.type === "session.status_rescheduling") setStatus("rescheduling");
      if (event.type === "session.status_terminated") {
        setStatus("terminated");
        es.close(); // the server ends the stream here; without close() EventSource reconnects forever
      }
    };
    // Named SSE events require a listener per type; a catch-all keeps this simple.
    EVENT_TYPES.forEach((type) => es.addEventListener(type, push));
    es.onopen = () => setConnected(true);
    // A CLOSED source never retries (e.g. the session does not exist), so
    // "reconnecting…" would be a lie; the missing-session state covers it.
    es.onerror = () => setConnected(es.readyState === EventSource.CLOSED);
    return () => es.close();
  }, [sessionId]);

  async function send() {
    if (!message.trim() || sending) return;
    setSending(true);
    try {
      await api(`/v1/sessions/${sessionId}/events`, {
        json: { events: [{ type: "user.message", content: [{ type: "text", text: message }] }] },
      });
      setMessage("");
      if (composerRef.current) composerRef.current.style.height = "auto";
    } finally {
      setSending(false);
    }
  }

  async function interrupt() {
    await api(`/v1/sessions/${sessionId}/events`, {
      json: { events: [{ type: "user.interrupt" }] },
    });
  }

  async function confirm(callHash: string, result: "allow" | "deny") {
    await api(`/v1/sessions/${sessionId}/events`, {
      json: { events: [{ type: "user.tool_confirmation", call_hash: callHash, result }] },
    });
  }

  const decided = new Set(
    events
      .filter((e) => e.type === "user.tool_confirmation")
      .map((e) => String(e.payload.call_hash)),
  );

  const live = status === "running" || status === "rescheduling";

  if (missing) {
    return (
      <div className="panel">
        <div className="row mb12">
          <a className="back" href="#sessions" aria-label="back to sessions">
            <BackIcon />
          </a>
          <strong className="mono">{shortId(sessionId)}</strong>
        </div>
        <p className="muted">
          this session could not be loaded — it may have been deleted.{" "}
          <a href="#sessions">Back to sessions</a>
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="row between mb12">
        <div className="row">
          <a className="back" href="#sessions" aria-label="back to sessions">
            <BackIcon />
          </a>
          <strong>{session?.title ?? <span className="mono" title={sessionId}>{shortId(sessionId)}</span>}</strong>
          <span className={`badge ${status}`}>{status}</span>
          {!connected && status !== "terminated" && (
            <span className="stream-note"><span className="spinner" />reconnecting…</span>
          )}
        </div>
        <div className="row">
          <button className="ghost" onClick={loadFiles}>Files</button>
          <button
            className="ghost"
            onClick={interrupt}
            disabled={!live}
            title={live ? undefined : "the agent is not running"}
          >
            Interrupt
          </button>
        </div>
      </div>
      {files && (
        <div className="panel mb12">
          {files.length === 0 && <span className="muted">workspace is empty</span>}
          {files.map((f) => (
            <div className="row between" key={f.path}>
              <a
                className="mono"
                href={`/v1/sessions/${sessionId}/workspace/${f.path}`}
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
          <div className="event system">
            <span className="muted"><span className="spinner" />session is waking up…</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="composer">
        <textarea
          ref={composerRef}
          rows={1}
          value={message}
          placeholder="Message the agent…"
          title="Enter to send, Shift+Enter for a new line"
          onChange={(e) => {
            setMessage(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${e.target.scrollHeight + 4}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          onFocus={(e) => e.target.scrollIntoView({ block: "center" })}
          disabled={status === "terminated"}
          aria-label="message the agent"
        />
        <button
          className="primary"
          onClick={send}
          disabled={status === "terminated" || sending || !message.trim()}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </div>
  );
}

function EventTime({ at }: { at: string }) {
  return (
    <span className="event-time" title={fullTime(at)}>
      {new Date(at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </span>
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
  const token = share_url?.match(/\/artifacts\/shared\/([^/]+)$/)?.[1];
  return (
    <div className="event agent fold">
      <span className="fold-line">
        artifact {action}
        {action === "deleted" ? (
          <span className="mono">{name}</span>
        ) : (
          <a className="mono" href={`#artifacts/${artifact_id}`}>
            {name}
          </a>
        )}
        {version != null && action !== "deleted" && <span className="muted">v{version}</span>}
        {action === "shared" && (token ? (
          <a href={`#artifacts/shared/${token}`}>link</a>
        ) : (
          share_url && <a href={share_url} target="_blank" rel="noreferrer">link</a>
        ))}
        <EventTime at={event.created_at} />
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
          <strong>{String(event.payload.tool_name ?? "")}</strong>{" "}
          <EventTime at={event.created_at} />
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
      <EventTime at={event.created_at} />
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
      <span className="muted">
        {event.type}
        {event.principal ? ` · ${event.principal}` : ""}{" "}
        <EventTime at={event.created_at} />
      </span>
      {markdown && <Markdown source={markdown} />}
      {body && <pre>{body}</pre>}
    </div>
  );
}
