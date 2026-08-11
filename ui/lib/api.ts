export async function api<T = unknown>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
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
  return response.json();
}

export type Agent = {
  id: string;
  name: string;
  environment_id: string;
  latest_version: number;
  version?: number;
  disabled: boolean;
  model?: string;
  instructions?: string | null;
};

export const agentName = (agents: Agent[], id: string) =>
  agents.find((a) => a.id === id)?.name ?? id;

export type Session = {
  id: string;
  agent_id: string;
  agent_version: number;
  title: string | null;
  status: "idle" | "running" | "rescheduling" | "terminated";
  stop_reason: string | null;
  cost_usd: string | number;
  budget_usd: string | number | null;
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

export type Vault = { id: string; name: string };
export type Credential = {
  id: string;
  name: string;
  type: string;
  target: Record<string, string>;
  created_at: string;
};
export type MemoryStore = { id: string; name: string };
export type Memory = { id: string; path: string; size?: number; content?: string };
export type WorkspaceFile = { path: string; size: number };
