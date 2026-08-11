"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Credential, Vault } from "@/lib/api";

function targetLabel(cred: Credential) {
  if (cred.type === "header" && cred.target.mcp_server) {
    return `mcp:${cred.target.mcp_server} · ${cred.target.header ?? "authorization"}`;
  }
  if (cred.type === "env" && cred.target.env_var) {
    return `env:${cred.target.env_var}`;
  }
  return JSON.stringify(cred.target);
}

export default function Vaults() {
  const [vaults, setVaults] = useState<Vault[]>([]);
  const [credentials, setCredentials] = useState<Record<string, Credential[]>>({});
  const [vaultName, setVaultName] = useState("");
  const [form, setForm] = useState({ vaultId: "", name: "", value: "", mcpServer: "" });

  const refresh = useCallback(async () => {
    const result = await api<{ data: Vault[] }>("/v1/vaults");
    setVaults(result.data);
    for (const vault of result.data) {
      const creds = await api<{ data: Credential[] }>(`/v1/vaults/${vault.id}/credentials`);
      setCredentials((prev) => ({ ...prev, [vault.id]: creds.data }));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function createVault() {
    await api("/v1/vaults", { json: { name: vaultName } });
    setVaultName("");
    refresh();
  }

  async function addCredential() {
    await api(`/v1/vaults/${form.vaultId || vaults[0]?.id}/credentials`, {
      json: {
        name: form.name,
        type: "header",
        value: form.value,
        target: { mcp_server: form.mcpServer, header: "authorization", prefix: "Bearer " },
      },
    });
    setForm({ vaultId: "", name: "", value: "", mcpServer: "" });
    refresh();
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <input
          placeholder="new vault name"
          value={vaultName}
          onChange={(e) => setVaultName(e.target.value)}
          style={{ width: 240 }}
        />
        <button className="primary" onClick={createVault} disabled={!vaultName}>Create vault</button>
      </div>

      {vaults.map((vault) => (
        <div className="panel" key={vault.id}>
          <div className="row between">
            <strong>{vault.name}</strong>
            <span className="row">
              <span className="muted mono">{vault.id}</span>
              <button
                className="danger"
                onClick={async () => {
                  if (!window.confirm(`Delete vault "${vault.name}"? Agents using it lose its credentials.`)) return;
                  await api(`/v1/vaults/${vault.id}/archive`, { json: {} });
                  setVaults((prev) => prev.filter((v) => v.id !== vault.id));
                }}
              >
                Delete
              </button>
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>name</th><th>type</th><th>target</th><th>value</th><th>created</th><th /></tr>
              </thead>
              <tbody>
                {(credentials[vault.id] ?? []).length === 0 && (
                  <tr><td className="empty" colSpan={6}>no credentials registered in this vault.</td></tr>
                )}
                {(credentials[vault.id] ?? []).map((cred) => (
                  <tr key={cred.id}>
                    <td>{cred.name}</td>
                    <td className="muted">{cred.type}</td>
                    <td className="muted mono">{targetLabel(cred)}</td>
                    <td className="muted" title="Secret values are write-only: stored in Secret Manager, injected by the egress proxy, never returned by the API.">
                      •••• <span className="muted">write-only</span>
                    </td>
                    <td className="muted">{new Date(cred.created_at).toLocaleDateString()}</td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="danger"
                        onClick={async () => {
                          if (!window.confirm(`Delete credential "${cred.name}"?`)) return;
                          await api(`/v1/vaults/${vault.id}/credentials/${cred.id}`, { method: "DELETE" });
                          refresh();
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {vaults.length > 0 && (
        <div className="panel">
          <strong>Add credential</strong>
          <div className="grid2">
            <div>
              <label>vault</label>
              <select value={form.vaultId} onChange={(e) => setForm({ ...form, vaultId: e.target.value })}>
                {vaults.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
            </div>
            <div>
              <label>name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label>MCP server name (as in agent config)</label>
              <input value={form.mcpServer} onChange={(e) => setForm({ ...form, mcpServer: e.target.value })} />
            </div>
            <div>
              <label>secret value</label>
              <input type="password" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            <button className="primary" onClick={addCredential} disabled={!form.name || !form.value}>
              Store credential
            </button>
          </div>
        </div>
      )}
    </>
  );
}
