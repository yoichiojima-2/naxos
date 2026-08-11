"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiConfirm, listFor, Memory, MemoryStore } from "@/lib/api";
import { fullTime, relativeTime } from "@/lib/format";
import CountHeader from "@/components/list-header";
import FileList from "@/components/file-list";
import FileEditor, { PATH_PATTERN } from "@/components/file-editor";
import LoadingPanel from "@/components/loading-panel";

type Editing = {
  storeId: string;
  id: string | null;
  originalPath: string | null;
  path: string;
  content: string;
};

const pathValid = (path: string) => {
  const segments = path.split("/");
  return (
    PATH_PATTERN.test(path) &&
    !segments.includes("..") &&
    !segments.includes("")
  );
};

export default function MemoryStores() {
  const [stores, setStores] = useState<MemoryStore[] | null>(null);
  const [memories, setMemories] = useState<Record<string, Memory[]>>({});
  const [storeName, setStoreName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [saving, setSaving] = useState(false);

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

  async function renameStore() {
    if (!renaming || !renaming.name.trim()) return;
    await api(`/v1/memory_stores/${renaming.id}`, {
      method: "PATCH",
      json: { name: renaming.name.trim() },
    });
    setRenaming(null);
    refresh();
  }

  async function deleteStore(store: MemoryStore) {
    const files = store.file_count ?? 0;
    if (
      await apiConfirm(
        `Delete store "${store.name}"${files ? ` and its ${files} file(s)` : ""}?`,
        `/v1/memory_stores/${store.id}`,
        { method: "DELETE" },
      )
    ) {
      refresh();
    }
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
    if (!editing || saving || !pathValid(editing.path)) return;
    setSaving(true);
    try {
      const fresh = await api<{ data: Memory[] }>(
        `/v1/memory_stores/${editing.storeId}/memories`,
      );
      const collision = fresh.data.find(
        (m) => m.path === editing.path && m.id !== editing.id,
      );
      if (collision && !window.confirm(`"${editing.path}" already exists — overwrite it?`)) {
        return;
      }
      await api(`/v1/memory_stores/${editing.storeId}/memories`, {
        json: { path: editing.path, content: editing.content },
      });
      if (editing.id && editing.originalPath && editing.originalPath !== editing.path) {
        try {
          await api(`/v1/memory_stores/${editing.storeId}/memories/${editing.id}`, {
            method: "DELETE",
          });
        } catch {
          // content is saved under the new path; refresh shows the leftover if any
        }
      }
      setEditing(null);
      refresh();
    } finally {
      setSaving(false);
    }
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
      <FileEditor
        path={editing.path}
        onPathChange={(path) => setEditing({ ...editing, path })}
        pathInputProps={{
          placeholder: "path, e.g. notes.md",
          style: { width: 280 },
          "aria-label": "file path",
        }}
        pathExtra={
          editing.originalPath && editing.originalPath !== editing.path && (
            <span className="muted">renaming {editing.originalPath}</span>
          )
        }
        content={editing.content}
        onContentChange={(content) => setEditing({ ...editing, content })}
        onBack={() => setEditing(null)}
        onSave={save}
        saveDisabled={invalid || saving}
        saveLabel={saving ? "Saving…" : "Save"}
      >
        {invalid && editing.path !== "" && (
          <p className="muted mb12">
            paths may only use letters, digits, and <span className="mono">. _ / -</span>
          </p>
        )}
      </FileEditor>
    );
  }

  return (
    <>
      <CountHeader count={stores === null ? null : stores.length} noun="store">
        <input
          placeholder="new store name"
          value={storeName}
          onChange={(e) => setStoreName(e.target.value)}
          style={{ width: 220 }}
        />
        <button className="primary" onClick={createStore} disabled={!storeName}>Create</button>
      </CountHeader>
      {stores === null && <LoadingPanel />}
      {stores?.length === 0 && (
        <div className="panel">
          <span className="muted">no memory stores yet — create one above to give agents durable memory.</span>
        </div>
      )}
      {(stores ?? []).map((store) => (
        <div className="panel" key={store.id}>
          <div className="row between">
            <div className="row">
              {renaming?.id === store.id ? (
                <>
                  <input
                    value={renaming.name}
                    onChange={(e) => setRenaming({ id: store.id, name: e.target.value })}
                    onKeyDown={(e) => e.key === "Enter" && renameStore()}
                    style={{ width: 220 }}
                    aria-label="store name"
                    autoFocus
                  />
                  <button className="primary" onClick={renameStore} disabled={!renaming.name.trim()}>
                    Save
                  </button>
                  <button className="ghost" onClick={() => setRenaming(null)}>Cancel</button>
                </>
              ) : (
                <>
                  <strong>{store.name}</strong>
                  {(store.used_by ?? []).length > 0 && (
                    <span className="muted">used by {store.used_by!.join(", ")}</span>
                  )}
                </>
              )}
            </div>
            <div className="row">
              <button
                className="ghost"
                onClick={() =>
                  setEditing({ storeId: store.id, id: null, originalPath: null, path: "", content: "" })
                }
              >
                New file
              </button>
              {renaming?.id !== store.id && (
                <button className="ghost" onClick={() => setRenaming({ id: store.id, name: store.name })}>
                  Rename
                </button>
              )}
              <button
                className="danger"
                onClick={() => deleteStore(store)}
                disabled={(store.used_by ?? []).length > 0}
                title={
                  (store.used_by ?? []).length > 0
                    ? "detach this store from its agents before deleting"
                    : undefined
                }
              >
                Delete
              </button>
            </div>
          </div>
          {(memories[store.id] ?? []).length === 0 ? (
            <p className="muted mt8">no files in this store yet.</p>
          ) : (
            <FileList count={(memories[store.id] ?? []).length}>
              <table>
                <tbody>
                  {(memories[store.id] ?? []).map((memory) => (
                    <tr key={memory.id} className="click" onClick={() => openMemory(store.id, memory)}>
                        <td className="mono">{memory.path}</td>
                        <td className="muted" title={memory.updated_at ? fullTime(memory.updated_at) : undefined}>
                          {memory.updated_at && relativeTime(memory.updated_at)}
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
            </FileList>
          )}
        </div>
      ))}
    </>
  );
}
