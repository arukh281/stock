"use client";

import { useCallback, useState } from "react";
import { AlgoCard } from "@/components/AlgoCard";
import { ALGOS } from "@/lib/api";
import { runEodAnalyze } from "@/lib/analyze-client";

export function SandboxDashboard() {
  const [batchLocked, setBatchLocked] = useState(false);
  const [batchCurrentId, setBatchCurrentId] = useState<string | null>(null);
  const [batchStep, setBatchStep] = useState(0);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [refreshTokens, setRefreshTokens] = useState<Record<string, number>>({});

  const bumpRefresh = useCallback((algoId: string) => {
    setRefreshTokens((t) => ({ ...t, [algoId]: (t[algoId] ?? 0) + 1 }));
  }, []);

  async function onRunAll() {
    setBatchLocked(true);
    setBatchError(null);
    setBatchStep(0);
    setBatchCurrentId(null);

    for (let i = 0; i < ALGOS.length; i++) {
      const algo = ALGOS[i];
      setBatchStep(i + 1);
      setBatchCurrentId(algo.id);
      try {
        await runEodAnalyze(algo.id);
        bumpRefresh(algo.id);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Analyze failed";
        setBatchError(`${algo.label}: ${msg}`);
        setBatchLocked(false);
        setBatchCurrentId(null);
        return;
      }
    }

    setBatchLocked(false);
    setBatchCurrentId(null);
    setBatchStep(0);
  }

  const currentLabel = ALGOS.find((a) => a.id === batchCurrentId)?.label;

  return (
    <div className="page">
      <header className="page-toolbar">
        <div>
          <h1>Paper sandbox</h1>
          <p>Four strategies, four ledgers. EOD analyze after NSE cash close.</p>
          {batchError ? <p className="err-msg">{batchError}</p> : null}
        </div>
        <div className="toolbar-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={batchLocked}
            onClick={onRunAll}
          >
            {batchLocked
              ? `${batchStep}/${ALGOS.length}: ${currentLabel ?? "…"}`
              : "Run all EOD"}
          </button>
        </div>
      </header>

      <div className="card-grid">
        {ALGOS.map((a) => (
          <AlgoCard
            key={a.id}
            id={a.id}
            label={a.label}
            description={a.description}
            batchLocked={batchLocked}
            batchRunningThis={batchCurrentId === a.id}
            refreshToken={refreshTokens[a.id] ?? 0}
          />
        ))}
      </div>
    </div>
  );
}
