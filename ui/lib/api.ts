async function request(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<Response> {
  const options: RequestInit = { ...init };
  if (init?.json !== undefined) {
    options.method = init.method ?? "POST";
    options.body = JSON.stringify(init.json);
    options.headers = { "content-type": "application/json", ...init.headers };
  }
  let response: Response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    window.dispatchEvent(new CustomEvent("api-error", { detail: "network error — is the API reachable?" }));
    throw e;
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    const message = `${response.status}: ${detail}`;
    window.dispatchEvent(new CustomEvent("api-error", { detail: message }));
    throw new Error(message);
  }
  return response;
}

export async function api<T = unknown>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  return (await request(path, init)).json();
}

export async function apiBlob(path: string): Promise<Blob> {
  return (await request(path)).blob();
}

export async function listFor<T>(
  ids: string[],
  path: (id: string) => string,
): Promise<Record<string, T[]>> {
  const results = await Promise.all(ids.map((id) => api<{ data: T[] }>(path(id))));
  return Object.fromEntries(ids.map((id, i) => [id, results[i].data]));
}

export async function apiConfirm(
  message: string,
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<boolean> {
  if (!window.confirm(message)) return false;
  await api(path, init ?? { json: {} });
  return true;
}

// Mirrors naxos_shared.events.EventType.
export const EVENT_TYPES = [
  "user.message",
  "user.interrupt",
  "user.tool_confirmation",
  "user.custom_tool_result",
  "agent.message",
  "agent.thinking",
  "agent.tool_use",
  "agent.tool_result",
  "agent.artifact",
  "session.status_running",
  "session.status_idle",
  "session.status_rescheduling",
  "session.status_terminated",
  "session.error",
  "span.model_request_start",
  "span.model_request_end",
] as const;

export type Agent = {
  id: string;
  name: string;
  environment_id: string;
  latest_version: number;
  disabled: boolean;
};

export const agentName = (agents: Agent[], id: string) =>
  agents.find((a) => a.id === id)?.name ?? id;

export type EffortLevel = "low" | "medium" | "high" | "xhigh" | "max";

export type PermissionMode = "always_ask" | "always_allow";
export type PermissionRule = { tool: string; mode: PermissionMode };
export type PermissionPolicy = { default: PermissionMode; rules: PermissionRule[] };

export type AgentDetail = Agent & {
  version: number;
  model: string;
  instructions: string | null;
  tools: string[];
  permission_policy: PermissionPolicy;
  mcp_servers: Record<string, unknown>;
  vault_ids: string[];
  memory_store_ids: string[];
  skill_ids: string[];
  default_budget_usd: string | number | null;
  max_turns: number | null;
  effort: EffortLevel | null;
  created_by?: string;
  created_at?: string;
};

export type AgentIn = {
  name: string;
  environment_id: string;
  model: string;
  instructions: string | null;
  tools: string[];
  permission_policy: PermissionPolicy;
  mcp_servers: Record<string, unknown>;
  vault_ids: string[];
  memory_store_ids: string[];
  skill_ids: string[];
  default_budget_usd: number | null;
  max_turns: number | null;
  effort: EffortLevel | null;
};

export type Session = {
  id: string;
  agent_id: string;
  title: string | null;
  status: "idle" | "running" | "rescheduling" | "terminated";
  stop_reason: string | null;
  cost_usd: string | number;
  created_by: string | null;
  created_at: string;
};

export type SessionEvent = {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  principal: string | null;
  created_at: string;
};

export type Deployment = {
  id: string;
  name: string;
  agent_id: string;
  agent_version: number | null;
  cron: string;
  timezone: string;
  paused: boolean;
  initial_events: { type: string; content?: { type: string; text?: string }[] }[];
  budget_usd: string | null;
  created_by: string;
  created_at: string;
};

export type DeploymentRun = {
  id: string;
  session_id: string | null;
  status: string;
  error_type: string | null;
  fired_at: string;
};

export type Environment = { id: string; name: string };
export type Vault = { id: string; name: string };
export type Connector = {
  name: string;
  title: string;
  type: "http" | "sse";
  url: string;
  available: boolean;
  requires_vault: boolean;
  credential: string;
  tool_glob: string;
};
export type Credential = {
  id: string;
  name: string;
  type: string;
  target: Record<string, string>;
  created_at: string;
};
export type Artifact = {
  id: string;
  session_id: string;
  agent_id: string;
  name: string;
  description: string | null;
  content_type: string;
  size_bytes: number;
  version: number;
  share_token: string | null;
  updated_at: string;
};

export type MonitoringSummary = {
  window_days: number;
  totals: { cost_usd: number; runs: number; num_turns: number; tool_calls: number };
  all_time: { cost_usd: number };
  cost_by_day: { day: string; cost_usd: number; runs: number }[];
  cost_by_agent: {
    agent_id: string;
    name: string;
    cost_usd: number;
    runs: number;
    sessions: number;
  }[];
  cost_by_model: { model: string; cost_usd: number; runs: number }[];
  sessions_by_status: { status: string; count: number }[];
  tool_usage: { tool_name: string; calls: number; denied: number }[];
  deployment_runs: { status: string; count: number }[];
};

export type MemoryStore = { id: string; name: string; file_count?: number; used_by?: string[] };
export type Memory = {
  id: string;
  path: string;
  size?: number;
  content?: string;
  updated_by?: string | null;
  updated_at?: string;
};
export type Skill = {
  id: string; name: string; description: string | null; ready: boolean; file_count?: number;
};
export type SkillFile = { id: string; path: string; size?: number; content?: string };
export type WorkspaceFile = { path: string; size: number };

export type FavoriteType = "agent" | "session" | "artifact" | "skill";
export type Favorite = { entity_type: FavoriteType; entity_id: string };

export const favKey = (type: FavoriteType, id: string) => `${type}:${id}`;
