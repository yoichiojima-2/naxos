"use client";

import { useCallback, useEffect, useState } from "react";
import { api, favKey, Agent, Environment, Favorite, FavoriteType } from "@/lib/api";
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
import Approvals from "@/components/approvals";
import {
  AgentsIcon,
  ApprovalsIcon,
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
  "sessions", "approvals", "deployments", "artifacts", "monitoring", "agents", "vaults",
  "memory", "skills", "docs",
] as const;
type Page = (typeof PAGES)[number];
type Route = { page: Page; id?: string };

const APPROVAL_POLL_MS = 15_000;

const NAV: Record<Page, { label: string; icon: () => React.ReactNode; info: string }> = {
  sessions: {
    label: "Sessions",
    icon: SessionsIcon,
    info: "Live agent runs. Follow the event stream in real time, send messages, and approve or deny tool calls the agent is waiting on.",
  },
  approvals: {
    label: "Approvals",
    icon: ApprovalsIcon,
    info: "Every tool call an agent is paused on, across all sessions. Allow or deny from here — the agent's container is released while it waits, so a pending approval costs nothing but blocks the work until someone answers.",
  },
  agents: {
    label: "Agents",
    icon: AgentsIcon,
    info: "Define who your agents are: instructions, model, tools, and permission policy. Every edit creates a new version, and the kill switch to disable an agent instantly lives here.",
  },
  deployments: {
    label: "Deployments",
    icon: DeploymentsIcon,
    info: "Unattended scheduled runs. A cron schedule wakes an agent with a fixed prompt — no human in the loop, results land as sessions.",
  },
  artifacts: {
    label: "Artifacts",
    icon: ArtifactsIcon,
    info: "Outputs agents chose to publish: reports, datasets, generated files. Open them in the built-in viewer, download, share a stable org-internal link, or delete them — sharing never leaves the IAP boundary.",
  },
  monitoring: {
    label: "Monitoring",
    icon: MonitoringIcon,
    info: "Cost and usage across the platform: spend over time and per agent and model, tool-call activity, and deployment outcomes — aggregated from every wake-to-idle run.",
  },
  vaults: {
    label: "Vaults",
    icon: VaultsIcon,
    info: "Credentials for external services. Only names and targets are shown here — secret values are stored in Secret Manager, injected by the egress proxy at request time, and never enter the agent's sandbox or leave the API.",
  },
  memory: {
    label: "Memory",
    icon: MemoryIcon,
    info: "What agents remember across sessions. Create, rename, and delete memory stores, see which agents use each one, and browse a store's files — open one to edit its content, rename it, or create and delete files directly.",
  },
  skills: {
    label: "Skills",
    icon: SkillsIcon,
    info: "Reusable know-how shared across the organization. A skill is a folder of instructions (SKILL.md plus supporting files) that any agent can be given; agents load it read-only — skills are edited only here.",
  },
  docs: {
    label: "Docs",
    icon: DocsIcon,
    info: "How naxos works and how to run your first agent session — from environment to agent to session.",
  },
};

const NAV_SECTIONS: { label: string; pages: Page[] }[] = [
  { label: "Work", pages: ["sessions", "approvals", "deployments", "artifacts", "monitoring"] },
  { label: "Configure", pages: ["agents", "skills", "vaults", "memory"] },
  { label: "Resources", pages: ["docs"] },
];

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
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);
  const [theme, setTheme] = useState<string | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState(0);

  const refresh = useCallback(async () => {
    const [agentResult, envResult, favResult] = await Promise.all([
      api<{ data: Agent[] }>("/v1/agents"),
      api<{ data: Environment[] }>("/v1/environments"),
      api<{ data: Favorite[] }>("/v1/favorites"),
    ]);
    setAgents(agentResult.data);
    setEnvironments(envResult.data);
    setFavorites(new Set(favResult.data.map((f) => favKey(f.entity_type, f.entity_id))));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // The badge is the whole point of the inbox: a paused agent has to be visible
  // from wherever you happen to be standing, not only from its own session.
  const refreshApprovals = useCallback(async () => {
    const result = await api<{ pending: number }>("/v1/tool_confirmations?limit=1");
    setPendingApprovals(result.pending);
  }, []);

  useEffect(() => {
    refreshApprovals();
    const timer = setInterval(refreshApprovals, APPROVAL_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshApprovals]);

  const toggleFavorite = useCallback(async (type: FavoriteType, id: string) => {
    const key = favKey(type, id);
    const on = favorites.has(key);
    if (on) await api(`/v1/favorites/${type}/${id}`, { method: "DELETE" });
    else await api("/v1/favorites", { json: { entity_type: type, entity_id: id } });
    setFavorites((prev) => {
      const next = new Set(prev);
      if (on) next.delete(key);
      else next.add(key);
      return next;
    });
  }, [favorites]);

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
    setTheme(
      document.documentElement.dataset.theme ??
        (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
    );
  }, []);

  function toggleTheme() {
    if (!theme) return;
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    setTheme(next);
  }

  // Collapsed to a scrolling row on narrow screens, the active page can sit
  // past the right edge; keep it in view.
  useEffect(() => {
    document
      .querySelector(".sidebar a.active")
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [route.page]);

  const current = NAV[route.page];
  const agentDetail = route.page === "agents" ? route.id : undefined;
  const artifactDetail = route.page === "artifacts" ? route.id : undefined;
  const sessionDetail = route.page === "sessions" ? route.id : undefined;

  useEffect(() => {
    document.title = `naxos · ${current.label}`;
  }, [current.label]);

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
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>
      <div className="body">
        <aside className="sidebar">
          {NAV_SECTIONS.map(({ label, pages }) => (
            <nav className="nav-group" key={label}>
              <span className="nav-label">{label}</span>
              {pages.map((page) => {
                const { label: pageLabel, icon: Icon } = NAV[page];
                const badge = page === "approvals" && pendingApprovals > 0;
                return (
                  <a
                    key={page}
                    href={`#${page}`}
                    className={page === route.page ? "active" : ""}
                  >
                    <Icon />
                    {pageLabel}
                    {badge && (
                      <span className="nav-count" aria-label={`${pendingApprovals} waiting`}>
                        {pendingApprovals}
                      </span>
                    )}
                  </a>
                );
              })}
            </nav>
          ))}
        </aside>
        <div className="frame">
          <main className="content">
            {!agentDetail && !artifactDetail && !sessionDetail && (
              <div className="page-head">
                <div className="breadcrumbs">
                  naxos<span className="sep">/</span>{current.label}
                </div>
                <h2>{current.label}</h2>
                <p>{current.info}</p>
              </div>
            )}
            {route.page === "sessions" && (
              <Sessions
                agents={agents}
                sessionId={sessionDetail}
                favorites={favorites}
                onToggleFavorite={toggleFavorite}
              />
            )}
            {route.page === "agents" && !route.id && (
              <Agents
                agents={agents}
                environments={environments}
                onChange={refresh}
                favorites={favorites}
                onToggleFavorite={toggleFavorite}
              />
            )}
            {agentDetail && (
              <AgentDetail
                agentId={agentDetail}
                environments={environments}
                onChange={refresh}
              />
            )}
            {route.page === "deployments" && <Deployments agents={agents} view={route.id} />}
            {route.page === "artifacts" && !route.id && (
              <Artifacts agents={agents} favorites={favorites} onToggleFavorite={toggleFavorite} />
            )}
            {artifactDetail && (
              artifactDetail.startsWith("shared/")
                ? <ArtifactViewer token={artifactDetail.slice("shared/".length)} agents={agents} />
                : <ArtifactViewer artifactId={artifactDetail} agents={agents} />
            )}
            {route.page === "approvals" && <Approvals onDecided={refreshApprovals} />}
            {route.page === "monitoring" && <Monitoring />}
            {route.page === "vaults" && <Vaults />}
            {route.page === "memory" && <MemoryStores />}
            {route.page === "skills" && (
              <Skills favorites={favorites} onToggleFavorite={toggleFavorite} />
            )}
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
