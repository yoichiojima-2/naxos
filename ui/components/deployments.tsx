"use client";

import { useCallback, useEffect, useState } from "react";
import { agentName, api, apiConfirm, Agent, Deployment, DeploymentRun } from "@/lib/api";
import CountHeader from "@/components/list-header";

export default function Deployments({ agents }: { agents: Agent[] }) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [runs, setRuns] = useState<Record<string, DeploymentRun[]>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [prompt, setPrompt] = useState("");

  const refresh = useCallback(async () => {
    const result = await api<{ data: Deployment[] }>("/v1/deployments");
    setDeployments(result.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function create() {
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
  }

  async function action(id: string, verb: string) {
    if (verb === "archive") {
      if (
        !(await apiConfirm(
          "Archive this deployment? Its schedule stops firing.",
          `/v1/deployments/${id}/archive`,
        ))
      ) return;
    } else {
      await api(`/v1/deployments/${id}/${verb}`, { json: {} });
    }
    refresh();
    if (verb === "run") loadRuns(id);
  }

  async function loadRuns(id: string) {
    const result = await api<{ data: DeploymentRun[] }>(`/v1/deployments/${id}/runs`);
    setRuns((prev) => ({ ...prev, [id]: result.data }));
  }

  function toggleOpen(id: string) {
    setOpenId((prev) => (prev === id ? null : id));
    loadRuns(id);
  }

  return (
    <>
      <CountHeader count={deployments.length} noun="deployment">
        <button className={showForm ? "ghost" : "primary"} onClick={() => setShowForm(!showForm)}>
          {showForm ? "Cancel" : "New deployment"}
        </button>
      </CountHeader>

      {showForm && (
        <div className="panel">
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
          <div className="mt12">
            <button className="primary" onClick={create} disabled={!name || !prompt}>Create</button>
          </div>
        </div>
      )}

      <div className="panel flush">
        <div className="table-wrap">
          <table>
          <thead>
            <tr><th>name</th><th>cron</th><th>state</th><th /></tr>
          </thead>
          <tbody>
            {deployments.length === 0 && (
              <tr><td className="empty" colSpan={4}>no deployments yet — schedule an agent to run unattended.</td></tr>
            )}
            {deployments.map((d) => (
              <DeploymentRow
                key={d.id}
                deployment={d}
                agents={agents}
                open={openId === d.id}
                runs={runs[d.id]}
                onAction={action}
                onToggle={toggleOpen}
              />
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </>
  );
}

function DeploymentRow({
  deployment,
  agents,
  open,
  runs,
  onAction,
  onToggle,
}: {
  deployment: Deployment;
  agents: Agent[];
  open: boolean;
  runs?: DeploymentRun[];
  onAction: (id: string, verb: string) => void;
  onToggle: (id: string) => void;
}) {
  const prompt = deployment.initial_events
    ?.flatMap((e) => e.content ?? [])
    .map((b) => b.text)
    .filter(Boolean)
    .join("\n");
  return (
    <>
      <tr className="click" onClick={() => onToggle(deployment.id)}>
        <td>{deployment.name}</td>
        <td className="mono">{deployment.cron}</td>
        <td>
          {deployment.paused
            ? <span className="badge idle">paused</span>
            : <span className="badge running">active</span>}
        </td>
        <td className="ta-right" onClick={(e) => e.stopPropagation()}>
          <span className="row end">
            <button className="ghost" onClick={() => onAction(deployment.id, "run")}>Run now</button>
            <button className="ghost" onClick={() => onAction(deployment.id, deployment.paused ? "unpause" : "pause")}>
              {deployment.paused ? "Unpause" : "Pause"}
            </button>
            <button className="danger" onClick={() => onAction(deployment.id, "archive")}>Archive</button>
          </span>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4}>
            <div className="grid2" style={{ marginBottom: 10 }}>
              <div><label>agent</label>{agentName(agents, deployment.agent_id)}{" "}
                <span className="muted">{deployment.agent_version ? `pinned to v${deployment.agent_version}` : "latest version"}</span>
              </div>
              <div><label>schedule</label><span className="mono">{deployment.cron}</span> <span className="muted">{deployment.timezone}</span></div>
              <div><label>budget per run</label>{deployment.budget_usd ? `$${deployment.budget_usd}` : <span className="muted">none</span>}</div>
              <div><label>created</label>{new Date(deployment.created_at).toLocaleString()} <span className="muted">by {deployment.created_by}</span></div>
            </div>
            <label>prompt for each run</label>
            <pre className="prewrap" style={{ margin: "0 0 10px" }}>{prompt || <span className="muted">(none)</span>}</pre>
            <label>recent runs</label>
            {!runs && <span className="muted">loading…</span>}
            {runs?.length === 0 && <span className="muted">no runs yet</span>}
            {runs?.map((run) => (
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
