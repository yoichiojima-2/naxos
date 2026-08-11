"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, apiBlob, apiConfirm, Agent, agentName, Artifact } from "@/lib/api";
import Markdown from "@/components/markdown";

const MAX_RENDERED_CHARS = 1_000_000;
const MAX_PRETTY_JSON_BYTES = 2 * 1024 * 1024;
const MAX_CSV_ROWS = 500;

type Kind =
  | "markdown" | "html" | "svg" | "image" | "json" | "csv" | "pdf" | "text" | "binary";

const TEXT_EXT = new Set([
  "txt", "log", "py", "js", "ts", "tsx", "jsx", "css", "sh", "yaml", "yml", "toml",
  "xml", "sql", "rs", "go", "java", "rb", "c", "h", "cpp",
]);

function kindOf(contentType: string, name: string): Kind {
  const mime = contentType.split(";")[0].trim().toLowerCase();
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (mime === "text/markdown" || ext === "md" || ext === "markdown") return "markdown";
  if (mime === "text/html" || ext === "html" || ext === "htm") return "html";
  if (mime === "image/svg+xml" || ext === "svg") return "svg";
  if (mime.startsWith("image/")) return "image";
  if (mime === "application/json" || mime.endsWith("+json") || ext === "json") return "json";
  if (mime === "text/csv" || mime === "text/tab-separated-values" || ext === "csv" || ext === "tsv")
    return "csv";
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (mime.startsWith("text/") || TEXT_EXT.has(ext)) return "text";
  return "binary";
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"' && cell === "") {
      quoted = true;
    } else if (c === delimiter) {
      row.push(cell); cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      rows.push(row); row = [];
      if (rows.length > MAX_CSV_ROWS) return rows;
    } else {
      cell += c;
    }
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

export default function ArtifactViewer({
  artifactId,
  token,
  agents,
}: {
  artifactId?: string;
  token?: string;
  agents: Agent[];
}) {
  const metaPath = token ? `/v1/artifacts/shared/${token}` : `/v1/artifacts/${artifactId}`;
  const contentPath = `${metaPath}/content`;

  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const fullscreenTrigger = useRef<HTMLButtonElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const wasFullscreen = useRef(false);

  const kind = artifact ? kindOf(artifact.content_type, artifact.name) : null;

  useEffect(() => {
    let cancelled = false;
    setArtifact(null); setBlob(null); setText(null); setMissing(false); setShowSource(false);
    setFullscreen(false);
    (async () => {
      try {
        const meta = await api<Artifact>(metaPath);
        if (cancelled) return;
        setArtifact(meta);
        const content = await apiBlob(`${metaPath}/content`);
        if (cancelled) return;
        setBlob(content);
        const k = kindOf(meta.content_type, meta.name);
        if (["markdown", "html", "svg", "json", "csv", "text"].includes(k)) {
          const t = await content.text();
          if (!cancelled) setText(t);
        }
      } catch {
        if (!cancelled) setMissing(true);
      }
    })();
    return () => { cancelled = true; };
  }, [metaPath]);

  useEffect(() => {
    if (!blob || !kind || !["image", "svg", "pdf"].includes(kind)) return;
    // Force the type so the browser cannot sniff a blob into an HTML document.
    const typed = kind === "pdf" ? new Blob([blob], { type: "application/pdf" }) : blob;
    const url = URL.createObjectURL(typed);
    setBlobUrl(url);
    return () => { setBlobUrl(null); URL.revokeObjectURL(url); };
  }, [blob, kind]);

  useEffect(() => {
    if (!fullscreen) return;
    // The covered page stays in the tab order and the accessibility tree —
    // Delete included — so inert every ancestor sibling of the overlay. This is
    // what <dialog>.showModal() does, which we cannot use: swapping the element
    // would remount the preview. Alerts are exempt: the error toast draws above
    // the overlay and must stay clickable and announced.
    const inerted: HTMLElement[] = [];
    for (let node: HTMLElement | null = container.current; node?.parentElement; node = node.parentElement) {
      for (const sibling of node.parentElement.children) {
        if (sibling.getAttribute("role") === "alert") continue;
        if (sibling !== node && sibling instanceof HTMLElement && !sibling.inert) {
          sibling.inert = true;
          inerted.push(sibling);
        }
      }
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(false); };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      inerted.forEach((el) => { el.inert = false; });
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [fullscreen]);

  useEffect(() => {
    if (!fullscreen && wasFullscreen.current) fullscreenTrigger.current?.focus();
    wasFullscreen.current = fullscreen;
  }, [fullscreen]);

  const body = useMemo(() => {
    if (!artifact || !kind) return null;
    const truncated = text !== null && text.length > MAX_RENDERED_CHARS;
    const shown = truncated ? text!.slice(0, MAX_RENDERED_CHARS) : text ?? "";
    const notice = truncated && (
      <div className="viewer-notice">
        showing the first {formatSize(MAX_RENDERED_CHARS)} of this file — download for the rest.
      </div>
    );

    if (kind === "html" && text !== null) {
      // sandbox without allow-same-origin = opaque origin: no cookies, no
      // credentialed same-origin fetch, no parent access. Never feed agent
      // HTML to a blob: URL — blob documents inherit this app's origin.
      return showSource ? (
        <><pre>{shown}</pre>{notice}</>
      ) : (
        <iframe sandbox="allow-scripts" srcDoc={text} title={artifact.name} className="viewer-frame" />
      );
    }
    if (kind === "markdown" && text !== null) {
      return showSource ? <><pre>{shown}</pre>{notice}</> : <><Markdown source={shown} />{notice}</>;
    }
    if (kind === "svg") {
      // <img> renders SVG in image mode: scripts never run. Never use
      // <object>/<embed>/<iframe> here — those give the SVG a scriptable
      // document on a same-origin blob URL.
      if (showSource && text !== null) return <><pre>{shown}</pre>{notice}</>;
      // eslint-disable-next-line @next/next/no-img-element -- blob URLs can't use next/image
      return blobUrl ? <img src={blobUrl} alt={artifact.name} /> : null;
    }
    if (kind === "image") {
      // eslint-disable-next-line @next/next/no-img-element -- blob URLs can't use next/image
      return blobUrl ? <img src={blobUrl} alt={artifact.name} /> : null;
    }
    if (kind === "json" && text !== null) {
      let out = shown;
      if (!truncated && artifact.size_bytes <= MAX_PRETTY_JSON_BYTES) {
        try { out = JSON.stringify(JSON.parse(text), null, 2); } catch { /* show raw */ }
      }
      return <><pre>{out}</pre>{notice}</>;
    }
    if (kind === "csv" && text !== null) {
      if (showSource) return <><pre>{shown}</pre>{notice}</>;
      const delimiter = artifact.name.endsWith(".tsv") ? "\t" : ",";
      const rows = parseDelimited(shown, delimiter);
      const capped = rows.length > MAX_CSV_ROWS;
      const [head, ...data] = capped ? rows.slice(0, MAX_CSV_ROWS) : rows;
      if (!head) return <span className="muted">empty file</span>;
      return (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr>{head.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
              <tbody>
                {data.map((r, i) => (
                  <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          {(capped || truncated) && (
            <div className="viewer-notice">
              showing the first {MAX_CSV_ROWS} rows — download for the full file.
            </div>
          )}
        </>
      );
    }
    if (kind === "pdf") {
      // Browser-native PDF viewer on a type-forced blob; sandboxing would
      // disable the PDF plugin, and the viewer is not agent-controlled HTML.
      return blobUrl ? (
        <iframe src={blobUrl} title={artifact.name} className="viewer-frame" />
      ) : null;
    }
    if (kind === "text" && text !== null) {
      // Syntax highlighting is a deliberate non-dependency for now.
      return <><pre>{shown}</pre>{notice}</>;
    }
    return (
      <div className="muted" style={{ padding: "24px 0", textAlign: "center" }}>
        no preview for {artifact.content_type} —{" "}
        <a className="mono" href={contentPath}>download ({formatSize(artifact.size_bytes)})</a>
      </div>
    );
  }, [artifact, kind, text, blobUrl, showSource, contentPath]);

  if (missing) {
    return (
      <div className="panel">
        <a className="back" href="#artifacts">Artifacts</a>
        <p className="muted mt12">Artifact not found. It may have been deleted or unshared.</p>
      </div>
    );
  }
  if (!artifact) return <p className="muted">loading…</p>;

  const hasSourceToggle =
    kind !== null && ["html", "markdown", "svg", "csv"].includes(kind) && text !== null;
  const canFullscreen = kind !== null && kind !== "binary";

  const sourceToggle = hasSourceToggle && (
    <>
      <button className={`chip ${showSource ? "" : "on"}`} onClick={() => setShowSource(false)}>
        Preview
      </button>
      <button className={`chip ${showSource ? "on" : ""}`} onClick={() => setShowSource(true)}>
        Source
      </button>
    </>
  );

  async function copyLink() {
    const path = artifact!.share_token
      ? `/#artifacts/shared/${artifact!.share_token}`
      : `/#artifacts/${artifact!.id}`;
    await navigator.clipboard.writeText(`${window.location.origin}${path}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function setShared(shared: boolean) {
    const updated = shared
      ? await api<Artifact>(`/v1/artifacts/${artifact!.id}/share`, { json: {} })
      : await api<Artifact>(`/v1/artifacts/${artifact!.id}/share`, { method: "DELETE" });
    setArtifact(updated);
  }

  async function remove() {
    if (
      await apiConfirm(
        `Delete "${artifact!.name}"? Its content and share link are removed.`,
        `/v1/artifacts/${artifact!.id}`,
        { method: "DELETE" },
      )
    ) {
      window.location.hash = "#artifacts";
    }
  }

  return (
    <>
      <div className="row between mb16">
        <div className="row">
          <a className="back" href="#artifacts">Artifacts</a>
          <h2 style={{ fontSize: 20, fontWeight: 650 }}>{artifact.name}</h2>
          {artifact.share_token && <span className="badge running">shared</span>}
        </div>
        <div className="row">
          {sourceToggle}
          {canFullscreen && (
            <button className="ghost" ref={fullscreenTrigger} onClick={() => setFullscreen(true)}>
              Full screen
            </button>
          )}
          <a className="mono" href={contentPath}>Download</a>
          <button className="ghost" onClick={copyLink}>{copied ? "Copied!" : "Copy link"}</button>
          {!token && (
            <>
              <button className="ghost" onClick={() => setShared(!artifact.share_token)}>
                {artifact.share_token ? "Unshare" : "Share"}
              </button>
              <button className="danger" onClick={remove}>Delete</button>
            </>
          )}
        </div>
      </div>
      <div className="panel mb12">
        <dl className="kv">
          <dt>Agent</dt><dd>{agentName(agents, artifact.agent_id)}</dd>
          <dt>Session</dt><dd className="mono">{artifact.session_id}</dd>
          <dt>Type</dt><dd className="mono">{artifact.content_type}</dd>
          <dt>Size</dt><dd>{formatSize(artifact.size_bytes)} · v{artifact.version}</dd>
          <dt>Updated</dt><dd>{new Date(artifact.updated_at).toLocaleString()}</dd>
          {artifact.description && <><dt>Description</dt><dd>{artifact.description}</dd></>}
        </dl>
      </div>
      {/* One container in both modes: remounting would reload the preview iframe. */}
      <div
        ref={container}
        className={fullscreen ? "viewer-full" : "panel"}
        role={fullscreen ? "dialog" : undefined}
        aria-modal={fullscreen || undefined}
        aria-label={fullscreen ? artifact.name : undefined}
      >
        {fullscreen && (
          <div className="viewer-full-bar">
            <span className="mono viewer-full-name">{artifact.name}</span>
            <span className="row">
              {sourceToggle}
              <a className="mono" href={contentPath}>Download</a>
              <button className="ghost" autoFocus onClick={() => setFullscreen(false)}>
                Exit full screen
              </button>
            </span>
          </div>
        )}
        <div className={`viewer-body ${fullscreen ? "viewer-full-body" : ""}`}>
          {body ?? <span className="muted">loading content…</span>}
        </div>
      </div>
    </>
  );
}
