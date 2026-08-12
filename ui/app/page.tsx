"use client";

import { useCallback, useEffect, useState } from "react";
import { api, favKey, Agent, Environment, Favorite, FavoriteType, Session } from "@/lib/api";
import { shortId } from "@/lib/format";
import Agents from "@/components/agents";
import AgentDetail from "@/components/agent-detail";
import Sessions from "@/components/sessions";
import Chat from "@/components/chat";
import ChatHome from "@/components/chat-home";
import Deployments from "@/components/deployments";
import Vaults from "@/components/vaults";
import MemoryStores from "@/components/memory";
import Artifacts from "@/components/artifacts";
import ArtifactViewer from "@/components/artifact-viewer";
import Skills from "@/components/skills";
import Docs from "@/components/docs";
import Monitoring from "@/components/monitoring";
import DialogHost from "@/components/confirm";
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

const CONSOLE_PAGES = [
  "sessions", "agents", "deployments", "artifacts", "monitoring", "vaults", "memory", "skills",
  "docs",
] as const;
type ConsolePage = (typeof CONSOLE_PAGES)[number];
type Route = { page: ConsolePage | "new" | "chat"; id?: string };

const NAV: Record<ConsolePage, { label: string; icon: () => React.ReactNode; info: string }> = {
  sessions: {
    label: "All sessions",
    icon: SessionsIcon,
    info: "Every session on the platform, for operating rather than chatting: filter, set budgets, terminate, or delete in bulk. Open one to continue the conversation.",
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

const CONSOLE_ORDER: ConsolePage[] = [
  "sessions", "agents", "deployments", "artifacts", "monitoring", "skills", "vaults", "memory",
  "docs",
];

function parseHash(hash: string): Route {
  const [page, ...rest] = hash.replace(/^#/, "").split("/");
  const id = rest.join("/") || undefined;
  if (page === "chat" && id) return { page: "chat", id };
  // Old deep links: #sessions/{id} was the timeline; it is a chat now.
  if (page === "sessions" && id) return { page: "chat", id };
  if ((CONSOLE_PAGES as readonly string[]).includes(page)) {
    return { page: page as ConsolePage, id };
  }
  return { page: "new" };
}

function timeGroup(iso: string): string {
  const day = 24 * 60 * 60 * 1000;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const at = new Date(iso).getTime();
  if (at >= today) return "Today";
  if (at >= today - day) return "Yesterday";
  if (at >= today - 7 * day) return "Previous 7 days";
  return "Older";
}

const needsApproval = (s: Session) =>
  s.status === "idle" && s.stop_reason === "requires_action";

export default function Page() {
  const [route, setRoute] = useState<Route>({ page: "new" });
  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [sessions, setSessions] = useState<Session[]>([]);
  const [query, setQuery] = useState("");
  const [sideOpen, setSideOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [theme, setTheme] = useState<string | null>(null);

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

  const refreshSessions = useCallback(async () => {
    const result = await api<{ data: Session[] }>("/v1/sessions?limit=200");
    setSessions(result.data);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    refreshSessions();
    const timer = setInterval(refreshSessions, 20_000);
    window.addEventListener("naxos-sessions", refreshSessions);
    return () => {
      clearInterval(timer);
      window.removeEventListener("naxos-sessions", refreshSessions);
    };
  }, [refreshSessions]);

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
    const fromHash = () => {
      const next = parseHash(window.location.hash);
      setRoute(next);
      setSideOpen(false);
      // Normalize legacy #sessions/{id} links so copied URLs stay canonical.
      if (next.page === "chat" && window.location.hash.startsWith("#sessions/")) {
        window.history.replaceState(null, "", `#chat/${next.id}`);
      }
    };
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

  const consolePage = route.page !== "new" && route.page !== "chat"
    ? (route.page as ConsolePage)
    : null;

  useEffect(() => {
    document.title = consolePage ? `naxos · ${NAV[consolePage].label}` : "naxos";
  }, [consolePage]);

  const q = query.trim().toLowerCase();
  const recents = sessions.filter(
    (s) => !q || `${s.title ?? ""} ${s.id}`.toLowerCase().includes(q),
  );
  const starred = recents.filter((s) => favorites.has(favKey("session", s.id)));
  const rest = recents.filter((s) => !favorites.has(favKey("session", s.id)));
  const groups: { label: string; items: Session[] }[] = [];
  for (const s of rest) {
    const label = timeGroup(s.created_at);
    const group = groups[groups.length - 1];
    if (group?.label === label) group.items.push(s);
    else groups.push({ label, items: [s] });
  }

  const sideItem = (s: Session) => (
    <a
      key={s.id}
      className={`side-item ${route.page === "chat" && route.id === s.id ? "active" : ""}`}
      href={`#chat/${s.id}`}
    >
      {needsApproval(s) && <span className="dot ask" title="waiting for approval" />}
      {(s.status === "running" || s.status === "rescheduling") && (
        <span className="dot live" title="agent is working" />
      )}
      <span className="title">{s.title ?? shortId(s.id)}</span>
    </a>
  );

  return (
    <div className="shell">
      <button
        className="icon-btn side-toggle"
        onClick={() => setSideOpen(!sideOpen)}
        aria-label="toggle navigation"
      >
        ☰
      </button>
      <div className={`side-scrim ${sideOpen ? "show" : ""}`} onClick={() => setSideOpen(false)} />
      <aside className={`side ${sideOpen ? "open" : ""}`}>
        <div className="side-head">
          <a className="brand" href="#new">
            <span className="brand-mark" aria-hidden>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2 22 12 12 22 2 12z" />
              </svg>
            </span>
            <span>naxos</span>
          </a>
          <button className="icon-btn" onClick={toggleTheme} aria-label="toggle dark mode">
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
        <div className="side-top">
          <a className="new-chat" href="#new">
            <span className="plus" aria-hidden>+</span>
            New chat
          </a>
          {sessions.length > 0 && (
            <input
              className="side-search"
              placeholder="Search chats…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="search chats"
            />
          )}
        </div>
        <div className="side-scroll">
          {recents.length === 0 && (
            <p className="side-empty">
              {sessions.length === 0 ? "no chats yet" : "no chats match"}
            </p>
          )}
          {starred.length > 0 && (
            <div className="side-group">
              <span className="nav-label">Starred</span>
              {starred.map(sideItem)}
            </div>
          )}
          {groups.map(({ label, items }) => (
            <div className="side-group" key={label}>
              <span className="nav-label">{label}</span>
              {items.map(sideItem)}
            </div>
          ))}
        </div>
        <div className="side-foot">
          <span className="nav-label">Console</span>
          {CONSOLE_ORDER.map((page) => {
            const { label, icon: Icon } = NAV[page];
            return (
              <a
                key={page}
                href={`#${page}`}
                className={`side-item ${consolePage === page ? "active" : ""}`}
              >
                <Icon />
                <span className="title">{label}</span>
              </a>
            );
          })}
        </div>
      </aside>

      <main className="main">
        {route.page === "new" && <ChatHome agents={agents} />}
        {route.page === "chat" && route.id && (
          <Chat
            key={route.id}
            sessionId={route.id}
            agents={agents}
            favorites={favorites}
            onToggleFavorite={toggleFavorite}
          />
        )}
        {consolePage && (
          <div className="content">
            {!route.id && (
              <div className="page-head">
                <div className="breadcrumbs">
                  console<span className="sep">/</span>{NAV[consolePage].label}
                </div>
                <h2>{NAV[consolePage].label}</h2>
                <p>{NAV[consolePage].info}</p>
              </div>
            )}
            {consolePage === "sessions" && (
              <Sessions
                agents={agents}
                favorites={favorites}
                onToggleFavorite={toggleFavorite}
                onChange={refreshSessions}
              />
            )}
            {consolePage === "agents" && !route.id && (
              <Agents
                agents={agents}
                environments={environments}
                onChange={refresh}
                favorites={favorites}
                onToggleFavorite={toggleFavorite}
              />
            )}
            {consolePage === "agents" && route.id && (
              <AgentDetail
                agentId={route.id}
                environments={environments}
                onChange={refresh}
              />
            )}
            {consolePage === "deployments" && <Deployments agents={agents} view={route.id} />}
            {consolePage === "artifacts" && !route.id && (
              <Artifacts agents={agents} favorites={favorites} onToggleFavorite={toggleFavorite} />
            )}
            {consolePage === "artifacts" && route.id && (
              route.id.startsWith("shared/")
                ? <ArtifactViewer token={route.id.slice("shared/".length)} agents={agents} />
                : <ArtifactViewer artifactId={route.id} agents={agents} />
            )}
            {consolePage === "monitoring" && <Monitoring />}
            {consolePage === "vaults" && <Vaults />}
            {consolePage === "memory" && <MemoryStores />}
            {consolePage === "skills" && (
              <Skills favorites={favorites} onToggleFavorite={toggleFavorite} />
            )}
            {consolePage === "docs" && <Docs />}
          </div>
        )}
        {toast && (
          <div className="toast" role="alert" onClick={() => setToast(null)}>
            {toast}
          </div>
        )}
      </main>
      <DialogHost />
    </div>
  );
}
