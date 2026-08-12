"use client";

import { SessionEvent } from "@/lib/api";
import { Block, ToolBlock, prettifyResult, toolSummary } from "@/lib/timeline";
import { fullTime } from "@/lib/format";
import Markdown from "@/components/markdown";

export function EventTime({ at }: { at: string }) {
  return (
    <span className="event-time" title={fullTime(at)}>
      {new Date(at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export default function TimelineBlock({
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
      return <Marker label="interrupted" at={block.event.created_at} />;
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
      Thinking{count > 1 ? ` ×${count}` : ""}…
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

export function RawEvent({ event }: { event: SessionEvent }) {
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
