"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Agent, Deployment, DeploymentRun } from "@/lib/api";

export default function Deployments({ agents }: { agents: Agent[] }) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [runs, setRuns] = useState<Record<string, DeploymentRun[]>>({});
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const result = await api<{ data: Deployment[] }>("/v1/deployments");
    setDeployments(result.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function create() {
    try {
      await api("/v1/deployments", {
        json: {
          name,
          agent_id: agentId || agents[0]?.id,
          cron,
          initial_events: [
            { type: "user.message", content: [{ type: "text", text: prompt }] },
          ],
        },
      });
      setShowForm(false);
      setName("");
      setPrompt("");
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function action(id: string, verb: string) {
    await api(`/v1/deployments/${id}/${verb}`, { json: {} });
    refresh();
    if (verb === "run") showRuns(id);
  }

  async function showRuns(id: string) {
    const result = await api<{ data: DeploymentRun[] }>(`/v1/deployments/${id}/runs`);
    setRuns((prev) => ({ ...prev, [id]: result.data }));
  }

  return (
    <div className="panel">
      <div className="row between" style={{ marginBottom: 12 }}>
        <strong>Scheduled deployments</strong>
        <button className={showForm ? "ghost" : "primary"} onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "New deployment"}
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
              <label>cron (Asia/Tokyo)</label>
              <input value={cron} onChange={(e) => setCron(e.target.value)} className="mono" />
            </div>
          </div>
          <label>agent</label>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <label>prompt for each run</label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          {error && <p className="muted" style={{ color: "var(--danger)" }}>{error}</p>}
          <div style={{ marginTop: 12 }}>
            <button className="primary" onClick={create} disabled={!name || !prompt}>Create</button>
          </div>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>name</th><th>cron</th><th>state</th><th /></tr>
          </thead>
          <tbody>
            {deployments.map((d) => (
              <DeploymentRow
                key={d.id}
                deployment={d}
                runs={runs[d.id]}
                onAction={action}
                onShowRuns={showRuns}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DeploymentRow({
  deployment,
  runs,
  onAction,
  onShowRuns,
}: {
  deployment: Deployment;
  runs?: DeploymentRun[];
  onAction: (id: string, verb: string) => void;
  onShowRuns: (id: string) => void;
}) {
  return (
    <>
      <tr>
        <td>{deployment.name}</td>
        <td className="mono">{deployment.cron}</td>
        <td>
          {deployment.paused
            ? <span className="badge idle">paused</span>
            : <span className="badge running">active</span>}
        </td>
        <td style={{ textAlign: "right" }}>
          <span className="row" style={{ justifyContent: "flex-end" }}>
            <button className="ghost" onClick={() => onAction(deployment.id, "run")}>Run now</button>
            <button className="ghost" onClick={() => onAction(deployment.id, deployment.paused ? "unpause" : "pause")}>
              {deployment.paused ? "Unpause" : "Pause"}
            </button>
            <button className="ghost" onClick={() => onShowRuns(deployment.id)}>Runs</button>
            <button className="danger" onClick={() => onAction(deployment.id, "archive")}>Archive</button>
          </span>
        </td>
      </tr>
      {runs && (
        <tr>
          <td colSpan={4}>
            {runs.length === 0 && <span className="muted">no runs yet</span>}
            {runs.map((run) => (
              <div key={run.id} className="row muted" style={{ gap: 16 }}>
                <span>{new Date(run.fired_at).toLocaleString()}</span>
                <span className={`badge ${run.status === "failed" ? "terminated" : "running"}`}>
                  {run.status}{run.error_type ? `: ${run.error_type}` : ""}
                </span>
                {run.session_id && <span className="mono">{run.session_id}</span>}
              </div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
