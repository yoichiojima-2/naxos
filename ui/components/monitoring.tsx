"use client";

import { useEffect, useState } from "react";
import { api, MonitoringSummary } from "@/lib/api";
import { barPath, niceScale, useWidth } from "@/lib/chart";

const RANGES = [7, 30, 90] as const;

const NO_RUNS = "no runs in this window yet";
const NO_TOOL_CALLS = "no tool calls in this window yet";

const fmtUsd = (v: number) =>
  v >= 100
    ? `$${Math.round(v).toLocaleString()}`
    : v >= 1
      ? `$${v.toFixed(2)}`
      : v > 0
        ? `$${v.toFixed(4)}`
        : "$0";

const fmtInt = (v: number) => v.toLocaleString();

export default function Monitoring() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [data, setData] = useState<MonitoringSummary | null>(null);
  const [asTable, setAsTable] = useState(false);

  useEffect(() => {
    let live = true;
    const refresh = async () => {
      const summary = await api<MonitoringSummary>(`/v1/monitoring/summary?days=${days}`);
      if (live) setData(summary);
    };
    refresh();
    const timer = setInterval(refresh, 30_000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [days]);

  if (data === null) return <span className="muted">loading…</span>;

  const { totals, all_time } = data;
  const deployTotal = data.deployment_runs.reduce((sum, r) => sum + r.count, 0);
  const deployFailed = data.deployment_runs.find((r) => r.status === "failed")?.count ?? 0;

  return (
    <div className={data.window_days !== days ? "refetching" : ""}>
      <div className="row mb16">
        {RANGES.map((r) => (
          <button
            key={r}
            className={`chip ${days === r ? "on" : ""}`}
            onClick={() => setDays(r)}
          >
            last {r} days
          </button>
        ))}
        <button className={`chip ${asTable ? "on" : ""}`} onClick={() => setAsTable(!asTable)}>
          table view
        </button>
      </div>

      <div className="stat-grid">
        <div className="stat-tile">
          <div className="stat-label">Spend, last {days} days</div>
          <div className="stat-value">{fmtUsd(totals.cost_usd)}</div>
          <div className="stat-sub">{fmtUsd(all_time.cost_usd)} all time</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Runs</div>
          <div className="stat-value">{fmtInt(totals.runs)}</div>
          <div className="stat-sub">{fmtInt(totals.num_turns)} agent turns</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Tool calls</div>
          <div className="stat-value">{fmtInt(totals.tool_calls)}</div>
          <div className="stat-sub">across all sessions</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Deployment runs</div>
          <div className="stat-value">{fmtInt(deployTotal)}</div>
          <div className="stat-sub">
            {deployTotal ? `${fmtInt(deployFailed)} failed` : "none in this window"}
          </div>
        </div>
      </div>

      <div className="panel chart-card">
        <div className="chart-title">Daily spend (USD, UTC days)</div>
        {asTable ? (
          <DataTable
            head={["day", "spend", "runs"]}
            rows={[...data.cost_by_day].reverse().map((d) => [
              d.day,
              fmtUsd(d.cost_usd),
              fmtInt(d.runs),
            ])}
            empty={NO_RUNS}
          />
        ) : (
          <DailySpend data={data.cost_by_day} days={days} />
        )}
      </div>

      <div className="grid2">
        <Breakdown
          title="Spend by agent"
          asTable={asTable}
          head={["agent", "spend", "runs", "sessions"]}
          rows={data.cost_by_agent.map((a) => [
            a.name,
            fmtUsd(a.cost_usd),
            fmtInt(a.runs),
            fmtInt(a.sessions),
          ])}
          bars={data.cost_by_agent.map((a) => ({
            key: a.agent_id,
            name: a.name,
            value: a.cost_usd,
            label: fmtUsd(a.cost_usd),
          }))}
          empty={NO_RUNS}
        />
        <Breakdown
          title="Tool calls by tool"
          asTable={asTable}
          head={["tool", "calls", "denied"]}
          rows={data.tool_usage.map((t) => [t.tool_name, fmtInt(t.calls), fmtInt(t.denied)])}
          bars={data.tool_usage.map((t) => ({
            key: t.tool_name,
            name: t.tool_name,
            value: t.calls,
            label: fmtInt(t.calls) + (t.denied ? ` (${fmtInt(t.denied)} denied)` : ""),
          }))}
          empty={NO_TOOL_CALLS}
        />
      </div>

      <div className="grid2">
        <Breakdown
          title="Spend by model"
          asTable={asTable}
          head={["model", "spend", "runs"]}
          rows={data.cost_by_model.map((m) => [m.model, fmtUsd(m.cost_usd), fmtInt(m.runs)])}
          bars={data.cost_by_model.map((m) => ({
            key: m.model,
            name: m.model,
            value: m.cost_usd,
            label: fmtUsd(m.cost_usd),
          }))}
          empty={NO_RUNS}
        />
        <div className="panel chart-card">
          <div className="chart-title">Sessions by status (all time)</div>
          <div className="row mb12">
            {data.sessions_by_status.length === 0 && <span className="muted">no sessions yet</span>}
            {data.sessions_by_status.map((s) => (
              <span key={s.status} className="chip">
                <span className={`badge ${s.status}`}>{s.status}</span>
                {fmtInt(s.count)}
              </span>
            ))}
          </div>
          <div className="chart-title">Deployment runs, last {days} days</div>
          <div className="row">
            {data.deployment_runs.length === 0 && (
              <span className="muted">no deployment runs in this window</span>
            )}
            {data.deployment_runs.map((r) => (
              <span key={r.status} className="chip">
                <span
                  className={`badge ${
                    r.status === "failed"
                      ? "terminated"
                      : r.status === "succeeded"
                        ? "running"
                        : "idle"
                  }`}
                >
                  {r.status === "failed" ? "✕ " : r.status === "succeeded" ? "✓ " : ""}
                  {r.status}
                </span>
                {fmtInt(r.count)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Breakdown({
  title,
  asTable,
  head,
  rows,
  bars,
  empty,
}: {
  title: string;
  asTable: boolean;
  head: string[];
  rows: (string | number)[][];
  bars: { key: string; name: string; value: number; label: string }[];
  empty: string;
}) {
  return (
    <div className="panel chart-card">
      <div className="chart-title">{title}</div>
      {asTable ? (
        <DataTable head={head} rows={rows} empty={empty} />
      ) : (
        <HBars rows={bars} empty={empty} />
      )}
    </div>
  );
}

function DataTable({
  head,
  rows,
  empty,
}: {
  head: string[];
  rows: (string | number)[][];
  empty: string;
}) {
  if (!rows.length) return <span className="muted">{empty}</span>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{head.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((cell, j) => (
                <td key={j} className={j === 0 ? "" : "mono"}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Tip = { x: number; y: number; day: string; cost: number; runs: number };

function DailySpend({
  data,
  days,
}: {
  data: MonitoringSummary["cost_by_day"];
  days: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);

  const byDay = new Map(data.map((d) => [d.day, d]));
  const series = Array.from({ length: days }, (_, i) => {
    const day = new Date(Date.now() - (days - 1 - i) * 86_400_000).toISOString().slice(0, 10);
    return { day, cost_usd: 0, runs: 0, ...byDay.get(day) };
  });

  const height = 190;
  const padLeft = 48;
  const padRight = 8;
  const padTop = 18;
  const padBottom = 22;
  const plotWidth = Math.max(0, width - padLeft - padRight);
  const plotHeight = height - padTop - padBottom;
  const band = series.length ? plotWidth / series.length : 0;
  const barWidth = Math.min(24, Math.max(2, band - 2));
  const maxValue = Math.max(...series.map((s) => s.cost_usd), 0);
  const { top, ticks } = niceScale(maxValue);
  const y = (v: number) => padTop + plotHeight * (1 - v / top);
  const maxIndex = maxValue > 0 ? series.findIndex((s) => s.cost_usd === maxValue) : -1;
  const labelEvery = Math.ceil(series.length / 6);

  const bar = (x: number, value: number) => barPath(x, barWidth, y(value), padTop + plotHeight);

  const show = (s: (typeof series)[number], center: number) =>
    setTip({ x: center, y: y(s.cost_usd), day: s.day, cost: s.cost_usd, runs: s.runs });

  return (
    <div ref={ref} className="chart-plot">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="daily spend column chart">
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
                {fmtUsd(t)}
              </text>
            </g>
          ))}
          {series.map((s, i) => {
            const x = padLeft + i * band + (band - barWidth) / 2;
            const center = x + barWidth / 2;
            return (
              <g key={s.day}>
                {i % labelEvery === 0 && (
                  <text
                    x={center}
                    y={height - 6}
                    textAnchor="middle"
                    className="chart-axis"
                  >
                    {s.day.slice(5)}
                  </text>
                )}
                {s.cost_usd > 0 && (
                  <path
                    d={bar(x, s.cost_usd)}
                    className={`chart-bar ${tip?.day === s.day ? "hot" : ""}`}
                    tabIndex={0}
                    aria-label={`${s.day}: ${fmtUsd(s.cost_usd)}, ${s.runs} runs`}
                    onFocus={() => show(s, center)}
                    onBlur={() => setTip(null)}
                  />
                )}
                <rect
                  x={padLeft + i * band}
                  y={padTop}
                  width={band}
                  height={plotHeight}
                  fill="transparent"
                  onPointerMove={() => show(s, center)}
                  onPointerLeave={() => setTip(null)}
                />
                {i === maxIndex && (
                  <text x={center} y={y(s.cost_usd) - 5} textAnchor="middle" className="chart-label">
                    {fmtUsd(s.cost_usd)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
      {maxValue === 0 && (
        <span className="muted chart-empty">no spend recorded in this window yet</span>
      )}
      {tip && (
        <div
          className="chart-tip"
          style={{ left: tip.x, top: Math.max(0, tip.y - 8) }}
        >
          <strong>{fmtUsd(tip.cost)}</strong> · {tip.runs} {tip.runs === 1 ? "run" : "runs"}
          <div className="muted">{tip.day}</div>
        </div>
      )}
    </div>
  );
}

function HBars({
  rows,
  empty,
}: {
  rows: { key: string; name: string; value: number; label: string }[];
  empty: string;
}) {
  if (!rows.length) return <span className="muted">{empty}</span>;
  const max = Math.max(...rows.map((r) => r.value), 0) || 1;
  return (
    <div>
      {rows.map((r) => (
        <div className="hbar-row" key={r.key}>
          <span className="hbar-name" title={r.name}>{r.name}</span>
          <span className="hbar-track">
            <span className="hbar-fill" style={{ width: `${(r.value / max) * 72}%` }} />
            <span className="hbar-val">{r.label}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
