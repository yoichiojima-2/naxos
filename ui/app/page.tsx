"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Agent } from "@/lib/api";
import Sessions from "@/components/sessions";
import Deployments from "@/components/deployments";
import Vaults from "@/components/vaults";
import MemoryStores from "@/components/memory";
import {
  DeploymentsIcon,
  MemoryIcon,
  SessionsIcon,
  VaultsIcon,
} from "@/components/icons";

const PAGES = ["sessions", "deployments", "vaults", "memory"] as const;
type Page = (typeof PAGES)[number];
type Route = { page: Page; id?: string };

const NAV: { page: Page; label: string; icon: () => React.ReactNode }[] = [
  { page: "sessions", label: "Sessions", icon: SessionsIcon },
  { page: "deployments", label: "Deployments", icon: DeploymentsIcon },
  { page: "vaults", label: "Vaults", icon: VaultsIcon },
  { page: "memory", label: "Memory", icon: MemoryIcon },
];

const PAGE_INFO: Record<Page, string> = {
  sessions:
    "Live agent runs. Follow the event stream in real time, send messages, and approve or deny tool calls the agent is waiting on.",
  deployments:
    "Unattended scheduled runs. A cron schedule wakes an agent with a fixed prompt — no human in the loop, results land as sessions.",
  vaults:
    "Credentials for external services. Only names and targets are shown here — secret values are stored in Secret Manager, injected by the egress proxy at request time, and never enter the agent's sandbox or leave the API.",
  memory:
    "What agents remember across sessions. Browse each memory store's files, open one to read or edit it.",
};

function parseHash(hash: string): Route {
  const [page, id] = hash.replace(/^#/, "").split("/");
  if ((PAGES as readonly string[]).includes(page)) {
    return { page: page as Page, id: id || undefined };
  }
  return { page: "sessions" };
}

export default function Page() {
  const [route, setRoute] = useState<Route>({ page: "sessions" });
  const [agents, setAgents] = useState<Agent[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [theme, setTheme] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const result = await api<{ data: Agent[] }>("/v1/agents");
    setAgents(result.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    const fromHash = () => setRoute(parseHash(window.location.hash));
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

  useEffect(() => {
    const onError = (e: Event) => setToast((e as CustomEvent<string>).detail);
    window.addEventListener("api-error", onError);
    return () => window.removeEventListener("api-error", onError);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme ?? null);
  }, []);

  function toggleTheme() {
    const effective =
      theme ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = effective === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    setTheme(next);
  }

  const current = NAV.find((n) => n.page === route.page) ?? NAV[0];

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#sessions"><span>naxos</span></a>
        <nav>
          {NAV.map(({ page, label, icon: Icon }) => (
            <a
              key={page}
              href={`#${page}`}
              className={page === route.page ? "active" : ""}
            >
              <Icon />
              {label}
            </a>
          ))}
        </nav>
      </aside>
      <div className="frame">
        <header className="topbar">
          <span className="topbar-title">{current.label}</span>
          <button className="icon-btn" onClick={toggleTheme} aria-label="toggle dark mode">
            ☾
          </button>
        </header>
        <main className="content">
          <div className="page-head">
            <h2>{current.label}</h2>
            <p>{PAGE_INFO[route.page]}</p>
          </div>
          {route.page === "sessions" && <Sessions agents={agents} />}
          {route.page === "deployments" && <Deployments agents={agents} />}
          {route.page === "vaults" && <Vaults />}
          {route.page === "memory" && <MemoryStores />}
        </main>
        {toast && (
          <div className="toast" role="alert" onClick={() => setToast(null)}>
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}
