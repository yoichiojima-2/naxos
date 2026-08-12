"use client";

import { useCallback, useEffect, useState } from "react";
import { agentName, api, Agent, Session } from "@/lib/api";
import { fullTime, relativeTime, shortId } from "@/lib/format";
import FavoriteStar, { FavoriteProps, useFavoriteFilter } from "@/components/favorite-star";
import CountHeader from "@/components/list-header";
import FilterInput from "@/components/filter-input";
import TableStates from "@/components/table-states";
import { alertDialog, confirmDialog, promptDialog } from "@/components/confirm";

const STATUS_FILTERS = ["needs approval", "running", "idle", "rescheduling", "terminated"] as const;

const statusOf = (s: Session) =>
  s.status === "idle" && s.stop_reason === "requires_action" ? "needs approval" : s.status;

/** The operator view of sessions: filter, budget, terminate, delete in bulk.
 * Conversations themselves live at #chat/{id}. */
export default function Sessions({
  agents,
  favorites,
  onToggleFavorite,
  onChange,
}: { agents: Agent[]; onChange?: () => void } & FavoriteProps) {
  const [sessions, setSessions] = useState<Session[] | null>(null);
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
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

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
    onChange?.();
    if (failed.length) {
      await alertDialog(
        "Some requests failed",
        `${failed.length} of ${ids.length} requests failed; the failed ${
          failed.length === 1 ? "session stays" : "sessions stay"} selected.`,
      );
    }
  }

  async function bulkSetBudget() {
    const raw = await promptDialog({
      title: `Set budget for ${selected.size} ${noun}`,
      body: "The hard cap in USD each session may spend before it pauses.",
      action: "Set budget",
      placeholder: "10.00",
    });
    if (raw === null || !raw.trim()) return;
    const budget = Number(raw);
    if (!Number.isFinite(budget) || budget < 0) {
      await alertDialog("Invalid budget", `"${raw}" is not a valid budget.`);
      return;
    }
    await bulkApply((id) =>
      api(`/v1/sessions/${id}`, { method: "PATCH", json: { budget_usd: budget } }),
    );
  }

  async function bulkTerminate() {
    const ok = await confirmDialog({
      title: `Terminate ${selected.size} ${noun}?`,
      body: "Running sandboxes stop and the sessions accept no further messages.",
      action: "Terminate",
      danger: true,
    });
    if (!ok) return;
    await bulkApply((id) => api(`/v1/sessions/${id}/terminate`, { json: {} }));
  }

  async function bulkDelete() {
    const ok = await confirmDialog({
      title: `Delete ${selected.size} ${noun} permanently?`,
      body:
        "Events, workspace files, and artifacts are removed and shared artifact links stop " +
        "working. Sessions with a live sandbox are refused.",
      action: "Delete",
      danger: true,
    });
    if (!ok) return;
    await bulkApply((id) => api(`/v1/sessions/${id}`, { method: "DELETE" }));
  }

  return (
    <>
      <CountHeader count={sessions === null ? null : filtered.length} of={sessions?.length} noun="session">
        {selected.size > 0 && (
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
            <TableStates
              items={sessions}
              filtered={filtered}
              colSpan={8}
              empty="no sessions yet — start a chat to create one."
              noMatch="no sessions match the current filters."
            />
            {filtered.map((s) => {
              const status = statusOf(s);
              const needsAction = status === "needs approval";
              return (
                <tr key={s.id} className="click" onClick={() => { window.location.hash = `#chat/${s.id}`; }}>
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
                      {status}
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
