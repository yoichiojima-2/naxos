"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ToolConfirmation } from "@/lib/api";
import CountHeader from "@/components/list-header";
import FilterInput from "@/components/filter-input";
import TableStates from "@/components/table-states";
import { toolSummary } from "@/lib/timeline";
import { fullTime, relativeTime, shortId } from "@/lib/format";

const POLL_MS = 10_000;
const EMPTY = "nothing is waiting on a human right now.";

export default function Approvals({ onDecided }: { onDecided?: () => void }) {
  const [items, setItems] = useState<ToolConfirmation[] | null>(null);
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const result = await api<{ data: ToolConfirmation[] }>("/v1/tool_confirmations");
    setItems(result.data);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Deciding goes through the session's event stream, not a side channel: the same
  // path a decision made from the timeline takes, so audit and resume are identical.
  async function decide(item: ToolConfirmation, result: "allow" | "deny") {
    setBusy(item.id);
    try {
      await api(`/v1/sessions/${item.session_id}/events`, {
        json: {
          events: [
            { type: "user.tool_confirmation", call_hash: item.call_hash, result },
          ],
        },
      });
      setItems((prev) => (prev ?? []).filter((c) => c.id !== item.id));
      onDecided?.();
    } finally {
      setBusy(null);
    }
  }

  const q = query.trim().toLowerCase();
  const filtered = (items ?? []).filter(
    (c) =>
      !q ||
      [c.tool_name, c.agent_name, c.session_title ?? "", c.session_id, c.requested_for ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q),
  );

  return (
    <>
      <CountHeader count={items && filtered.length} of={items?.length} noun="pending approval">
        <FilterInput value={query} onChange={setQuery} placeholder="filter approvals" />
      </CountHeader>
      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>tool</th>
                <th>agent</th>
                <th>session</th>
                <th>for</th>
                <th>waiting</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <TableStates
                items={items}
                filtered={filtered}
                colSpan={6}
                empty={EMPTY}
                noMatch="no approval matches that filter."
              />
              {filtered.map((c) => (
                <Row
                  key={c.id}
                  item={c}
                  open={openId === c.id}
                  busy={busy === c.id}
                  onToggle={() => setOpenId((prev) => (prev === c.id ? null : c.id))}
                  onDecide={decide}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Row({
  item,
  open,
  busy,
  onToggle,
  onDecide,
}: {
  item: ToolConfirmation;
  open: boolean;
  busy: boolean;
  onToggle: () => void;
  onDecide: (item: ToolConfirmation, result: "allow" | "deny") => void;
}) {
  const summary = toolSummary(item.input);
  return (
    <>
      <tr className="click" onClick={onToggle}>
        <td className="approval-tool">
          <strong>{item.tool_name}</strong>
          {summary && <div className="muted mono tool-arg">{summary}</div>}
        </td>
        <td>
          <a href={`#agents/${item.agent_id}`} onClick={(e) => e.stopPropagation()}>
            {item.agent_name}
          </a>
          {item.agent_disabled && <span className="badge terminated">disabled</span>}
        </td>
        <td>
          <a href={`#sessions/${item.session_id}`} onClick={(e) => e.stopPropagation()}>
            {item.session_title || shortId(item.session_id)}
          </a>
        </td>
        <td className="muted">{item.requested_for ?? "—"}</td>
        <td className="muted" title={fullTime(item.requested_at)}>
          {relativeTime(item.requested_at)}
          {item.expires_at && (
            <div className="muted">expires {relativeTime(item.expires_at)}</div>
          )}
        </td>
        <td onClick={(e) => e.stopPropagation()}>
          <div className="row end nowrap">
            <button className="primary" disabled={busy} onClick={() => onDecide(item, "allow")}>
              Allow
            </button>
            <button className="danger" disabled={busy} onClick={() => onDecide(item, "deny")}>
              Deny
            </button>
          </div>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            <pre className="prewrap mono">{JSON.stringify(item.input, null, 2)}</pre>
          </td>
        </tr>
      )}
    </>
  );
}
