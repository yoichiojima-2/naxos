"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiConfirm, listFor, Memory, MemoryStore } from "@/lib/api";
import { BackIcon } from "@/components/icons";
import CountHeader from "@/components/list-header";

type Editing = {
  storeId: string;
  id: string | null;
  originalPath: string | null;
  path: string;
  content: string;
};

const pathValid = (path: string) =>
  /^[a-zA-Z0-9._/-]{1,200}$/.test(path) && !path.split("/").includes("..");

export default function MemoryStores() {
  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [memories, setMemories] = useState<Record<string, Memory[]>>({});
  const [storeName, setStoreName] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);

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
    setEditing({
      storeId,
      id: memory.id,
      originalPath: full.path,
      path: full.path,
      content: full.content ?? "",
    });
  }

  async function save() {
    if (!editing || !pathValid(editing.path)) return;
    const collision = (memories[editing.storeId] ?? []).find(
      (m) => m.path === editing.path && m.id !== editing.id,
    );
    if (collision && !window.confirm(`"${editing.path}" already exists — overwrite it?`)) {
      return;
    }
    await api(`/v1/memory_stores/${editing.storeId}/memories`, {
      json: { path: editing.path, content: editing.content },
    });
    if (editing.id && editing.originalPath && editing.originalPath !== editing.path) {
      await api(`/v1/memory_stores/${editing.storeId}/memories/${editing.id}`, {
        method: "DELETE",
      });
    }
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
    const invalid = !pathValid(editing.path);
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
            <input
              className="mono"
              placeholder="path, e.g. notes.md"
              value={editing.path}
              onChange={(e) => setEditing({ ...editing, path: e.target.value })}
              style={{ width: 280 }}
              aria-label="file path"
            />
            {editing.originalPath && editing.originalPath !== editing.path && (
              <span className="muted">renaming {editing.originalPath}</span>
            )}
          </div>
          <button className="primary" onClick={save} disabled={invalid}>
            Save
          </button>
        </div>
        {invalid && editing.path !== "" && (
          <p className="muted mb12">
            paths may only use letters, digits, and <span className="mono">. _ / -</span>
          </p>
        )}
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
                setEditing({ storeId: store.id, id: null, originalPath: null, path: "", content: "" })
              }
            >
              New file
            </button>
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {(memories[store.id] ?? []).length === 0 && (
                  <tr><td className="empty" colSpan={4}>no files in this store yet.</td></tr>
                )}
                {(memories[store.id] ?? []).map((memory) => (
                  <tr key={memory.id} className="click" onClick={() => openMemory(store.id, memory)}>
                    <td className="mono">{memory.path}</td>
                    <td className="muted">
                      {memory.updated_at && new Date(memory.updated_at).toLocaleString()}
                      {memory.updated_by && ` · ${memory.updated_by}`}
                    </td>
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
