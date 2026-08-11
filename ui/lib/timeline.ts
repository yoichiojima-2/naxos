import { SessionEvent } from "@/lib/api";

export type ToolBlock = {
  kind: "tool";
  key: number;
  use: SessionEvent;
  result?: SessionEvent;
};

export type Block =
  | { kind: "user"; key: number; event: SessionEvent }
  | { kind: "agent"; key: number; events: SessionEvent[] }
  | { kind: "thinking"; key: number; count: number; event: SessionEvent }
  | ToolBlock
  | { kind: "artifact" | "error" | "interrupt" | "raw"; key: number; event: SessionEvent };

// Status events drive the header badge and confirmations drive the approval
// buttons — showing them again as rows only adds noise. The model request spans
// are consumed above, for cost and for whether a request is still open.
const HIDDEN = new Set([
  "session.status_running",
  "session.status_idle",
  "session.status_rescheduling",
  "session.status_terminated",
  "user.tool_confirmation",
]);

const RESULT_TYPE = "agent.tool_result";

const text = (value: unknown) => (typeof value === "string" ? value : "");

export function groupEvents(events: SessionEvent[]): {
  blocks: Block[];
  costUsd: number | null;
  modelRequestOpen: boolean;
} {
  const blocks: Block[] = [];
  const byToolUseId = new Map<string, ToolBlock>();
  let costUsd: number | null = null;
  // A start with no end yet means the model is generating — thinking and a
  // half-streamed message both sit inside that span, and it closes as soon as
  // the turn is answered even though the session stays running until the idle
  // checkpoint.
  let openRequests = 0;

  const awaiting = (hash: string) =>
    blocks.find(
      (b) =>
        b.kind === "tool" &&
        b.use.payload.decision === "awaiting_confirmation" &&
        text(b.use.payload.call_hash) === hash,
    ) as ToolBlock | undefined;

  for (const event of events) {
    const payload = event.payload;

    if (event.type === "span.model_request_start") {
      openRequests += 1;
      continue;
    }
    if (event.type === "span.model_request_end") {
      openRequests = Math.max(0, openRequests - 1);
      const cost = Number(payload.cost_usd);
      if (Number.isFinite(cost)) costUsd = cost;
      continue;
    }
    if (HIDDEN.has(event.type)) continue;

    if (event.type === "agent.message") {
      const last = blocks[blocks.length - 1];
      if (last?.kind === "agent") last.events.push(event);
      else blocks.push({ kind: "agent", key: event.seq, events: [event] });
      continue;
    }

    if (event.type === "agent.thinking") {
      const last = blocks[blocks.length - 1];
      if (last?.kind === "thinking") last.count += 1;
      else blocks.push({ kind: "thinking", key: event.seq, count: 1, event });
      continue;
    }

    if (event.type === "agent.tool_use") {
      // A resumed call replays with a new tool_use_id but the same call_hash;
      // it settles the row that asked for approval instead of adding another.
      // The pause denied the call, so its result is the "needs approval"
      // notice — the replay supersedes it. call_hash has no per-invocation
      // nonce, so identical concurrent calls share one; settle them in order.
      const hash = text(payload.call_hash);
      const settled =
        payload.decision === "awaiting_confirmation" || !hash ? undefined : awaiting(hash);
      const block = settled ?? { kind: "tool" as const, key: event.seq, use: event };
      if (settled) {
        settled.use = event;
        settled.result = undefined;
      } else {
        blocks.push(block);
      }
      const id = text(payload.tool_use_id);
      if (id) byToolUseId.set(id, block);
      continue;
    }

    if (event.type === RESULT_TYPE) {
      const matched = byToolUseId.get(text(payload.tool_use_id));
      const target =
        matched && !matched.result
          ? matched
          : (blocks.find((b) => b.kind === "tool" && !b.result) as ToolBlock | undefined);
      if (target) target.result = event;
      else blocks.push({ kind: "raw", key: event.seq, event });
      continue;
    }

    const kind =
      event.type === "user.message"
        ? "user"
        : event.type === "user.interrupt"
          ? "interrupt"
          : event.type === "agent.artifact"
            ? "artifact"
            : event.type === "session.error"
              ? "error"
              : "raw";
    blocks.push({ kind, key: event.seq, event });
  }

  return { blocks, costUsd, modelRequestOpen: openRequests > 0 };
}

const SUMMARY_KEYS = ["command", "file_path", "path", "pattern", "query", "url", "name", "prompt"];

export function toolSummary(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const record = input as Record<string, unknown>;
  const usable = (key: string) => typeof record[key] === "string" && record[key] !== "";
  const key = SUMMARY_KEYS.find(usable) ?? Object.keys(record).find(usable);
  if (!key && !Object.keys(record).length) return "";
  const value = key ? String(record[key]) : JSON.stringify(record);
  const line = value.replace(/\s+/g, " ").trim();
  return line.length > 88 ? `${line.slice(0, 88)}…` : line;
}

const PYTHON_TEXT = /'text':\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")/g;
const PYTHON_ESCAPES: Record<string, string> = {
  n: "\n",
  t: "\t",
  r: "\r",
  "'": "'",
  '"': '"',
  "\\": "\\",
};

function textsOf(value: unknown): string[] {
  if (!Array.isArray(value) || !value.length) return [];
  const texts = value.map((item) => {
    const block = item as { type?: unknown; text?: unknown };
    return block?.type === "text" && typeof block.text === "string" ? block.text : null;
  });
  return texts.every((t) => t !== null) ? (texts as string[]) : [];
}

// Sessions from before the harness sent plain text hold the Python repr of a
// content-block list; show the text the agent saw rather than the wrapper.
// Anything that is not recognisably that wrapper is left exactly as recorded.
export function prettifyResult(content: string): string {
  const trimmed = content.trim();
  if (!trimmed.startsWith("[")) return content;
  try {
    const texts = textsOf(JSON.parse(trimmed));
    return texts.length ? texts.join("\n\n") : content;
  } catch {
    const segments = [...trimmed.matchAll(PYTHON_TEXT)].map((match) =>
      (match[1] ?? match[2] ?? "").replace(/\\(.)/g, (raw, char) => PYTHON_ESCAPES[char] ?? raw),
    );
    return segments.length ? segments.join("\n\n") : content;
  }
}
