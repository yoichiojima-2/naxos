"use client";

import { useEffect, useState } from "react";
import {
  agentName,
  api,
  Agent,
  DeploymentRunRow,
  DeploymentRunTotals,
  RunsOverview,
  RunStatus,
  RUN_STATUSES,
} from "@/lib/api";
import { barPath, durationScale, useWidth } from "@/lib/chart";
import { formatDuration, fullTime, relativeTime } from "@/lib/format";
import { RUN_STATUS } from "@/lib/runs";
import LoadingPanel from "@/components/loading-panel";

const RANGES = [7, 30, 90] as const;
const STRIP_LENGTH = 20;

const fmtUsd = (v: number) =>
  v >= 1 ? `$${v.toFixed(2)}` : v > 0 ? `$${v.toFixed(4)}` : "$0";

const isOpen = (run: DeploymentRunRow) => run.finished_at === null;

/** Elapsed for an unfinished run, measured against the server clock. */
function elapsedOf(run: DeploymentRunRow, skewMs: number): number {
  return Math.max(0, (Date.now() - skewMs - Date.parse(run.fired_at)) / 1000);
}

const durationOf = (run: DeploymentRunRow, skewMs: number) =>
  run.duration_seconds ?? elapsedOf(run, skewMs);

export default function DeploymentRuns({
  agents,
  deploymentId,
}: {
  agents: Agent[];
  deploymentId?: string;
}) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [data, setData] = useState<RunsOverview | null>(null);
  const [skewMs, setSkewMs] = useState(0);

  useEffect(() => {
    let live = true;
    const refresh = async () => {
      const params = new URLSearchParams({ days: String(days) });
      if (deploymentId) params.set("deployment_id", deploymentId);
      if (status) params.set("status", status);
      const overview = await api<RunsOverview>(`/v1/deployments/runs?${params}`);
      if (!live) return;
      setData(overview);
      setSkewMs(Date.now() - Date.parse(overview.now));
    };
    refresh();
    const timer = setInterval(refresh, 30_000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [days, deploymentId, status]);

  if (data === null) return <LoadingPanel />;

  const focused = deploymentId
    ? data.deployments.find((d) => d.id === deploymentId)
    : undefined;
  const totals = data.deployments.reduce(
    (sum, d) => ({
      runs: sum.runs + d.runs,
      succeeded: sum.succeeded + d.succeeded,
      failed: sum.failed + d.failed,
      cancelled: sum.cancelled + d.cancelled,
      active: sum.active + d.active,
      finished: sum.finished + d.finished,
      cost_usd: sum.cost_usd + d.cost_usd,
      duration_seconds: sum.duration_seconds + d.duration_seconds,
    }),
    { runs: 0, succeeded: 0, failed: 0, cancelled: 0, active: 0, finished: 0, cost_usd: 0, duration_seconds: 0 },
  );
  const settled = totals.succeeded + totals.failed + totals.cancelled;
  // The status filter narrows the run list, not the totals: a 0%-success reading
  // while filtered to failures would be a lie about the deployment.
  const counts: Record<string, number> = {
    succeeded: totals.succeeded,
    failed: totals.failed,
    cancelled: totals.cancelled,
    running: totals.active,
  };

  return (
    <div className={data.window_days !== days ? "refetching" : ""}>
      <div className="row mb16">
        {RANGES.map((r) => (
          <button key={r} className={`chip ${days === r ? "on" : ""}`} onClick={() => setDays(r)}>
            last {r} days
          </button>
        ))}
        <span className="chip-sep" />
        <button className={`chip ${status === null ? "on" : ""}`} onClick={() => setStatus(null)}>
          all runs
        </button>
        {RUN_STATUSES.filter((s) => s !== "queued").map((s) => (
          <button
            key={s}
            className={`chip ${status === s ? "on" : ""}`}
            onClick={() => setStatus(status === s ? null : s)}
          >
            <span className={`run-dot ${RUN_STATUS[s].kind}`} aria-hidden />
            {s} {counts[s] ?? 0}
          </button>
        ))}
      </div>

      {focused && (
        <div className="row between mb16">
          <div>
            <span className="run-focus-name">{focused.name}</span>
            <span className="muted">
              {" "}<span className="mono">{focused.cron}</span> {focused.timezone} ·{" "}
              {agentName(agents, focused.agent_id)}
            </span>
          </div>
          <a className="back" href="#deployments/runs">← all deployments</a>
        </div>
      )}

      <div className="stat-grid">
        <div className="stat-tile">
          <div className="stat-label">Runs, last {days} days</div>
          <div className="stat-value">{totals.runs.toLocaleString()}</div>
          <div className="stat-sub">
            {totals.active ? `${totals.active} still running` : "none in flight"}
          </div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Success rate</div>
          <div className="stat-value">
            {settled ? `${Math.round((totals.succeeded / settled) * 100)}%` : "—"}
          </div>
          <div className="stat-sub">
            {settled ? `${totals.succeeded} of ${settled} completed` : "no completed runs yet"}
          </div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Average duration</div>
          <div className="stat-value">
            {totals.finished ? formatDuration(totals.duration_seconds / totals.finished) : "—"}
          </div>
          <div className="stat-sub">over {totals.finished} finished runs</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Spend</div>
          <div className="stat-value">{fmtUsd(totals.cost_usd)}</div>
          <div className="stat-sub">across every run in the window</div>
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-title">
          Run duration{status ? ` — ${status} runs only` : ""}
        </div>
        <DurationChart runs={data.runs} skewMs={skewMs} />
        <div className="run-legend">
          {RUN_STATUSES.filter((s) => s !== "queued").map((s) => (
            <span key={s} className="run-legend-item">
              <span className={`run-swatch ${RUN_STATUS[s].kind}`} aria-hidden />
              {RUN_STATUS[s].glyph} {s}
            </span>
          ))}
          <span className="run-legend-item muted">dashed line = average</span>
        </div>
      </div>

      {!deploymentId && (
        <DeploymentHealth deployments={data.deployments} runs={data.runs} agents={agents} />
      )}

      <RunTable runs={data.runs} skewMs={skewMs} showDeployment={!deploymentId} />
    </div>
  );
}

/** Keep the first bar of each day, thinned so at most six labels are drawn. */
function axisLabels(days: string[]): (string | null)[] {
  const firsts = days.map((day, i) => (i === 0 || day !== days[i - 1] ? i : -1)).filter((i) => i >= 0);
  const every = Math.max(1, Math.ceil(firsts.length / 6));
  const keep = new Set(firsts.filter((_, n) => n % every === 0));
  return days.map((day, i) => (keep.has(i) ? day : null));
}

type Tip = { x: number; y: number; run: DeploymentRunRow; duration: number };

function DurationChart({ runs, skewMs }: { runs: DeploymentRunRow[]; skewMs: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);

  const series = [...runs].reverse();
  const height = 200;
  const padLeft = 56;
  const padRight = 10;
  const padTop = 18;
  const padBottom = 24;
  const plotWidth = Math.max(0, width - padLeft - padRight);
  const plotHeight = height - padTop - padBottom;
  const band = series.length ? plotWidth / series.length : 0;
  const barWidth = Math.min(26, Math.max(2, band - 2));
  const durations = series.map((r) => durationOf(r, skewMs));
  const { top, ticks } = durationScale(Math.max(...durations, 0));
  const y = (v: number) => padTop + plotHeight * (1 - v / top);
  const mean = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
  // One label per distinct day, thinned to at most six: repeating "08-11" five
  // times across a run of hourly fires tells the reader nothing.
  const labels = axisLabels(series.map((r) => r.fired_at.slice(5, 10)));

  return (
    <div ref={ref} className="chart-plot">
      {width > 0 && series.length > 0 && (
        <svg width={width} height={height} role="img" aria-label="deployment run duration by run">
          <defs>
            <pattern
              id="run-hatch"
              width="6"
              height="6"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="6" height="6" className="run-hatch-bg" />
              <line x1="0" y1="0" x2="0" y2="6" className="run-hatch-line" />
            </pattern>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={padLeft}
                x2={width - padRight}
                y1={y(t)}
                y2={y(t)}
                className={t === 0 ? "chart-baseline" : "chart-grid"}
              />
              <text x={padLeft - 8} y={y(t) + 3.5} textAnchor="end" className="chart-axis">
                {t === 0 ? "0" : formatDuration(t)}
              </text>
            </g>
          ))}
          {mean > 0 && (
            <line
              x1={padLeft}
              x2={width - padRight}
              y1={y(mean)}
              y2={y(mean)}
              className="run-mean"
            />
          )}
          {series.map((run, i) => {
            const duration = durations[i];
            const x = padLeft + i * band + (band - barWidth) / 2;
            const center = x + barWidth / 2;
            const kind = RUN_STATUS[run.status].kind;
            const hot = tip?.run.id === run.id;
            const show = () => setTip({ x: center, y: y(duration), run, duration });
            return (
              <g key={run.id}>
                {labels[i] && (
                  <text x={center} y={height - 6} textAnchor="middle" className="chart-axis">
                    {labels[i]}
                  </text>
                )}
                <path
                  d={barPath(x, barWidth, y(duration), padTop + plotHeight)}
                  className={`run-bar ${kind} ${hot ? "hot" : ""}`}
                  tabIndex={0}
                  aria-label={
                    `${run.deployment_name} ${run.status} — ` +
                    `${formatDuration(duration)}, started ${fullTime(run.fired_at)}`
                  }
                  onFocus={show}
                  onBlur={() => setTip(null)}
                />
                <rect
                  x={padLeft + i * band}
                  y={padTop}
                  width={band}
                  height={plotHeight}
                  fill="transparent"
                  onPointerMove={show}
                  onPointerLeave={() => setTip(null)}
                />
              </g>
            );
          })}
        </svg>
      )}
      {series.length === 0 && (
        <span className="muted chart-empty">no runs in this window yet</span>
      )}
      {tip && (
        <div className="chart-tip" style={{ left: tip.x, top: Math.max(0, tip.y - 8) }}>
          <strong>{formatDuration(tip.duration)}</strong>{" "}
          <span className={`badge ${RUN_STATUS[tip.run.status].badge}`}>
            {RUN_STATUS[tip.run.status].glyph} {tip.run.status}
          </span>
          <div className="muted">{tip.run.deployment_name}</div>
          <div className="muted">{fullTime(tip.run.fired_at)}</div>
          {isOpen(tip.run) && <div className="muted">still running</div>}
        </div>
      )}
    </div>
  );
}

function DeploymentHealth({
  deployments,
  runs,
  agents,
}: {
  deployments: DeploymentRunTotals[];
  runs: DeploymentRunRow[];
  agents: Agent[];
}) {
  if (!deployments.length) {
    return (
      <div className="panel">
        <span className="muted">no deployments have run in this window.</span>
      </div>
    );
  }
  return (
    <div className="run-cards">
      {deployments.map((d) => {
        const strip = runs
          .filter((r) => r.deployment_id === d.id)
          .slice(0, STRIP_LENGTH)
          .reverse();
        const settled = d.succeeded + d.failed + d.cancelled;
        return (
          <a className="panel run-card" key={d.id} href={`#deployments/runs/${d.id}`}>
            <div className="row between">
              <span className="run-card-name">{d.name}</span>
              {d.archived ? (
                <span className="badge idle">archived</span>
              ) : d.paused ? (
                <span className="badge idle">paused</span>
              ) : (
                <span className="badge running">active</span>
              )}
            </div>
            <div className="muted run-card-sub">
              <span className="mono">{d.cron}</span> · {agentName(agents, d.agent_id)}
            </div>
            <div className="run-strip" aria-hidden>
              {strip.map((r) => (
                <span
                  key={r.id}
                  className={`run-cell ${RUN_STATUS[r.status].kind}`}
                  title={`${r.status} — ${fullTime(r.fired_at)}`}
                />
              ))}
              {strip.length === 0 && <span className="muted">no runs</span>}
            </div>
            <div className="run-card-stats">
              <span>
                <strong>{settled ? `${Math.round((d.succeeded / settled) * 100)}%` : "—"}</strong>{" "}
                success
              </span>
              <span>
                <strong>
                  {d.finished ? formatDuration(d.duration_seconds / d.finished) : "—"}
                </strong>{" "}
                avg
              </span>
              <span>
                <strong>{fmtUsd(d.cost_usd)}</strong> spend
              </span>
              <span className="muted">
                {d.last_fired_at ? relativeTime(d.last_fired_at) : "never run"}
              </span>
            </div>
          </a>
        );
      })}
    </div>
  );
}

function RunTable({
  runs,
  skewMs,
  showDeployment,
}: {
  runs: DeploymentRunRow[];
  skewMs: number;
  showDeployment: boolean;
}) {
  return (
    <div className="panel flush">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>status</th>
              {showDeployment && <th>deployment</th>}
              <th>started</th>
              <th>queued</th>
              <th>duration</th>
              <th>turns</th>
              <th>cost</th>
              <th>session</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td className="empty" colSpan={showDeployment ? 8 : 7}>
                  no runs match the current filter.
                </td>
              </tr>
            )}
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  <span className={`badge ${RUN_STATUS[run.status].badge}`} title={run.error_message ?? ""}>
                    {RUN_STATUS[run.status].glyph} {run.status}
                  </span>
                  {run.error_type && <span className="muted"> {run.error_type}</span>}
                  {run.stop_reason === "requires_action" && !run.finished_at && (
                    <span className="muted"> awaiting approval</span>
                  )}
                </td>
                {showDeployment && <td>{run.deployment_name}</td>}
                <td title={fullTime(run.fired_at)}>{relativeTime(run.fired_at)}</td>
                <td className="mono">{formatDuration(run.queued_seconds)}</td>
                <td className="mono">
                  {formatDuration(durationOf(run, skewMs))}
                  {isOpen(run) && <span className="muted"> so far</span>}
                </td>
                <td className="mono">{run.num_turns}</td>
                <td className="mono">{fmtUsd(run.cost_usd)}</td>
                <td>
                  {run.session_id ? (
                    <a className="mono" href={`#sessions/${run.session_id}`}>
                      open
                    </a>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
