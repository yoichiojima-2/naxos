"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api, apiConfirm, AgentDetail as Detail, AgentIn, Environment } from "@/lib/api";
import AgentForm, { EFFORT_LEVELS, MODELS } from "@/components/agent-form";
import { BackIcon } from "@/components/icons";

export default function AgentDetail({
  agentId,
  environments,
  onChange,
}: {
  agentId: string;
  environments: Environment[];
  onChange: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [missing, setMissing] = useState(false);

  const load = useCallback(async (v: number | null) => {
    try {
      const query = v ? `?version=${v}` : "";
      setDetail(await api<Detail>(`/v1/agents/${agentId}${query}`));
      setMissing(false);
    } catch {
      setMissing(true);
    }
  }, [agentId]);

  useEffect(() => { load(version); }, [load, version]);

  if (missing) {
    return (
      <div className="panel">
        <a className="back" href="#agents"><BackIcon />Agents</a>
        <p className="muted mt12">Agent not found.</p>
      </div>
    );
  }

  if (!detail) {
    return <p className="muted">loading…</p>;
  }

  const latest = version === null || version === detail.latest_version;

  async function toggleKill() {
    if (!detail) return;
    await api(`/v1/agents/${agentId}`, { method: "PATCH", json: { disabled: !detail.disabled } });
    await load(version);
    onChange();
  }

  async function archive() {
    if (!detail) return;
    if (
      !(await apiConfirm(
        `Archive agent "${detail.name}"? It disappears from the console and can no longer start sessions.`,
        `/v1/agents/${agentId}/archive`,
      ))
    ) return;
    onChange();
    window.location.hash = "#agents";
  }

  async function saveVersion(body: AgentIn) {
    await api(`/v1/agents/${agentId}/versions`, { json: body });
    setEditing(false);
    setVersion(null);
    await load(null);
    onChange();
  }

  const modelLabel = MODELS.find((m) => m.id === detail.model)?.label ?? detail.model;
  const environment = environments.find((e) => e.id === detail.environment_id);
  const mcpNames = Object.keys(detail.mcp_servers ?? {});

  return (
    <>
      <div className="row between mb16">
        <div className="row">
          <a className="back" href="#agents"><BackIcon />Agents</a>
          <h2 style={{ fontSize: 20, fontWeight: 650 }}>{detail.name}</h2>
          {detail.disabled
            ? <span className="badge terminated">disabled</span>
            : <span className="badge running">active</span>}
        </div>
        {!editing && (
          <div className="row">
            <select
              value={version ?? detail.latest_version}
              style={{ width: "auto" }}
              onChange={(e) => {
                const v = Number(e.target.value);
                setVersion(v === detail.latest_version ? null : v);
              }}
            >
              {Array.from({ length: detail.latest_version }, (_, i) => detail.latest_version - i).map((v) => (
                <option key={v} value={v}>
                  v{v}{v === detail.latest_version ? " (latest)" : ""}
                </option>
              ))}
            </select>
            <button className="ghost" onClick={() => setEditing(true)} disabled={!latest}>
              Edit
            </button>
            <button className={detail.disabled ? "ghost" : "danger"} onClick={toggleKill}>
              {detail.disabled ? "Enable" : "Kill"}
            </button>
            <button className="danger" onClick={archive}>Archive</button>
          </div>
        )}
      </div>

      {editing ? (
        <AgentForm
          environments={environments}
          initial={detail}
          submitLabel={`Save as v${detail.latest_version + 1}`}
          onSubmit={saveVersion}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <>
          <div className="panel">
            <strong>Overview</strong>
            <dl className="kv mt12">
              <dt>ID</dt>
              <dd className="mono">{detail.id}</dd>
              <dt>Model</dt>
              <dd>{modelLabel}</dd>
              <dt>Effort</dt>
              <dd>
                {detail.effort
                  ? EFFORT_LEVELS.find((l) => l.id === detail.effort)?.label ?? detail.effort
                  : <span className="muted">model default</span>}
              </dd>
              <dt>Environment</dt>
              <dd>{environment?.name ?? detail.environment_id}</dd>
              <dt>Version</dt>
              <dd>v{detail.version} of {detail.latest_version}</dd>
              <dt>Budget per session</dt>
              <dd>
                {detail.default_budget_usd != null
                  ? `$${Number(detail.default_budget_usd).toFixed(2)}`
                  : <span className="muted">no limit</span>}
              </dd>
              <dt>Max turns</dt>
              <dd>{detail.max_turns ?? <span className="muted">unlimited</span>}</dd>
              {detail.created_at && (
                <>
                  <dt>Created</dt>
                  <dd>
                    {new Date(detail.created_at).toLocaleString()}
                    {detail.created_by && <span className="muted"> by {detail.created_by}</span>}
                  </dd>
                </>
              )}
            </dl>
          </div>

          <div className="panel">
            <strong>Instructions</strong>
            {detail.instructions ? (
              <pre className="mono prewrap mt12">
                {detail.instructions}
              </pre>
            ) : (
              <p className="muted mt8">none — the agent runs with the default system prompt.</p>
            )}
          </div>

          <div className="panel">
            <strong>Tools</strong>
            {detail.tools.length ? (
              <div className="chips mt12">
                {detail.tools.map((tool) => <span className="chip on" key={tool}>{tool}</span>)}
              </div>
            ) : (
              <p className="muted mt8">All tools allowed (unrestricted).</p>
            )}
          </div>

          <div className="panel">
            <strong>Permission policy</strong>
            <dl className="kv mt12">
              <dt>Default</dt>
              <dd>
                {detail.permission_policy.default === "always_ask"
                  ? "ask before every tool call"
                  : "allow tool calls without asking"}
              </dd>
              {detail.permission_policy.rules.map((rule, i) => (
                <Fragment key={i}>
                  <dt className="mono">{rule.tool}</dt>
                  <dd>{rule.mode}</dd>
                </Fragment>
              ))}
            </dl>
          </div>

          {(detail.vault_ids.length > 0 || detail.memory_store_ids.length > 0 ||
            detail.skill_ids.length > 0 || mcpNames.length > 0) && (
            <div className="panel">
              <strong>Attachments</strong>
              <dl className="kv mt12">
                {detail.vault_ids.length > 0 && (
                  <>
                    <dt>Vaults</dt>
                    <dd className="mono">{detail.vault_ids.join(", ")}</dd>
                  </>
                )}
                {detail.memory_store_ids.length > 0 && (
                  <>
                    <dt>Memory stores</dt>
                    <dd className="mono">{detail.memory_store_ids.join(", ")}</dd>
                  </>
                )}
                {detail.skill_ids.length > 0 && (
                  <>
                    <dt>Skills</dt>
                    <dd className="mono">{detail.skill_ids.join(", ")}</dd>
                  </>
                )}
                {mcpNames.length > 0 && (
                  <>
                    <dt>MCP servers</dt>
                    <dd className="mono">{mcpNames.join(", ")}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
        </>
      )}
    </>
  );
}
