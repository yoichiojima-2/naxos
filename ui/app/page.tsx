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
import Monitoring from "@/components/monitoring";
import {
  AgentsIcon,
  ArtifactsIcon,
  DeploymentsIcon,
  DocsIcon,
  MemoryIcon,
  MonitoringIcon,
  SessionsIcon,
  SkillsIcon,
  VaultsIcon,
} from "@/components/icons";

const PAGES = [
  "sessions", "agents", "deployments", "artifacts", "monitoring", "vaults", "memory", "skills",
  "docs",
] as const;
type Page = (typeof PAGES)[number];
type Route = { page: Page; id?: string };

const NAV: { page: Page; label: string; icon: () => React.ReactNode }[] = [
  { page: "sessions", label: "Sessions", icon: SessionsIcon },
  { page: "agents", label: "Agents", icon: AgentsIcon },
  { page: "deployments", label: "Deployments", icon: DeploymentsIcon },
  { page: "artifacts", label: "Artifacts", icon: ArtifactsIcon },
  { page: "monitoring", label: "Monitoring", icon: MonitoringIcon },
  { page: "vaults", label: "Vaults", icon: VaultsIcon },
  { page: "memory", label: "Memory", icon: MemoryIcon },
  { page: "skills", label: "Skills", icon: SkillsIcon },
  { page: "docs", label: "Docs", icon: DocsIcon },
];

const NAV_SECTIONS: { label: string; pages: Page[] }[] = [
  { label: "Work", pages: ["sessions", "deployments", "artifacts", "monitoring"] },
  { label: "Configure", pages: ["agents", "skills", "vaults", "memory"] },
  { label: "Resources", pages: ["docs"] },
];

const PAGE_INFO: Record<Page, string> = {
  sessions:
    "Live agent runs. Follow the event stream in real time, send messages, and approve or deny tool calls the agent is waiting on.",
  agents:
    "Define who your agents are: instructions, model, tools, and permission policy. Every edit creates a new version, and the kill switch to disable an agent instantly lives here.",
  deployments:
    "Unattended scheduled runs. A cron schedule wakes an agent with a fixed prompt — no human in the loop, results land as sessions.",
  artifacts:
    "Outputs agents chose to publish: reports, datasets, generated files. Open them in the built-in viewer, download, share a stable org-internal link, or delete them — sharing never leaves the IAP boundary.",
  monitoring:
    "Cost and usage across the platform: spend over time and per agent and model, tool-call activity, and deployment outcomes — aggregated from every wake-to-idle run.",
  vaults:
    "Credentials for external services. Only names and targets are shown here — secret values are stored in Secret Manager, injected by the egress proxy at request time, and never enter the agent's sandbox or leave the API.",
  memory:
    "What agents remember across sessions. Create, rename, and delete memory stores, see which agents use each one, and browse a store's files — open one to edit its content, rename it, or create and delete files directly.",
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
  return { page: "sessions" };
}

export default function Page() {
  const [route, setRoute] = useState<Route>({ page: "sessions" });
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
      <header className="appbar">
        <a className="brand" href="#sessions">
          <span className="brand-mark" aria-hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2 22 12 12 22 2 12z" />
            </svg>
          </span>
          <span>naxos</span>
        </a>
        <div className="appbar-spacer" />
        <button className="icon-btn" onClick={toggleTheme} aria-label="toggle dark mode">
          ☾
        </button>
      </header>
      <div className="body">
        <aside className="sidebar">
          {NAV_SECTIONS.map(({ label, pages }) => (
            <nav className="nav-group" key={label}>
              <span className="nav-label">{label}</span>
              {pages.map((page) => {
                const { label: pageLabel, icon: Icon } = NAV.find((n) => n.page === page)!;
                return (
                  <a
                    key={page}
                    href={`#${page}`}
                    className={page === route.page ? "active" : ""}
                  >
                    <Icon />
                    {pageLabel}
                  </a>
                );
              })}
            </nav>
          ))}
        </aside>
        <div className="frame">
          <main className="content">
            {!agentDetail && !artifactDetail && (
              <div className="page-head">
                <div className="breadcrumbs">
                  naxos<span className="sep">/</span>{current.label}
                </div>
                <h2>{current.label}</h2>
                <p>{PAGE_INFO[route.page]}</p>
              </div>
            )}
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
            {route.page === "monitoring" && <Monitoring />}
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
    </div>
  );
}
