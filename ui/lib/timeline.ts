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

// Status events drive the header badge, confirmations drive the approval
// buttons, and a request start carries no payload — showing them again as rows
// only adds noise.
const HIDDEN = new Set([
  "session.status_running",
  "session.status_idle",
  "session.status_rescheduling",
  "session.status_terminated",
  "user.tool_confirmation",
  "span.model_request_start",
]);

const RESULT_TYPES = new Set(["agent.tool_result", "user.custom_tool_result"]);

const text = (value: unknown) => (typeof value === "string" ? value : "");

export function groupEvents(events: SessionEvent[]): {
  blocks: Block[];
  costUsd: number | null;
} {
  const blocks: Block[] = [];
  const byToolUseId = new Map<string, ToolBlock>();
  const byCallHash = new Map<string, ToolBlock>();
  let costUsd: number | null = null;

  const index = (block: ToolBlock) => {
    const id = text(block.use.payload.tool_use_id);
    const hash = text(block.use.payload.call_hash);
    if (id) byToolUseId.set(id, block);
    if (hash) byCallHash.set(hash, block);
  };

  for (const event of events) {
    const payload = event.payload;

    if (event.type === "span.model_request_end") {
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
      const pendingApproval = byCallHash.get(text(payload.call_hash));
      if (
        payload.decision !== "awaiting_confirmation" &&
        pendingApproval?.use.payload.decision === "awaiting_confirmation"
      ) {
        pendingApproval.use = event;
        index(pendingApproval);
      } else {
        const block: ToolBlock = { kind: "tool", key: event.seq, use: event };
        blocks.push(block);
        index(block);
      }
      continue;
    }

    if (RESULT_TYPES.has(event.type)) {
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

  return { blocks, costUsd };
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
  const texts = value.map((item) =>
    item && typeof item === "object" && typeof (item as { text?: unknown }).text === "string"
      ? (item as { text: string }).text
      : null,
  );
  return texts.every((t) => t !== null) ? (texts as string[]) : [];
}

// Tool results reach older sessions as the Python repr of a content-block list;
// show the text the agent actually saw rather than the wrapper.
export function prettifyResult(content: string): string {
  const trimmed = content.trim();
  if (!trimmed.startsWith("[") && !trimmed.startsWith("{")) return content;
  try {
    const parsed = JSON.parse(trimmed);
    const texts = textsOf(parsed);
    return texts.length ? texts.join("\n\n") : JSON.stringify(parsed, null, 2);
  } catch {
    const segments = [...trimmed.matchAll(PYTHON_TEXT)].map((match) =>
      (match[1] ?? match[2] ?? "").replace(/\\(.)/g, (raw, char) => PYTHON_ESCAPES[char] ?? raw),
    );
    return segments.length ? segments.join("\n\n") : content;
  }
}
