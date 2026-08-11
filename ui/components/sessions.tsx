"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { agentName, api, Agent, EVENT_TYPES, Session, SessionEvent, WorkspaceFile } from "@/lib/api";
import { Block, ToolBlock, groupEvents, prettifyResult, toolSummary } from "@/lib/timeline";
import { fullTime, relativeTime, shortId } from "@/lib/format";
import { BackIcon } from "@/components/icons";
import FavoriteStar, { FavoriteProps, useFavoriteFilter } from "@/components/favorite-star";
import CountHeader from "@/components/list-header";
import Markdown from "@/components/markdown";
import FilterInput from "@/components/filter-input";

const STATUS_FILTERS = ["needs approval", "running", "idle", "rescheduling", "terminated"] as const;

const statusOf = (s: Session) =>
  s.status === "idle" && s.stop_reason === "requires_action" ? "needs approval" : s.status;

const openSession = (id: string) => { window.location.hash = `#sessions/${id}`; };

export default function Sessions({
  agents,
  sessionId,
  favorites,
  onToggleFavorite,
}: { agents: Agent[]; sessionId?: string } & FavoriteProps) {
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
  const favFilter = useFavoriteFilter("session", base, favorites);
  const filtered = favFilter.apply(
    base.filter((s) => !statusFilter || statusOf(s) === statusFilter),
  );
  const hasFilters = !!(q || agentFilter || statusFilter || favFilter.favOnly);

  const statusCounts = new Map<string, number>();
  for (const s of base) {
    const status = statusOf(s);
    statusCounts.set(status, (statusCounts.get(status) ?? 0) + 1);
  }

  function clearFilters() {
    setQuery("");
    setAgentFilter("");
    setStatusFilter("");
    favFilter.clear();
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
        agents={agents}
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
          {favFilter.chip}
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
              <th />
              <th>title</th><th>agent</th><th>status</th><th>principal</th>
              <th className="ta-right">cost</th><th>created</th>
            </tr>
          </thead>
          <tbody>
            {sessions === null && (
              <tr><td className="empty" colSpan={8}>loading…</td></tr>
            )}
            {sessions?.length === 0 && (
              <tr><td className="empty" colSpan={8}>no sessions yet — pick an agent above and start one.</td></tr>
            )}
            {!!sessions?.length && filtered.length === 0 && (
              <tr><td className="empty" colSpan={8}>no sessions match the current filters.</td></tr>
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
                  <td onClick={(e) => e.stopPropagation()} style={{ width: 1 }}>
                    <FavoriteStar
                      type="session"
                      id={s.id}
                      favorites={favorites}
                      onToggleFavorite={onToggleFavorite}
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

function Timeline({
  sessionId,
  agents,
  known,
}: { sessionId: string; agents: Agent[]; known?: Session }) {
  const [session, setSession] = useState<Session | null>(known ?? null);
  const [missing, setMissing] = useState(false);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState(known?.status ?? "idle");
  const [connected, setConnected] = useState(true);
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [raw, setRaw] = useState(false);
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

  const decided = new Map(
    events
      .filter((e) => e.type === "user.tool_confirmation")
      .map((e) => [String(e.payload.call_hash), String(e.payload.result)]),
  );

  const { blocks, costUsd } = useMemo(() => groupEvents(events), [events]);

  const live = status === "running" || status === "rescheduling";
  // A run stays "running" until the idle checkpoint, well after the agent has
  // answered; only an unanswered turn or an unfinished tool call is real work.
  const last = blocks[blocks.length - 1];
  const working =
    live && (last?.kind === "user" || (last?.kind === "tool" && !last.result));

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
          {session && (
            <span className="muted" title={fullTime(session.created_at)}>
              {agentName(agents, session.agent_id)} · {relativeTime(session.created_at)}
            </span>
          )}
          <span className={`badge ${status}`}>{status}</span>
          {costUsd !== null && (
            <span className="muted mono" title="session cost so far">${costUsd.toFixed(4)}</span>
          )}
          {!connected && status !== "terminated" && (
            <span className="stream-note"><span className="spinner" />reconnecting…</span>
          )}
        </div>
        <div className="row">
          <button
            className="ghost"
            onClick={() => setRaw(!raw)}
            title="show every event exactly as it was recorded"
            aria-pressed={raw}
          >
            {raw ? "Timeline" : "Raw"}
          </button>
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
        {raw
          ? events.map((event) => <RawEvent key={event.seq} event={event} />)
          : blocks.map((block) => (
              <TimelineBlock
                key={block.key}
                block={block}
                decided={decided}
                onConfirm={confirm}
              />
            ))}
        {(status === "rescheduling" || working) && (
          <div className="event system">
            <span className="muted">
              <span className="spinner" />
              {status === "rescheduling" ? "session is waking up…" : "agent is working…"}
            </span>
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

function TimelineBlock({
  block,
  decided,
  onConfirm,
}: {
  block: Block;
  decided: Map<string, string>;
  onConfirm: (hash: string, result: "allow" | "deny") => void;
}) {
  switch (block.kind) {
    case "user":
      return <UserTurn event={block.event} />;
    case "agent":
      return <AgentTurn events={block.events} />;
    case "thinking":
      return <ThinkingNote count={block.count} />;
    case "tool":
      return <ToolCall block={block} decided={decided} onConfirm={onConfirm} />;
    case "artifact":
      return <ArtifactEvent event={block.event} />;
    case "error":
      return <ErrorNote event={block.event} />;
    case "interrupt":
      return <Marker label="interrupted by user" at={block.event.created_at} />;
    default:
      return <RawEvent event={block.event} />;
  }
}

function UserTurn({ event }: { event: SessionEvent }) {
  const body =
    (event.payload.content as { text?: string }[] | undefined)?.map((b) => b.text).join("\n") ?? "";
  return (
    <div className="turn user">
      <div className="bubble">{body}</div>
      <span className="turn-meta">
        {event.principal && `${event.principal} · `}
        <EventTime at={event.created_at} />
      </span>
    </div>
  );
}

function AgentTurn({ events }: { events: SessionEvent[] }) {
  const source = events.map((e) => String(e.payload.text ?? "")).join("\n\n");
  return (
    <div className="turn agent">
      <Markdown source={source} />
      <span className="turn-meta"><EventTime at={events[0].created_at} /></span>
    </div>
  );
}

function ThinkingNote({ count }: { count: number }) {
  return (
    <div className="thinking-note">
      thinking{count > 1 ? ` ×${count}` : ""}…
    </div>
  );
}

function ToolCall({
  block,
  decided,
  onConfirm,
}: {
  block: ToolBlock;
  decided: Map<string, string>;
  onConfirm: (hash: string, result: "allow" | "deny") => void;
}) {
  const call = block.use.payload;
  const result = block.result?.payload;
  const isError = !!result?.is_error;
  const verdict = decided.get(String(call.call_hash));

  if (call.decision === "awaiting_confirmation" && !verdict) {
    return <ApprovalEvent event={block.use} onConfirm={onConfirm} />;
  }

  // Between the decision and the replay the recorded result is still the notice
  // that paused the call; showing it as a failure would misread a granted
  // approval as an error.
  const settling = call.decision === "awaiting_confirmation";
  const flag = settling
    ? verdict === "allow"
      ? "approved"
      : "denying"
    : call.decision === "user_denied"
      ? "denied"
      : call.decision === "not_allowed"
        ? "not allowed"
        : call.decision === "killed"
          ? "killed"
          : isError
            ? "error"
            : "";
  const state = settling ? "wait" : flag ? "err" : block.result ? "ok" : "wait";
  const summary = toolSummary(call.input);

  return (
    <details className="tool-call">
      <summary>
        <span className={`dot ${state}`} />
        <span className="mono">{String(call.tool_name ?? "tool")}</span>
        {summary && <span className="tool-arg mono">{summary}</span>}
        {flag && (
          <span className={`badge ${settling ? "rescheduling" : "terminated"}`}>{flag}</span>
        )}
        <EventTime at={block.use.created_at} />
      </summary>
      <pre>{JSON.stringify(call.input ?? {}, null, 2)}</pre>
      {block.result && !settling && (
        <pre className={isError ? "err" : ""}>{prettifyResult(String(result?.content ?? ""))}</pre>
      )}
    </details>
  );
}

function ErrorNote({ event }: { event: SessionEvent }) {
  return (
    <div className="turn error">
      <span className="turn-meta">
        <span className="badge terminated">error</span> <EventTime at={event.created_at} />
      </span>
      <pre>{String(event.payload.error ?? "")}</pre>
    </div>
  );
}

function Marker({ label, at }: { label: string; at: string }) {
  return (
    <div className="timeline-marker">
      <span>{label} <EventTime at={at} /></span>
    </div>
  );
}

function RawEvent({ event }: { event: SessionEvent }) {
  return (
    <details className="tool-call">
      <summary>
        <span className="mono">{event.type}</span>
        {event.principal && <span className="tool-arg">{event.principal}</span>}
        <EventTime at={event.created_at} />
      </summary>
      <pre>{JSON.stringify(event.payload, null, 2)}</pre>
    </details>
  );
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
    <div className="artifact-row">
      <span>
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
  onConfirm,
}: {
  event: SessionEvent;
  onConfirm: (hash: string, result: "allow" | "deny") => void;
}) {
  const hash = String(event.payload.call_hash);
  return (
    <div className="event ask">
      <div className="row between">
        <span>
          <span className="badge requires_action">approval</span>{" "}
          <strong>{String(event.payload.tool_name ?? "")}</strong>{" "}
          <EventTime at={event.created_at} />
        </span>
        <span className="row">
          <button className="primary" onClick={() => onConfirm(hash, "allow")}>Allow</button>
          <button className="danger" onClick={() => onConfirm(hash, "deny")}>Deny</button>
        </span>
      </div>
      <pre>{JSON.stringify(event.payload.input, null, 2)}</pre>
    </div>
  );
}
