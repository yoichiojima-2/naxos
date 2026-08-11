"use client";

import { useCallback, useEffect, useState } from "react";
import { api, Agent, Environment } from "@/lib/api";
import Agents from "@/components/agents";
import AgentDetail from "@/components/agent-detail";
import Sessions from "@/components/sessions";
import Deployments from "@/components/deployments";
import Vaults from "@/components/vaults";
import MemoryStores from "@/components/memory";
import Artifacts from "@/components/artifacts";
import ArtifactViewer from "@/components/artifact-viewer";
import Skills from "@/components/skills";
import Docs from "@/components/docs";
import Dashboard from "@/components/dashboard";
import {
  AgentsIcon,
  ArtifactsIcon,
  DeploymentsIcon,
  DocsIcon,
  MemoryIcon,
  SessionsIcon,
  SkillsIcon,
  VaultsIcon,
  DashboardIcon,
} from "@/components/icons";

const PAGES = [
  "dashboard", "sessions", "agents", "deployments", "artifacts", "vaults", "memory", "skills", "docs",
] as const;
type Page = (typeof PAGES)[number];
type Route = { page: Page; id?: string };

const NAV: { page: Page; label: string; icon: () => React.ReactNode }[] = [
  { page: "dashboard", label: "Dashboard", icon: DashboardIcon },
  { page: "sessions", label: "Sessions", icon: SessionsIcon },
  { page: "agents", label: "Agents", icon: AgentsIcon },
  { page: "deployments", label: "Deployments", icon: DeploymentsIcon },
  { page: "artifacts", label: "Artifacts", icon: ArtifactsIcon },
  { page: "vaults", label: "Vaults", icon: VaultsIcon },
  { page: "memory", label: "Memory", icon: MemoryIcon },
  { page: "skills", label: "Skills", icon: SkillsIcon },
  { page: "docs", label: "Docs", icon: DocsIcon },
];

const PAGE_INFO: Record<Page, string> = {
  dashboard:
    "A live overview of agent activity, approvals, workspace capacity, and recent sessions.",
  sessions:
    "Live agent runs. Follow the event stream in real time, send messages, and approve or deny tool calls the agent is waiting on.",
  agents:
    "Define who your agents are: instructions, model, tools, and permission policy. Every edit creates a new version, and the kill switch to disable an agent instantly lives here.",
  deployments:
    "Unattended scheduled runs. A cron schedule wakes an agent with a fixed prompt — no human in the loop, results land as sessions.",
  artifacts:
    "Outputs agents chose to publish: reports, datasets, generated files. Open them in the built-in viewer, download, share a stable org-internal link, or delete them — sharing never leaves the IAP boundary.",
  vaults:
    "Credentials for external services. Only names and targets are shown here — secret values are stored in Secret Manager, injected by the egress proxy at request time, and never enter the agent's sandbox or leave the API.",
  memory:
    "What agents remember across sessions. Browse each memory store's files — open one to edit its content, rename it, or create and delete files directly.",
  skills:
    "Reusable know-how shared across the organization. A skill is a folder of instructions (SKILL.md plus supporting files) that any agent can be given; agents load it read-only — skills are edited only here.",
  docs:
    "How naxos works and how to run your first agent session — from environment to agent to session.",
};

function parseHash(hash: string): Route {
  const [page, ...rest] = hash.replace(/^#/, "").split("/");
  if ((PAGES as readonly string[]).includes(page)) {
    return { page: page as Page, id: rest.join("/") || undefined };
  }
  return { page: "dashboard" };
}

export default function Page() {
  const [route, setRoute] = useState<Route>({ page: "dashboard" });
  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [theme, setTheme] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [agentResult, envResult] = await Promise.all([
      api<{ data: Agent[] }>("/v1/agents"),
      api<{ data: Environment[] }>("/v1/environments"),
    ]);
    setAgents(agentResult.data);
    setEnvironments(envResult.data);
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
  const agentDetail = route.page === "agents" && route.id;
  const artifactDetail = route.page === "artifacts" && route.id;

  return (
    <div className="shell">
      <aside className="global-rail" aria-label="Global navigation">
        <a className="rail-logo" href="#dashboard" aria-label="Naxos home">N</a>
        <div className="rail-actions">
          <button className="rail-button" aria-label="Search">⌕</button>
          <button className="rail-button rail-create" aria-label="Create">＋</button>
        </div>
        <div className="rail-bottom">
          <button className="rail-button" onClick={toggleTheme} aria-label="Toggle dark mode">◐</button>
          <span className="avatar" aria-label="Signed in user">YO</span>
        </div>
      </aside>
      <aside className="sidebar">
        <a className="brand" href="#dashboard">
          <span className="brand-mark">N</span>
          <span><strong>naxos</strong><small>Agent workspace</small></span>
        </a>
        <div className="nav-label">Workspace</div>
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
          <div className="breadcrumbs"><span>naxos</span><b>/</b><span>{current.label}</span></div>
          <div className="topbar-actions">
            <div className="quick-search"><span>⌕</span><span>Search workspace</span><kbd>/</kbd></div>
            <button className="icon-btn help-button" aria-label="Help">?</button>
          </div>
        </header>
        <main className="content">
          {!agentDetail && !artifactDetail && (
            <div className="page-head">
              <div className="eyebrow">Workspace</div>
              <h1>{current.label}</h1>
              <p>{PAGE_INFO[route.page]}</p>
            </div>
          )}
          {route.page === "dashboard" && <Dashboard agents={agents} environments={environments} />}
          {route.page === "sessions" && <Sessions agents={agents} />}
          {route.page === "agents" && !route.id && (
            <Agents agents={agents} environments={environments} onChange={refresh} />
          )}
          {agentDetail && (
            <AgentDetail
              agentId={route.id!}
              environments={environments}
              onChange={refresh}
            />
          )}
          {route.page === "deployments" && <Deployments agents={agents} />}
          {route.page === "artifacts" && !route.id && <Artifacts agents={agents} />}
          {artifactDetail && (
            route.id!.startsWith("shared/")
              ? <ArtifactViewer token={route.id!.slice("shared/".length)} agents={agents} />
              : <ArtifactViewer artifactId={route.id!} agents={agents} />
          )}
          {route.page === "vaults" && <Vaults />}
          {route.page === "memory" && <MemoryStores />}
          {route.page === "skills" && <Skills />}
          {route.page === "docs" && <Docs />}
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
