"use client";

import { useState } from "react";
import { api, Agent, AgentIn, Environment } from "@/lib/api";
import AgentForm from "@/components/agent-form";
import CountHeader from "@/components/list-header";
import FilterInput from "@/components/filter-input";

export default function Agents({
  agents,
  environments,
  onChange,
}: {
  agents: Agent[];
  environments: Environment[];
  onChange: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [query, setQuery] = useState("");

  async function create(body: AgentIn) {
    const created = await api<Agent>("/v1/agents", { json: body });
    setShowForm(false);
    onChange();
    window.location.hash = `#agents/${created.id}`;
  }

  async function toggleKill(agent: Agent) {
    await api(`/v1/agents/${agent.id}`, { method: "PATCH", json: { disabled: !agent.disabled } });
    onChange();
  }

  const envName = (id: string) => environments.find((e) => e.id === id)?.name ?? id;

  const q = query.trim().toLowerCase();
  const filtered = agents.filter(
    (a) => !q || `${a.name} ${a.id} ${envName(a.environment_id)}`.toLowerCase().includes(q),
  );

  return (
    <>
      <CountHeader count={filtered.length} of={agents.length} noun="agent">
        {agents.length > 0 && (
          <FilterInput placeholder="Filter agents…" value={query} onChange={setQuery} />
        )}
        <button className={showForm ? "ghost" : "primary"} onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "New agent"}
        </button>
      </CountHeader>

      {showForm && (
        <AgentForm
          environments={environments}
          submitLabel="Create agent"
          onSubmit={create}
          onCancel={() => setShowForm(false)}
        />
      )}

      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Environment</th><th>Version</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {agents.length === 0 && (
                <tr><td className="empty" colSpan={5}>no agents yet — create one to get started.</td></tr>
              )}
              {agents.length > 0 && filtered.length === 0 && (
                <tr><td className="empty" colSpan={5}>no agents match the current filter.</td></tr>
              )}
              {filtered.map((agent) => (
                <tr
                  key={agent.id}
                  className="click"
                  onClick={() => { window.location.hash = `#agents/${agent.id}`; }}
                >
                  <td>
                    {agent.name}{" "}
                    <span className="muted mono">{agent.id}</span>
                  </td>
                  <td className="muted">{envName(agent.environment_id)}</td>
                  <td>v{agent.latest_version}</td>
                  <td>
                    {agent.disabled
                      ? <span className="badge terminated">disabled</span>
                      : <span className="badge running">active</span>}
                  </td>
                  <td className="ta-right" onClick={(e) => e.stopPropagation()}>
                    <button
                      className={agent.disabled ? "ghost" : "danger"}
                      onClick={() => toggleKill(agent)}
                    >
                      {agent.disabled ? "Enable" : "Kill"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
