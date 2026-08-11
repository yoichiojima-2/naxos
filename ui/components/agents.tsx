"use client";

import { useState } from "react";
import { api, Agent, AgentIn, Environment } from "@/lib/api";
import AgentForm from "@/components/agent-form";

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

  return (
    <>
      <div className="row between" style={{ marginBottom: 12 }}>
        <span className="muted">{agents.length} agent{agents.length === 1 ? "" : "s"}</span>
        <button className={showForm ? "ghost" : "primary"} onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "New agent"}
        </button>
      </div>

      {showForm && (
        <AgentForm
          environments={environments}
          submitLabel="Create agent"
          onSubmit={create}
          onCancel={() => setShowForm(false)}
        />
      )}

      <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Environment</th><th>Version</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {agents.length === 0 && (
                <tr><td className="empty" colSpan={5}>no agents yet — create one to get started.</td></tr>
              )}
              {agents.map((agent) => (
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
                  <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
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
