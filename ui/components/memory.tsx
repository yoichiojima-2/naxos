"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Memory, MemoryStore } from "@/lib/api";

export default function MemoryStores() {
  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [memories, setMemories] = useState<Record<string, Memory[]>>({});
  const [storeName, setStoreName] = useState("");
  const [editing, setEditing] = useState<{ storeId: string; path: string; content: string } | null>(null);

  const refresh = useCallback(async () => {
    const result = await api<{ data: MemoryStore[] }>("/v1/memory_stores");
    setStores(result.data);
    for (const store of result.data) {
      const items = await api<{ data: Memory[] }>(`/v1/memory_stores/${store.id}/memories`);
      setMemories((prev) => ({ ...prev, [store.id]: items.data }));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function createStore() {
    await api("/v1/memory_stores", { json: { name: storeName } });
    setStoreName("");
    refresh();
  }

  async function openMemory(storeId: string, memory: Memory) {
    const full = await api<Memory>(`/v1/memory_stores/${storeId}/memories/${memory.id}`);
    setEditing({ storeId, path: full.path, content: full.content ?? "" });
  }

  async function save() {
    if (!editing) return;
    await api(`/v1/memory_stores/${editing.storeId}/memories`, {
      json: { path: editing.path, content: editing.content },
    });
    setEditing(null);
    refresh();
  }

  if (editing) {
    return (
      <div className="panel">
        <div className="row between" style={{ marginBottom: 12 }}>
          <div className="row">
            <button className="ghost" onClick={() => setEditing(null)}>&larr;</button>
            <span className="mono">{editing.path}</span>
          </div>
          <button className="primary" onClick={save}>Save</button>
        </div>
        <textarea
          style={{ minHeight: 360 }}
          value={editing.content}
          onChange={(e) => setEditing({ ...editing, content: e.target.value })}
        />
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="row between" style={{ marginBottom: 12 }}>
        <strong>Memory stores</strong>
        <div className="row">
          <input
            placeholder="new store name"
            value={storeName}
            onChange={(e) => setStoreName(e.target.value)}
            style={{ width: 220 }}
          />
          <button className="primary" onClick={createStore} disabled={!storeName}>Create</button>
        </div>
      </div>
      {stores.map((store) => (
        <div className="panel" key={store.id}>
          <div className="row between">
            <strong>{store.name}</strong>
            <button
              className="ghost"
              onClick={() =>
                setEditing({ storeId: store.id, path: "notes.md", content: "" })
              }
            >
              New file
            </button>
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {(memories[store.id] ?? []).map((memory) => (
                  <tr key={memory.id} className="click" onClick={() => openMemory(store.id, memory)}>
                    <td className="mono">{memory.path}</td>
                    <td className="muted" style={{ textAlign: "right" }}>{memory.size} B</td>
                    <td style={{ textAlign: "right", width: 1 }}>
                      <button
                        className="danger"
                        onClick={async (e) => {
                          e.stopPropagation();
                          if (!window.confirm(`Delete "${memory.path}"?`)) return;
                          await api(`/v1/memory_stores/${store.id}/memories/${memory.id}`, { method: "DELETE" });
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
    </div>
  );
}
