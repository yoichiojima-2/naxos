"use client";

import { useCallback, useEffect, useState } from "react";
import { agentName, api, apiConfirm, Agent, Deployment, DeploymentRun } from "@/lib/api";
import CountHeader from "@/components/list-header";
import FilterInput from "@/components/filter-input";
import { formatDuration, fullTime } from "@/lib/format";
import TableStates from "@/components/table-states";
import DeploymentRuns from "@/components/deployment-runs";
import { RUN_STATUS } from "@/lib/runs";

export default function Deployments({ agents, view }: { agents: Agent[]; view?: string }) {
  const [tab, focusedId] = (view ?? "").split("/");
  const onRuns = tab === "runs";
  return (
    <>
      <div className="tabs">
        <a className={onRuns ? "" : "on"} href="#deployments">Schedules</a>
        <a className={onRuns ? "on" : ""} href="#deployments/runs">Runs</a>
      </div>
      {onRuns ? (
        <DeploymentRuns agents={agents} deploymentId={focusedId || undefined} />
      ) : (
        <Schedules agents={agents} />
      )}
    </>
  );
}

function Schedules({ agents }: { agents: Agent[] }) {
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [runs, setRuns] = useState<Record<string, DeploymentRun[]>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [query, setQuery] = useState("");
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

  const q = query.trim().toLowerCase();
  const filtered = (deployments ?? []).filter(
    (d) => !q || `${d.name} ${d.cron} ${agentName(agents, d.agent_id)}`.toLowerCase().includes(q),
  );

  return (
    <>
      <CountHeader
        count={deployments === null ? null : filtered.length}
        of={deployments?.length}
        noun="deployment"
      >
        {!!deployments?.length && (
          <FilterInput placeholder="Filter deployments…" value={query} onChange={setQuery} />
        )}
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
            <TableStates
              items={deployments}
              filtered={filtered}
              colSpan={4}
              empty="no deployments yet — schedule an agent to run unattended."
              noMatch="no deployments match the current filter."
            />
            {filtered.map((d) => (
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
            <a className="button-link" href={`#deployments/runs/${deployment.id}`}>Runs</a>
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
              <div><label>created</label>{fullTime(deployment.created_at)} <span className="muted">by {deployment.created_by}</span></div>
            </div>
            <label>prompt for each run</label>
            <pre className="prewrap" style={{ margin: "0 0 10px" }}>{prompt || <span className="muted">(none)</span>}</pre>
            <label>recent runs</label>
            {!runs && <span className="muted">loading…</span>}
            {runs?.length === 0 && <span className="muted">no runs yet</span>}
            {runs?.slice(0, 5).map((run) => (
              <div key={run.id} className="row muted" style={{ gap: 16 }}>
                <span>{fullTime(run.fired_at)}</span>
                <span className={`badge ${RUN_STATUS[run.status].badge}`}>
                  {run.status}{run.error_type ? `: ${run.error_type}` : ""}
                </span>
                <span className="mono">{formatDuration(run.duration_seconds)}</span>
                {run.session_id && (
                  <a className="mono" href={`#sessions/${run.session_id}`}>{run.session_id}</a>
                )}
              </div>
            ))}
            {!!runs?.length && (
              <div className="mt8">
                <a className="mono" href={`#deployments/runs/${deployment.id}`}>
                  all runs for this deployment →
                </a>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
