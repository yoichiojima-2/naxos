"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiConfirm, listFor, Memory, MemoryStore } from "@/lib/api";
import { BackIcon } from "@/components/icons";
import CountHeader from "@/components/list-header";

export default function MemoryStores() {
  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [memories, setMemories] = useState<Record<string, Memory[]>>({});
  const [storeName, setStoreName] = useState("");
  const [editing, setEditing] = useState<{ storeId: string; path: string; content: string } | null>(null);

  const refresh = useCallback(async () => {
    const result = await api<{ data: MemoryStore[] }>("/v1/memory_stores");
    setStores(result.data);
    setMemories(
      await listFor<Memory>(
        result.data.map((s) => s.id),
        (id) => `/v1/memory_stores/${id}/memories`,
      ),
    );
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

  async function deleteMemory(storeId: string, memory: Memory) {
    if (
      await apiConfirm(
        `Delete "${memory.path}"?`,
        `/v1/memory_stores/${storeId}/memories/${memory.id}`,
        { method: "DELETE" },
      )
    ) {
      refresh();
    }
  }

  if (editing) {
    return (
      <div className="panel">
        <div className="row between mb12">
          <div className="row">
            <button
              className="ghost flex-inline"
              onClick={() => setEditing(null)}
              aria-label="back"
            >
              <BackIcon />
            </button>
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
    <>
      <CountHeader count={stores.length} noun="store">
        <input
          placeholder="new store name"
          value={storeName}
          onChange={(e) => setStoreName(e.target.value)}
          style={{ width: 220 }}
        />
        <button className="primary" onClick={createStore} disabled={!storeName}>Create</button>
      </CountHeader>
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
                    <td className="muted ta-right">{memory.size} B</td>
                    <td className="ta-right" style={{ width: 1 }}>
                      <button
                        className="danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteMemory(store.id, memory);
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
    </>
  );
}
