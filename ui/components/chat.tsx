"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  agentName,
  api,
  Agent,
  EVENT_TYPES,
  STREAM_DELTA_TYPE,
  Session,
  SessionEvent,
  StreamDelta,
  WorkspaceFile,
} from "@/lib/api";
import { groupEvents } from "@/lib/timeline";
import { fullTime, relativeTime, shortId } from "@/lib/format";
import TimelineBlock, { RawEvent } from "@/components/chat-blocks";
import Composer from "@/components/composer";
import FavoriteStar, { FavoriteProps } from "@/components/favorite-star";
import Markdown from "@/components/markdown";

/** Tell the sidebar the session list changed (new title, new session). */
export const bumpSessions = () => window.dispatchEvent(new Event("naxos-sessions"));

export default function Chat({
  sessionId,
  agents,
  favorites,
  onToggleFavorite,
}: { sessionId: string; agents: Agent[] } & FavoriteProps) {
  const [session, setSession] = useState<Session | null>(null);
  const [missing, setMissing] = useState(false);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [status, setStatus] = useState("idle");
  const [stopReason, setStopReason] = useState<string | null>(null);
  const [connected, setConnected] = useState(true);
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [raw, setRaw] = useState(false);
  // The partial text of the message currently generating, keyed by its stream
  // id. The persisted agent.message carrying the same id supersedes it.
  const [streaming, setStreaming] = useState<{ stream: string; text: string } | null>(null);
  const finalized = useRef<Set<string>>(new Set());
  const untitled = useRef(true);
  const endRef = useRef<HTMLDivElement | null>(null);
  const followed = useRef(false);

  useEffect(() => {
    api<Session>(`/v1/sessions/${sessionId}`)
      .then((s) => {
        setSession(s);
        setStatus(s.status);
        setStopReason(s.stop_reason);
        untitled.current = s.title == null;
      })
      .catch(() => setMissing(true));
  }, [sessionId]);

  // The first user.message titles the session server-side; pick the title up
  // without a reload and let the sidebar know.
  useEffect(() => {
    if (!untitled.current) return;
    if (!events.some((e) => e.type === "user.message")) return;
    untitled.current = false;
    api<Session>(`/v1/sessions/${sessionId}`)
      .then((s) => {
        setSession(s);
        bumpSessions();
      })
      .catch(() => undefined);
  }, [events, sessionId]);

  useEffect(() => {
    if (!events.length) return;
    const main = endRef.current?.closest(".main");
    const nearBottom = main
      ? main.scrollHeight - main.scrollTop - main.clientHeight < 240
      : true;
    if (!followed.current || nearBottom) {
      endRef.current?.scrollIntoView({ block: "end" });
      followed.current = true;
    }
  }, [events.length, streaming?.text.length]);

  async function loadFiles() {
    if (files) { setFiles(null); return; }
    const result = await api<{ data: WorkspaceFile[] }>(`/v1/sessions/${sessionId}/workspace`);
    setFiles(result.data);
  }

  useEffect(() => {
    const es = new EventSource(`/v1/sessions/${sessionId}/events?stream=sse`);
    const push = (rawEvent: MessageEvent) => {
      setConnected(true);
      const event = JSON.parse(rawEvent.data) as SessionEvent;
      setEvents((prev) => (prev.some((e) => e.seq === event.seq) ? prev : [...prev, event]));
      if (event.type === "agent.message") {
        // The durable message replaces its live preview. A missing stream id
        // means streaming was off for that block — clear whatever is showing.
        const stream = typeof event.payload.stream === "string" ? event.payload.stream : "";
        if (stream) finalized.current.add(stream);
        setStreaming((prev) => (prev && stream && prev.stream !== stream ? prev : null));
      }
      if (event.type === "session.status_running") {
        setStatus("running");
        setStopReason(null);
      }
      if (event.type === "session.status_idle") {
        setStatus("idle");
        setStopReason((event.payload.stop_reason as string) ?? null);
        setStreaming(null);
      }
      if (event.type === "user.interrupt") setStreaming(null);
      if (event.type === "session.status_rescheduling") setStatus("rescheduling");
      if (event.type === "session.status_terminated") {
        setStatus("terminated");
        setStreaming(null);
        es.close(); // the server ends the stream here; without close() EventSource reconnects forever
      }
    };
    // Named SSE events require a listener per type; a catch-all keeps this simple.
    EVENT_TYPES.forEach((type) => es.addEventListener(type, push));
    es.addEventListener(STREAM_DELTA_TYPE, (rawEvent: MessageEvent) => {
      setConnected(true);
      const delta = JSON.parse(rawEvent.data) as StreamDelta;
      const stream = delta.stream ?? "";
      if (!stream || finalized.current.has(stream)) return;
      setStreaming((prev) =>
        prev && prev.stream === stream
          ? { stream, text: prev.text + (delta.text ?? "") }
          : { stream, text: delta.text ?? "" },
      );
    });
    es.onopen = () => setConnected(true);
    // A CLOSED source never retries (e.g. the session does not exist), so
    // "reconnecting…" would be a lie; the missing-session state covers it.
    es.onerror = () => setConnected(es.readyState === EventSource.CLOSED);
    return () => es.close();
  }, [sessionId]);

  function emit(event: Record<string, unknown>) {
    return api(`/v1/sessions/${sessionId}/events`, { json: { events: [event] } });
  }

  async function confirm(callHash: string, result: "allow" | "deny") {
    await emit({ type: "user.tool_confirmation", call_hash: callHash, result });
  }

  const decided = new Map(
    events
      .filter((e) => e.type === "user.tool_confirmation")
      .map((e) => [String(e.payload.call_hash), String(e.payload.result)]),
  );

  const { blocks, costUsd, modelRequestOpen } = useMemo(() => groupEvents(events), [events]);

  const live = status === "running" || status === "rescheduling";
  // A run stays "running" until the idle checkpoint, well after the agent has
  // answered, so the status alone would keep claiming work that is over. Real
  // work is a model request in flight, a tool still running, or a turn the
  // agent has not started answering yet.
  const last = blocks[blocks.length - 1];
  const working =
    live &&
    (modelRequestOpen || last?.kind === "user" || (last?.kind === "tool" && !last.result));

  if (missing) {
    return (
      <div className="chat">
        <div className="chat-col chat-body">
          <p className="muted mt16">
            This conversation could not be loaded — it may have been deleted.{" "}
            <a className="mono" href="#new">Start a new chat</a>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat">
      <header className="chat-head">
        <div className="chat-col chat-head-row">
          <span className="chat-title" title={session?.title ?? sessionId}>
            {session?.title ?? shortId(sessionId)}
          </span>
          {session && (
            <span className="chat-sub" title={fullTime(session.created_at)}>
              {agentName(agents, session.agent_id)} · {relativeTime(session.created_at)}
            </span>
          )}
          <span
            className={`badge ${
              status === "idle" && stopReason === "requires_action" ? "requires_action" : status
            }`}
          >
            {status === "idle" && stopReason === "requires_action" ? "needs approval" : status}
          </span>
          {costUsd !== null && (
            <span className="chat-sub mono" title="session cost so far">${costUsd.toFixed(4)}</span>
          )}
          {!connected && status !== "terminated" && (
            <span className="stream-note"><span className="spinner" />reconnecting…</span>
          )}
          <span className="chat-head-spacer" />
          <FavoriteStar
            type="session"
            id={sessionId}
            favorites={favorites}
            onToggleFavorite={onToggleFavorite}
          />
          <button
            className="icon-btn"
            onClick={() => setRaw(!raw)}
            title="show every event exactly as it was recorded"
            aria-pressed={raw}
          >
            {raw ? "Chat" : "Raw"}
          </button>
          <button className="icon-btn" onClick={loadFiles} aria-pressed={!!files}>
            Files
          </button>
        </div>
      </header>

      <div className="chat-col chat-body">
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
          {!raw && streaming && streaming.text && (
            <div className="turn agent streaming">
              <Markdown source={streaming.text} />
              <span className="turn-meta stream-caret">…</span>
            </div>
          )}
          {(status === "rescheduling" || (working && !streaming?.text)) && (
            <div className="event system">
              <span className="muted">
                <span className="spinner" />
                {status === "rescheduling" ? "waking up…" : "working…"}
              </span>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="chat-foot">
        <div className="chat-col">
          <Composer
            placeholder={`Message ${session ? agentName(agents, session.agent_id) : "the agent"}…`}
            disabled={status === "terminated"}
            working={live}
            draftKey={sessionId}
            onSend={async (text) => {
              await emit({ type: "user.message", content: [{ type: "text", text }] });
            }}
            onStop={() => emit({ type: "user.interrupt" })}
          />
        </div>
      </div>
    </div>
  );
}
