"use client";

import { useEffect, useState } from "react";
import {
  api,
  AgentDetail,
  AgentIn,
  Environment,
  MemoryStore,
  PermissionMode,
  PermissionRule,
  Vault,
} from "@/lib/api";

export const MODELS = [
  { id: "claude-opus-5", label: "Claude Opus 5" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
  { id: "claude-fable-5", label: "Claude Fable 5" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

const BUILTIN_TOOLS = [
  "Bash", "Read", "Write", "Edit", "Glob", "Grep",
  "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit",
];

export default function AgentForm({
  environments,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  environments: Environment[];
  initial?: AgentDetail;
  submitLabel: string;
  onSubmit: (body: AgentIn) => Promise<void>;
  onCancel: () => void;
}) {
  const editing = initial !== undefined;
  const [name, setName] = useState(initial?.name ?? "");
  const [model, setModel] = useState(initial?.model ?? MODELS[1].id);
  const [environmentId, setEnvironmentId] = useState(
    initial?.environment_id ?? environments[0]?.id ?? "",
  );
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [tools, setTools] = useState<string[]>(initial?.tools ?? []);
  const [customTool, setCustomTool] = useState("");
  const [defaultMode, setDefaultMode] = useState<PermissionMode>(
    initial?.permission_policy?.default ?? "always_ask",
  );
  const [rules, setRules] = useState<PermissionRule[]>(
    initial?.permission_policy?.rules ?? [],
  );
  const [budget, setBudget] = useState(
    initial?.default_budget_usd != null ? String(Number(initial.default_budget_usd)) : "",
  );
  const [maxTurns, setMaxTurns] = useState(
    initial?.max_turns != null ? String(initial.max_turns) : "",
  );
  const [mcpJson, setMcpJson] = useState(
    JSON.stringify(initial?.mcp_servers ?? {}, null, 2),
  );
  const [vaultIds, setVaultIds] = useState<string[]>(initial?.vault_ids ?? []);
  const [memoryStoreIds, setMemoryStoreIds] = useState<string[]>(
    initial?.memory_store_ids ?? [],
  );
  const [vaults, setVaults] = useState<Vault[]>([]);
  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ data: Vault[] }>("/v1/vaults").then((r) => setVaults(r.data));
    api<{ data: MemoryStore[] }>("/v1/memory_stores").then((r) => setStores(r.data));
  }, []);

  let mcpServers: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(mcpJson);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) mcpServers = parsed;
  } catch {
    /* invalid JSON disables submit */
  }

  const customTools = tools.filter((t) => !BUILTIN_TOOLS.includes(t));

  function toggleTool(tool: string) {
    setTools((prev) =>
      prev.includes(tool) ? prev.filter((t) => t !== tool) : [...prev, tool],
    );
  }

  function addCustomTool() {
    const tool = customTool.trim();
    if (tool && !tools.includes(tool)) setTools((prev) => [...prev, tool]);
    setCustomTool("");
  }

  function toggleId(list: string[], set: (v: string[]) => void, id: string) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function submit() {
    setBusy(true);
    try {
      await onSubmit({
        name,
        environment_id: environmentId,
        model,
        instructions: instructions || null,
        tools,
        permission_policy: { default: defaultMode, rules: rules.filter((r) => r.tool.trim()) },
        mcp_servers: mcpServers ?? {},
        vault_ids: vaultIds,
        memory_store_ids: memoryStoreIds,
        default_budget_usd: budget === "" ? null : Number(budget),
        max_turns: maxTurns === "" ? null : Number(maxTurns),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="grid2">
        <div>
          <label>Name</label>
          {editing ? (
            <input value={name} disabled title="Name is fixed after creation" />
          ) : (
            <input value={name} onChange={(e) => setName(e.target.value)} />
          )}
        </div>
        <div>
          <label>Model</label>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </div>
      </div>

      {(editing || environments.length > 1) && (
        <>
          <label>Environment</label>
          <select
            value={environmentId}
            disabled={editing}
            onChange={(e) => setEnvironmentId(e.target.value)}
          >
            {environments.map((env) => (
              <option key={env.id} value={env.id}>{env.name}</option>
            ))}
          </select>
        </>
      )}

      <label>Instructions</label>
      <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} />

      <label>Tools</label>
      <div className="chips">
        {BUILTIN_TOOLS.map((tool) => (
          <button
            key={tool}
            type="button"
            className={`chip ${tools.includes(tool) ? "on" : ""}`}
            onClick={() => toggleTool(tool)}
          >
            {tool}
          </button>
        ))}
        {customTools.map((tool) => (
          <button
            key={tool}
            type="button"
            className="chip on"
            title="Remove"
            onClick={() => toggleTool(tool)}
          >
            {tool} ×
          </button>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input
          value={customTool}
          placeholder="Custom tool name, e.g. mcp__github__get_me"
          style={{ maxWidth: 340 }}
          onChange={(e) => setCustomTool(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); addCustomTool(); }
          }}
        />
        <button type="button" className="ghost" onClick={addCustomTool} disabled={!customTool.trim()}>
          Add
        </button>
      </div>
      <p className="hint">
        No tools selected = the agent may use all tools. Selecting any tool restricts the
        agent to that list.
      </p>

      <label>Permission policy</label>
      <div className="row" style={{ gap: 18 }}>
        {(["always_ask", "always_allow"] as const).map((mode) => (
          <label key={mode} className="row" style={{ margin: 0, gap: 6, fontWeight: 500, color: "var(--text)" }}>
            <input
              type="radio"
              name="default-mode"
              checked={defaultMode === mode}
              onChange={() => setDefaultMode(mode)}
            />
            {mode === "always_ask" ? "Ask before every tool call" : "Allow tool calls without asking"}
          </label>
        ))}
      </div>
      {rules.map((rule, i) => (
        <div className="row" key={i} style={{ marginTop: 8 }}>
          <input
            value={rule.tool}
            placeholder="Tool name or *"
            list="rule-tools"
            style={{ maxWidth: 260 }}
            onChange={(e) =>
              setRules((prev) => prev.map((r, j) => (j === i ? { ...r, tool: e.target.value } : r)))
            }
          />
          <select
            value={rule.mode}
            style={{ maxWidth: 160 }}
            onChange={(e) =>
              setRules((prev) =>
                prev.map((r, j) => (j === i ? { ...r, mode: e.target.value as PermissionMode } : r)),
              )
            }
          >
            <option value="always_ask">always_ask</option>
            <option value="always_allow">always_allow</option>
          </select>
          <button
            type="button"
            className="ghost"
            onClick={() => setRules((prev) => prev.filter((_, j) => j !== i))}
          >
            Remove
          </button>
        </div>
      ))}
      <datalist id="rule-tools">
        <option value="*" />
        {tools.map((tool) => <option key={tool} value={tool} />)}
      </datalist>
      <div style={{ marginTop: 8 }}>
        <button
          type="button"
          className="ghost"
          onClick={() => setRules((prev) => [...prev, { tool: "", mode: "always_allow" }])}
        >
          Add per-tool rule
        </button>
      </div>

      <div className="grid2">
        <div>
          <label>Default budget per session (USD)</label>
          <input
            type="number"
            min="0"
            step="0.01"
            placeholder="no limit"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </div>
        <div>
          <label>Max turns per run</label>
          <input
            type="number"
            min="1"
            step="1"
            placeholder="unlimited"
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
          />
        </div>
      </div>

      {vaults.length > 0 && (
        <>
          <label>Vaults</label>
          <div className="chips">
            {vaults.map((v) => (
              <button
                key={v.id}
                type="button"
                className={`chip ${vaultIds.includes(v.id) ? "on" : ""}`}
                onClick={() => toggleId(vaultIds, setVaultIds, v.id)}
              >
                {v.name}
              </button>
            ))}
          </div>
        </>
      )}

      {stores.length > 0 && (
        <>
          <label>Memory stores</label>
          <div className="chips">
            {stores.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`chip ${memoryStoreIds.includes(s.id) ? "on" : ""}`}
                onClick={() => toggleId(memoryStoreIds, setMemoryStoreIds, s.id)}
              >
                {s.name}
              </button>
            ))}
          </div>
        </>
      )}

      <label>MCP servers (JSON)</label>
      <textarea
        value={mcpJson}
        style={{ minHeight: 80 }}
        onChange={(e) => setMcpJson(e.target.value)}
      />
      {mcpServers === null && <p className="hint" style={{ color: "var(--danger)" }}>Invalid JSON object.</p>}

      <div className="row" style={{ marginTop: 16 }}>
        <button className="primary" onClick={submit} disabled={!name || mcpServers === null || busy}>
          {submitLabel}
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
