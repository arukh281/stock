export const ALGO_SLUGS: Record<string, string> = {
  "44ma": "44ma",
  "44ma_stacked_2ma": "44ma-stacked-2ma",
  financially_free: "financially-free",
  kali: "kali",
};

export async function postAnalyze(slug: string) {
  const res = await fetch(`/api/analyze/${slug}`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Analyze failed");
  return data as { run_id?: string };
}

export async function waitForRun(runId: string) {
  for (;;) {
    const res = await fetch(`/api/runs/id/${runId}`);
    const run = await res.json();
    if (run.status !== "running") return run as {
      status: string;
      error_message?: string | null;
    };
    await new Promise((r) => setTimeout(r, 3000));
  }
}

export async function runEodAnalyze(algoId: string) {
  const slug = ALGO_SLUGS[algoId] ?? algoId;
  const data = await postAnalyze(slug);
  if (data.run_id) {
    const run = await waitForRun(data.run_id);
    if (run.status === "error") {
      throw new Error(run.error_message || "Analyze failed");
    }
  }
}
