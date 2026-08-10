"use client";

import { useState } from "react";
import { api, Agent, Environment } from "@/lib/api";

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
  const [name, setName] = useState("");
  const [model, setModel] = useState("claude-sonnet-5");
  const [environmentId, setEnvironmentId] = useState("");
  const [instructions, setInstructions] = useState("");
  const [askByDefault, setAskByDefault] = useState(true);
  const [error, setError] = useState("");

  async function create() {
    try {
      await api("/v1/agents", {
        json: {
          name,
          model,
          environment_id: environmentId || environments[0]?.id,
          instructions: instructions || null,
          permission_policy: { default: askByDefault ? "always_ask" : "always_allow", rules: [] },
        },
      });
      setShowForm(false);
      setName("");
      setInstructions("");
      onChange();
    } catch (e) {
      setError(String(e));
    }
  }

  async function toggleKill(agent: Agent) {
    await api(`/v1/agents/${agent.id}`, { method: "PATCH", json: { disabled: !agent.disabled } });
    onChange();
  }

  return (
    <div className="panel">
      <div className="row between" style={{ marginBottom: 12 }}>
        <strong>Agents</strong>
        <button className="primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "New agent"}
        </button>
      </div>

      {showForm && (
        <div className="panel" style={{ background: "var(--panel2)" }}>
          <div className="grid2">
            <div>
              <label>name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label>model</label>
              <input value={model} onChange={(e) => setModel(e.target.value)} />
            </div>
          </div>
          <label>environment</label>
          <select value={environmentId} onChange={(e) => setEnvironmentId(e.target.value)}>
            {environments.map((env) => (
              <option key={env.id} value={env.id}>{env.name}</option>
            ))}
          </select>
          <label>instructions</label>
          <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} />
          <label className="row" style={{ marginTop: 10 }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={askByDefault}
              onChange={(e) => setAskByDefault(e.target.checked)}
            />
            require approval for every tool call (always_ask)
          </label>
          {error && <p className="muted" style={{ color: "var(--danger)" }}>{error}</p>}
          <div style={{ marginTop: 12 }}>
            <button className="primary" onClick={create} disabled={!name}>Create</button>
          </div>
        </div>
      )}

      <table>
        <thead>
          <tr><th>name</th><th>version</th><th>kill switch</th><th /></tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id}>
              <td>{agent.name} <span className="muted mono">{agent.id}</span></td>
              <td>v{agent.latest_version}</td>
              <td>
                {agent.disabled
                  ? <span className="badge terminated">disabled</span>
                  : <span className="badge running">active</span>}
              </td>
              <td style={{ textAlign: "right" }}>
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
  );
}
