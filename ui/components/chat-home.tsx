"use client";

import { useEffect, useState } from "react";
import { api, Agent, Session } from "@/lib/api";
import Composer from "@/components/composer";
import { bumpSessions } from "@/components/chat";

const LAST_AGENT = "naxos.lastAgent";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Up late?";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** The new-conversation surface: a greeting and a composer. Typing is the
 * whole flow — sending creates the session and opens it. */
export default function ChatHome({ agents }: { agents: Agent[] }) {
  const [agentId, setAgentId] = useState("");

  useEffect(() => {
    try {
      const last = localStorage.getItem(LAST_AGENT);
      if (last && agents.some((a) => a.id === last)) {
        setAgentId(last);
        return;
      }
    } catch {
      /* storage unavailable */
    }
    if (agents.length) setAgentId((prev) => prev || agents[0].id);
  }, [agents]);

  const enabled = agents.filter((a) => !a.disabled);

  async function start(text: string) {
    const target = agentId || enabled[0]?.id;
    if (!target) return;
    const session = await api<Session>("/v1/sessions", {
      json: {
        agent: { id: target },
        initial_events: [{ type: "user.message", content: [{ type: "text", text }] }],
      },
    });
    try {
      localStorage.setItem(LAST_AGENT, target);
    } catch {
      /* storage unavailable */
    }
    bumpSessions();
    window.location.hash = `#chat/${session.id}`;
  }

  return (
    <div className="home">
      <h1 className="greet">{greeting()}</h1>
      {enabled.length === 0 ? (
        <p className="muted">
          no agents yet — <a className="mono" href="#agents">create one in the console</a> to start
          chatting.
        </p>
      ) : (
        <Composer
          placeholder="How can I help you today?"
          autoFocus
          draftKey="new"
          onSend={start}
          left={
            <select
              className="composer-agent"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              aria-label="agent to chat with"
            >
              {enabled.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          }
        />
      )}
    </div>
  );
}
