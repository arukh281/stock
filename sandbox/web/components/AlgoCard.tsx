"use client";

import { useCallback, useEffect, useState } from "react";
import { runEodAnalyze } from "@/lib/analyze-client";

type Portfolio = {
  cash?: number;
  equity?: number;
  in_market?: number;
  total_value?: number;
  starting_capital?: number;
  pnl?: {
    total?: number;
    total_pct?: number;
    realized?: number;
    unrealized?: number;
  };
  positions?: Array<{
    symbol: string;
    qty: number;
    entry_px: number;
    stop_px?: number;
    extra?: { unrealized_pnl?: number; last_close?: number };
  }>;
  pending_recent?: Array<{
    id?: number;
    symbol: string;
    status: string;
    signal_ts?: string;
    trigger_px?: number | null;
    stop_px?: number | null;
    target_px?: number | null;
    deadline_ts?: string | null;
    qty?: number | null;
    fill_model?: string | null;
  }>;
  journal_recent?: Array<{ ts: string; kind: string; message: string; symbol?: string }>;
  runs_recent?: Array<{
    id: string;
    status: string;
    started_at: string;
    finished_at?: string;
    error_message?: string | null;
  }>;
};

function fmtInr(n: number) {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function fmtPnl(n: number) {
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  const abs = Math.abs(n);
  return `${sign}₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function pnlClass(n: number) {
  if (n > 0) return "pnl-pos";
  if (n < 0) return "pnl-neg";
  return "";
}

function fmtPx(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function orderLabel(p: { fill_model?: string | null }) {
  if (p.fill_model?.includes("breakout")) return "breakout";
  if (p.fill_model?.includes("next_open") || p.fill_model?.includes("amo"))
    return "next open";
  return "pending";
}

function fmtRunTime(iso: string) {
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusClass(status: string) {
  if (status === "ok") return "ok";
  if (status === "error") return "error";
  return "run";
}

function Stat({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${valueClass ?? ""}`}>{value}</span>
      {sub ? <span className={`stat-sub ${valueClass ?? ""}`}>{sub}</span> : null}
    </div>
  );
}

export function AlgoCard({
  id,
  label,
  description,
  batchLocked = false,
  batchRunningThis = false,
  refreshToken = 0,
  loadDelayMs = 0,
}: {
  id: string;
  label: string;
  description?: string;
  batchLocked?: boolean;
  batchRunningThis?: boolean;
  refreshToken?: number;
  /** Stagger initial portfolio fetch to avoid Supabase 429 on page load */
  loadDelayMs?: number;
}) {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/portfolio/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Load failed");
      setPortfolio(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      load();
    }, loadDelayMs);
    return () => window.clearTimeout(t);
  }, [load, refreshToken, loadDelayMs]);

  const onCancelPending = async (symbol: string) => {
    setCancelling(symbol);
    setError(null);
    try {
      const res = await fetch(
        `/api/portfolio/${id}/pending/${encodeURIComponent(symbol)}/cancel`,
        { method: "POST" }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Cancel failed");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancelling(null);
    }
  };

  const lastRun = portfolio?.runs_recent?.[0];
  const openOrders =
    portfolio?.pending_recent?.filter((p) => p.status === "open") ?? [];
  const positions = portfolio?.positions ?? [];
  const journal = portfolio?.journal_recent ?? [];
  const isRunning =
    analyzing || batchRunningThis || lastRun?.status === "running";

  async function onAnalyze(force = false) {
    if (batchLocked) return;
    setAnalyzing(true);
    setError(null);
    try {
      await runEodAnalyze(id, force);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analyze failed");
    } finally {
      setAnalyzing(false);
    }
  }

  const runErr =
    error ?? (lastRun?.status === "error" ? lastRun.error_message : null);
  const totalPct = portfolio?.pnl?.total_pct ?? 0;

  return (
    <article className="card" data-algo={id}>
      <header className="card-head">
        <div className="card-head-main">
          <h2>{label}</h2>
          <code className="card-id">{id}</code>
          {description ? <p className="card-desc">{description}</p> : null}
        </div>
        {lastRun ? (
          <div className="card-run">
            <span className={`status-tag ${statusClass(lastRun.status)}`}>
              {lastRun.status}
            </span>
            <time dateTime={lastRun.finished_at ?? lastRun.started_at}>
              {fmtRunTime(lastRun.finished_at ?? lastRun.started_at)}
            </time>
          </div>
        ) : null}
      </header>

      <div className="card-body">
        {loading ? (
          <p className="loading-line">Loading…</p>
        ) : (
          <div className="stats-panel">
            <div className="stats-row">
              <Stat label="Cash" value={fmtInr(portfolio?.cash ?? 0)} />
              <Stat label="In market" value={fmtInr(portfolio?.in_market ?? 0)} />
              <Stat
                label="Total"
                value={fmtInr(portfolio?.total_value ?? portfolio?.equity ?? 0)}
              />
              <Stat
                label="Starting"
                value={fmtInr(portfolio?.starting_capital ?? 0)}
              />
            </div>
            <div className="stats-row stats-row-pnl">
              <Stat
                label="P&L"
                value={fmtPnl(portfolio?.pnl?.total ?? 0)}
                valueClass={pnlClass(portfolio?.pnl?.total ?? 0)}
                sub={`${totalPct > 0 ? "+" : ""}${totalPct.toFixed(2)}%`}
              />
              <Stat
                label="Realized"
                value={fmtPnl(portfolio?.pnl?.realized ?? 0)}
                valueClass={pnlClass(portfolio?.pnl?.realized ?? 0)}
              />
              <Stat
                label="Unrealized"
                value={fmtPnl(portfolio?.pnl?.unrealized ?? 0)}
                valueClass={pnlClass(portfolio?.pnl?.unrealized ?? 0)}
              />
            </div>
          </div>
        )}

        {runErr ? <p className="err-msg">{runErr}</p> : null}

        <div className="btn-row">
          <button
            type="button"
            className="btn btn-primary btn-block"
            disabled={isRunning || loading || batchLocked}
            onClick={() => onAnalyze(false)}
          >
            {batchRunningThis || (analyzing && !batchLocked)
              ? "Running EOD…"
              : "Analyze EOD"}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            title="Re-run even if today's snapshot exists (recovery)"
            disabled={isRunning || loading || batchLocked}
            onClick={() => onAnalyze(true)}
          >
            Force
          </button>
        </div>

        <div className="card-sections">
          <section className="card-section">
            <h3>
              Open orders <span>({openOrders.length})</span>
            </h3>
            {openOrders.length > 0 ? (
              <>
                <p className="section-note">
                  Fills checked on next analyze against that session&apos;s
                  candle.
                </p>
                <div className="table-panel">
                  <table className="data-table wide">
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Type</th>
                        <th className="num">Trigger</th>
                        <th className="num">Stop</th>
                        <th className="num">Target</th>
                        <th className="num">Qty</th>
                        <th>Signal</th>
                        <th>Until</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {openOrders.map((p) => (
                        <tr key={`${p.id ?? p.symbol}-${p.signal_ts}`}>
                          <td className="sym">{p.symbol}</td>
                          <td>{orderLabel(p)}</td>
                          <td className="num">{fmtPx(p.trigger_px)}</td>
                          <td className="num">{fmtPx(p.stop_px)}</td>
                          <td className="num">{fmtPx(p.target_px)}</td>
                          <td className="num">
                            {p.qty != null && p.qty > 0
                              ? p.qty >= 1
                                ? Math.round(p.qty)
                                : p.qty.toFixed(3)
                              : "—"}
                          </td>
                          <td>
                            {p.signal_ts ? p.signal_ts.slice(0, 10) : "—"}
                          </td>
                          <td>
                            {p.deadline_ts
                              ? String(p.deadline_ts).slice(0, 10)
                              : "next"}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={
                                cancelling === p.symbol ||
                                analyzing ||
                                batchLocked
                              }
                              onClick={() => onCancelPending(p.symbol)}
                            >
                              {cancelling === p.symbol ? "…" : "Cancel"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <p className="section-empty">No open orders</p>
            )}
          </section>

          <section className="card-section">
            <h3>
              Positions <span>({positions.length})</span>
            </h3>
            {positions.length > 0 ? (
              <div className="table-panel">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th className="num">Qty</th>
                      <th className="num">Entry</th>
                      <th className="num">Stop</th>
                      <th className="num">Unrealized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p) => {
                      const u = p.extra?.unrealized_pnl;
                      return (
                        <tr key={p.symbol}>
                          <td className="sym">{p.symbol}</td>
                          <td className="num">{p.qty}</td>
                          <td className="num">{fmtPx(p.entry_px)}</td>
                          <td className="num">{fmtPx(p.stop_px)}</td>
                          <td className={`num ${u != null ? pnlClass(u) : ""}`}>
                            {u != null ? fmtPnl(u) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="section-empty">No positions</p>
            )}
          </section>

          <section className="card-section">
            <h3>
              Activity <span>({journal.length})</span>
            </h3>
            {journal.length > 0 ? (
              <div className="log-panel">
                <ul className="log">
                  {journal.slice(0, 8).map((j, i) => (
                    <li key={i}>
                      <span className="kind">{j.kind}</span>
                      {j.symbol ? (
                        <>
                          <span className="sym">{j.symbol}</span>{" "}
                        </>
                      ) : null}
                      {j.message}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="section-empty">No recent activity</p>
            )}
          </section>
        </div>
      </div>
    </article>
  );
}
