"use client";

import { useCallback, useEffect, useState } from "react";
import { agentName, api, Agent, Environment, Session } from "@/lib/api";

const sessionStatus = (session: Session) =>
  session.status === "idle" && session.stop_reason === "requires_action"
    ? "needs approval"
    : session.status;

export default function Dashboard({
  agents,
  environments,
}: {
  agents: Agent[];
  environments: Environment[];
}) {
  const [sessions, setSessions] = useState<Session[] | null>(null);

  const refresh = useCallback(async () => {
    const result = await api<{ data: Session[] }>("/v1/sessions?limit=200");
    setSessions(result.data);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const recent = sessions?.slice(0, 6) ?? [];
  const running = sessions?.filter((session) => session.status === "running").length ?? 0;
  const approvals = sessions?.filter(
    (session) => session.status === "idle" && session.stop_reason === "requires_action",
  ).length ?? 0;
  const spend = sessions?.reduce((total, session) => total + Number(session.cost_usd || 0), 0) ?? 0;

  return (
    <div className="dashboard-stack">
      <section className="metric-grid" aria-label="Workspace summary">
        <a className="metric-card" href="#sessions">
          <span>Active sessions</span><strong>{sessions === null ? "—" : running}</strong>
          <small>{approvals ? `${approvals} waiting for approval` : "No approvals waiting"}</small>
        </a>
        <a className="metric-card" href="#agents">
          <span>Agents</span><strong>{agents.length}</strong>
          <small>{agents.filter((agent) => !agent.disabled).length} enabled</small>
        </a>
        <a className="metric-card" href="#deployments">
          <span>Environments</span><strong>{environments.length}</strong>
          <small>Available execution targets</small>
        </a>
        <a className="metric-card" href="#sessions">
          <span>Recent spend</span><strong>${spend.toFixed(2)}</strong>
          <small>Across the latest {sessions?.length ?? 0} sessions</small>
        </a>
      </section>

      <section className="panel dashboard-recent">
        <div className="dashboard-section-head">
          <div><h2>Recent sessions</h2><p>Latest activity across your agent workspace.</p></div>
          <a href="#sessions">View all sessions →</a>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>session</th><th>agent</th><th>status</th><th>cost</th><th>created</th></tr></thead>
            <tbody>
              {sessions === null && <tr><td className="empty" colSpan={5}>loading…</td></tr>}
              {sessions?.length === 0 && <tr><td className="empty" colSpan={5}>No sessions yet.</td></tr>}
              {recent.map((session) => (
                <tr className="click" key={session.id} onClick={() => { window.location.hash = "sessions"; }}>
                  <td><strong>{session.title || "Untitled session"}</strong></td>
                  <td>{agentName(agents, session.agent_id)}</td>
                  <td><span className={`badge ${sessionStatus(session).replace(" ", "-")}`}>{sessionStatus(session)}</span></td>
                  <td>${Number(session.cost_usd || 0).toFixed(2)}</td>
                  <td>{new Date(session.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
