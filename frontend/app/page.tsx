"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Message = { who: "you" | "agent"; text: string; meta?: string };

type Run = {
  run_id: string;
  session_id: string | null;
  role: string | null;
  principal: string | null;
  started_at: string;
  prompt: string;
  text: string;
  num_turns: number | null;
  cost_usd: number | null;
  is_error: boolean;
};

export default function Page() {
  const [tab, setTab] = useState<"chat" | "history">("chat");
  const [roles, setRoles] = useState<string[]>([]);
  const [role, setRole] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [me, setMe] = useState("");
  const [status, setStatus] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/roles")
      .then((r) => r.json())
      .then((list) => {
        setRoles(list);
        if (list.length) setRole(list[0]);
      })
      .catch(() => setRoles([]));
    fetch("/api/me")
      .then((r) => r.json())
      .then((data) => setMe(data.email))
      .catch(() => setMe(""));
  }, []);

  async function loadRuns() {
    try {
      setRuns(await (await fetch("/api/runs")).json());
    } catch {
      setRuns([]);
    }
  }

  useEffect(() => {
    if (tab === "history" && runs.length === 0) loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function statusLabel(event: { event: string; name?: string }): string {
    if (event.event === "tool") return `using ${event.name?.split("__").pop()}…`;
    if (event.event === "text") return "writing…";
    return "thinking…";
  }

  async function send() {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    setMessages((m) => [...m, { who: "you", text: prompt }]);
    setBusy(true);
    setStatus("starting…");
    try {
      const response = await fetch("/api/run/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, role, resume: sessionId }),
      });
      if (!response.ok || !response.body) {
        const detail = (await response.json()).detail ?? response.statusText;
        setMessages((m) => [...m, { who: "agent", text: `error: ${detail}` }]);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let final = "";
      let meta = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.event === "result") {
            setSessionId(event.session_id);
            const cost = event.cost_usd != null ? `$${event.cost_usd.toFixed(4)}` : "";
            final = event.text;
            meta = `${cost} · ${event.num_turns} turns`;
          } else if (event.event === "error") {
            final = `error: ${event.detail}`;
          } else {
            setStatus(statusLabel(event));
          }
        }
      }
      setMessages((m) => [...m, { who: "agent", text: final, meta: meta || undefined }]);
    } catch (e) {
      setMessages((m) => [...m, { who: "agent", text: `error: ${e}` }]);
    } finally {
      setBusy(false);
      setStatus("");
    }
  }

  function openSession(run: Run) {
    if (run.role) setRole(run.role);
    setSessionId(run.session_id);
    setMessages([
      { who: "you", text: run.prompt },
      { who: "agent", text: run.text, meta: "from history" },
    ]);
    setTab("chat");
  }

  function newChat() {
    setSessionId(null);
    setMessages([]);
  }

  return (
    <main>
      <header>
        <h1>
          <svg width="13" height="16" viewBox="0 0 13 16" fill="none" strokeWidth="2" aria-hidden="true">
            <path d="M2 16V1h9v15" />
          </svg>
          naxos
        </h1>
        <nav>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            chat
          </button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
            history
          </button>
        </nav>
        <div className="controls">
          {me && <span className="me">{me}</span>}
          <select value={role} onChange={(e) => setRole(e.target.value)} disabled={sessionId != null}>
            {roles.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <button onClick={newChat}>new chat</button>
        </div>
      </header>

      {tab === "chat" && (
        <section className="chat">
          <div className="messages">
            {messages.length === 0 && <p className="empty">ask {role} anything</p>}
            {messages.map((message, i) => (
              <div key={i} className={`message ${message.who}`}>
                {message.who === "agent" ? (
                  <div className="md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
                  </div>
                ) : (
                  <pre>{message.text}</pre>
                )}
                {message.meta && <span className="meta">{message.meta}</span>}
              </div>
            ))}
            {busy && (
              <div className="message agent thinking">
                <span />
                <span />
                <span />
                <em className="status">{status}</em>
              </div>
            )}
            <div ref={bottom} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={sessionId ? "continue this session…" : "new session…"}
              disabled={busy}
            />
            <button type="submit" disabled={busy}>
              send
            </button>
          </form>
          {sessionId && <p className="session">session {sessionId}</p>}
        </section>
      )}

      {tab === "history" && (
        <section className="history">
          <button className="refresh" onClick={loadRuns}>
            refresh
          </button>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>started</th>
                  <th>role</th>
                  <th>principal</th>
                  <th>prompt</th>
                  <th>cost</th>
                  <th>turns</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 && (
                  <tr className="empty-row">
                    <td colSpan={6}>no runs yet</td>
                  </tr>
                )}
                {runs.map((run) => (
                  <tr key={run.run_id} className={run.is_error ? "error" : ""} onClick={() => openSession(run)}>
                    <td>{run.started_at?.replace("T", " ").slice(0, 16)}</td>
                    <td>{run.role ?? "-"}</td>
                    <td>{run.principal ?? "-"}</td>
                    <td className="prompt">{run.prompt}</td>
                    <td>{run.cost_usd != null ? `$${run.cost_usd.toFixed(3)}` : "-"}</td>
                    <td>{run.num_turns ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
