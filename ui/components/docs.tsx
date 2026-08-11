const CREATE_AGENT_EXAMPLE = `curl -X POST https://<naxos-host>/v1/agents \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "release-notes-writer",
    "environment_id": "env-example",
    "model": "claude-sonnet-5",
    "instructions": "Summarize merged PRs into release notes.",
    "tools": ["Read", "Grep", "Bash"],
    "permission_policy": {
      "default": "always_ask",
      "rules": [{ "tool": "Read", "mode": "always_allow" }]
    }
  }'`;

export default function Docs() {
  return (
    <div className="docs">
      <div className="panel">
        <strong>How naxos works</strong>
        <p>
          naxos runs Claude agents inside this Google Cloud project — model calls go
          through Vertex AI and data never leaves the org boundary. Agents are{" "}
          <em>always available rather than always running</em>: a session&apos;s sandbox
          container exists only while it is actively processing. When the agent goes
          idle the session checkpoints and releases its container; your next message
          wakes it back up.
        </p>
        <dl className="kv mt12">
          <dt>Environment</dt>
          <dd>
            The tenant and isolation boundary: its own service account, sandbox job,
            and storage bucket. Provisioned by an administrator with Terraform — not
            through this UI.
          </dd>
          <dt>Agent</dt>
          <dd>
            A versioned configuration: instructions, model, tools, permission policy.
            Every update creates a new immutable version; running sessions keep the
            version they started with.
          </dd>
          <dt>Session</dt>
          <dd>
            One durable conversation with an agent. Its full history is an append-only
            event log you can replay or stream live.
          </dd>
          <dt>Deployment</dt>
          <dd>
            A cron schedule that wakes an agent with a fixed prompt — unattended runs
            whose results land as ordinary sessions.
          </dd>
          <dt>Vault</dt>
          <dd>
            Credentials for external services. Secret values are injected into
            outbound requests by the egress proxy and never enter the agent&apos;s
            sandbox.
          </dd>
          <dt>Memory</dt>
          <dd>Files an agent keeps across sessions, browsable and editable here.</dd>
          <dt>Skill</dt>
          <dd>
            Reusable know-how shared across the organization — a folder of
            instructions (SKILL.md plus supporting files) any agent can be given.
            Agents load skills read-only; they are edited only here.
          </dd>
        </dl>
      </div>

      <div className="panel">
        <strong>1 · Get an environment</strong>
        <p>
          Everything runs inside an environment. Environments are provisioned by
          Terraform (<code>terraform/environments.json</code>), so if none of your
          agents list one you expect, ask a platform administrator to add it — the API
          only registers environments that already exist in infrastructure.
        </p>
      </div>

      <div className="panel">
        <strong>2 · Create an agent</strong>
        <p>
          Create an agent on the <a href="#agents">Agents</a> page, or through the
          REST API. The API sits behind IAP, so requests are made as you:
        </p>
        <pre>{CREATE_AGENT_EXAMPLE}</pre>
        <p>
          The permission policy decides what happens at each tool call:{" "}
          <code>always_allow</code> runs it and records it,{" "}
          <code>always_ask</code> pauses the session until a human approves, and any
          tool not in <code>tools</code> simply does not exist for the agent. Posting
          the same body to <code>/v1/agents/&#123;id&#125;/versions</code> publishes a
          new version.
        </p>
      </div>

      <div className="panel">
        <strong>3 · Start a session</strong>
        <p>
          On the <a href="#sessions">Sessions</a> page, pick an agent and press{" "}
          <em>New session</em>, then send a message. The status badge tells you what
          the session is doing:
        </p>
        <ul>
          <li>
            <span className="badge running">running</span> — the sandbox is live and
            the agent is working.
          </li>
          <li>
            <span className="badge idle">idle</span> — the agent finished its turn and
            released its container. Sending another message resumes it.
          </li>
          <li>
            <span className="badge rescheduling">rescheduling</span> — a new event
            arrived and the session is waking up in a fresh container.
          </li>
          <li>
            <span className="badge requires_action">needs approval</span> — the agent
            wants to run a tool its policy gates. Open the session and press{" "}
            <em>Allow</em> or <em>Deny</em>; it waits (at zero compute cost) until you
            decide.
          </li>
        </ul>
        <p>
          Inside a session you can also interrupt a running turn and browse the files
          the agent produced in its workspace.
        </p>
      </div>

      <div className="panel">
        <strong>4 · Go further</strong>
        <p>
          <a href="#deployments">Deployments</a> put an agent on a cron schedule for
          unattended runs. <a href="#vaults">Vaults</a> hold credentials for external
          services — values are write-only and injected at the egress proxy, so agents
          can use a credential without ever seeing it.{" "}
          <a href="#memory">Memory</a> is what an agent carries between sessions.{" "}
          <a href="#skills">Skills</a> package shared procedures once and attach them
          to any agent.
        </p>
      </div>

      <div className="panel">
        <strong>Guardrails</strong>
        <p>
          Every tool call and every run is written to an audit log with who triggered
          it, what was called, and what was decided. Each session has a hard budget
          cap — hitting it pauses the session until the budget is raised. And any
          agent can be disabled instantly: new events are rejected and in-flight tool
          calls are stopped, which also pauses its deployments.
        </p>
      </div>
    </div>
  );
}
