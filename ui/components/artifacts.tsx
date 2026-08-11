"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiConfirm, Agent, agentName, Artifact } from "@/lib/api";
import CountHeader from "@/components/list-header";
import FilterInput from "@/components/filter-input";

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

export default function Artifacts({ agents }: { agents: Agent[] }) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [copied, setCopied] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const refresh = useCallback(async () => {
    const result = await api<{ data: Artifact[] }>("/v1/artifacts");
    setArtifacts(result.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function share(artifact: Artifact) {
    await api(`/v1/artifacts/${artifact.id}/share`, { json: {} });
    refresh();
  }

  async function unshare(artifact: Artifact) {
    await api(`/v1/artifacts/${artifact.id}/share`, { method: "DELETE" });
    refresh();
  }

  async function remove(artifact: Artifact) {
    if (
      await apiConfirm(
        `Delete "${artifact.name}"? Its content and share link are removed.`,
        `/v1/artifacts/${artifact.id}`,
        { method: "DELETE" },
      )
    ) {
      refresh();
    }
  }

  async function copyLink(artifact: Artifact) {
    const url = `${window.location.origin}/#artifacts/shared/${artifact.share_token}`;
    await navigator.clipboard.writeText(url);
    setCopied(artifact.id);
    setTimeout(() => setCopied(null), 1500);
  }

  const q = query.trim().toLowerCase();
  const filtered = artifacts.filter(
    (a) =>
      !q ||
      `${a.name} ${a.description ?? ""} ${a.session_id} ${agentName(agents, a.agent_id)}`
        .toLowerCase()
        .includes(q),
  );

  return (
    <>
      <CountHeader count={filtered.length} of={artifacts.length} noun="artifact">
        {artifacts.length > 0 && (
          <FilterInput placeholder="Filter artifacts…" value={query} onChange={setQuery} />
        )}
      </CountHeader>
      <div className="panel">
        {artifacts.length === 0 && (
          <span className="muted">
            no artifacts yet — agents publish them with the artifact_create tool.
          </span>
        )}
        {artifacts.length > 0 && filtered.length === 0 && (
          <span className="muted">no artifacts match the current filter.</span>
        )}
        {filtered.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Agent</th>
                  <th>Size</th>
                  <th>Version</th>
                  <th>Shared</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((artifact) => (
                  <tr
                    key={artifact.id}
                    className="click"
                    onClick={() => { window.location.hash = `#artifacts/${artifact.id}`; }}
                  >
                    <td>
                      <a
                        className="mono"
                        href={`#artifacts/${artifact.id}`}
                        title={artifact.description ?? undefined}
                      >
                        {artifact.name}
                      </a>
                      {artifact.description && (
                        <div className="muted">{artifact.description}</div>
                      )}
                    </td>
                    <td>
                      {agentName(agents, artifact.agent_id)}
                      <div className="muted mono">{artifact.session_id}</div>
                    </td>
                    <td className="muted">{formatSize(artifact.size_bytes)}</td>
                    <td className="muted">v{artifact.version}</td>
                    <td>
                      {artifact.share_token ? (
                        <button
                          className="ghost"
                          onClick={(e) => { e.stopPropagation(); copyLink(artifact); }}
                        >
                          {copied === artifact.id ? "Copied!" : "Copy link"}
                        </button>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="ta-right" style={{ width: 1 }}>
                      <span className="row">
                        <a
                          className="mono"
                          href={`/v1/artifacts/${artifact.id}/content`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          Download
                        </a>
                        {artifact.share_token ? (
                          <button
                            className="ghost"
                            onClick={(e) => { e.stopPropagation(); unshare(artifact); }}
                          >
                            Unshare
                          </button>
                        ) : (
                          <button
                            className="ghost"
                            onClick={(e) => { e.stopPropagation(); share(artifact); }}
                          >
                            Share
                          </button>
                        )}
                        <button
                          className="danger"
                          onClick={(e) => { e.stopPropagation(); remove(artifact); }}
                        >
                          Delete
                        </button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
