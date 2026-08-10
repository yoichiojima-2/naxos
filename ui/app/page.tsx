"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Agent, Environment } from "@/lib/api";
import Agents from "@/components/agents";
import Sessions from "@/components/sessions";
import Deployments from "@/components/deployments";
import Vaults from "@/components/vaults";
import MemoryStores from "@/components/memory";

const TABS = ["sessions", "agents", "deployments", "vaults", "memory"] as const;
type Tab = (typeof TABS)[number];

const TAB_INFO: Record<Tab, string> = {
  sessions:
    "Live agent runs. Follow the event stream in real time, send messages, and approve or deny tool calls the agent is waiting on.",
  agents:
    "Define who your agents are: instructions, model, tools, and permission policy. Every edit creates a new version, and the kill switch to disable an agent instantly lives here.",
  deployments:
    "Unattended scheduled runs. A cron schedule wakes an agent with a fixed prompt — no human in the loop, results land as sessions.",
  vaults:
    "Credentials for external services. Secrets are injected by the egress proxy at request time and never enter the agent's sandbox.",
  memory:
    "What agents remember across sessions. Browse each memory store's files, open one to read or edit it.",
};

export default function Page() {
  const [tab, setTab] = useState<Tab>("sessions");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);

  const refresh = useCallback(async () => {
    const [agentResult, envResult] = await Promise.all([
      api<{ data: Agent[] }>("/v1/agents"),
      api<{ data: Environment[] }>("/v1/environments"),
    ]);
    setAgents(agentResult.data);
    setEnvironments(envResult.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <main>
      <header className="top">
        <h1><span>naxos</span> managed agents</h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      <div className="tab-info">
        <span>{tab}</span>
        {TAB_INFO[tab]}
      </div>
      {tab === "sessions" && <Sessions agents={agents} />}
      {tab === "agents" && <Agents agents={agents} environments={environments} onChange={refresh} />}
      {tab === "deployments" && <Deployments agents={agents} />}
      {tab === "vaults" && <Vaults />}
      {tab === "memory" && <MemoryStores />}
    </main>
  );
}
