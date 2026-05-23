const API_URL =
  process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.ANALYZE_API_KEY || "";

const COLD_START_RETRY_MS = 20_000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Turn Render 502 HTML / gateway errors into a short message for the UI. */
export function formatApiError(status: number, detail: string): string {
  const trimmed = detail.trim();
  if (
    trimmed.startsWith("<!DOCTYPE") ||
    trimmed.startsWith("<html") ||
    /bad gateway/i.test(trimmed)
  ) {
    return `API unavailable (HTTP ${status}). On Render free tier the API may be sleeping or crashed—open paper-sandbox-api → Logs, confirm /health works, then retry in ~30s.`;
  }
  if (/too many requests|rate limit|429/i.test(trimmed)) {
    return (
      "Yahoo Finance or Supabase rate limit (429). Wait a few minutes, then use " +
      "Analyze EOD again — cron runs algos sequentially with cooldown. " +
      "Avoid Run all + manual analyze at the same time."
    );
  }
  if (trimmed.length > 280) {
    return `${trimmed.slice(0, 280)}…`;
  }
  return trimmed || `HTTP ${status}`;
}

async function fetchApi(path: string, init?: RequestInit): Promise<Response> {
  const url = `${API_URL}${path}`;
  let last: Response | null = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(url, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": API_KEY,
          ...(init?.headers || {}),
        },
        cache: "no-store",
      });
      last = res;
      if (attempt === 0 && (res.status === 502 || res.status === 503)) {
        await sleep(COLD_START_RETRY_MS);
        continue;
      }
      return res;
    } catch (err) {
      if (attempt === 0) {
        await sleep(COLD_START_RETRY_MS);
        continue;
      }
      const msg = err instanceof Error ? err.message : "fetch failed";
      throw new Error(
        `Cannot reach API at ${API_URL}. Check API_URL and that paper-sandbox-api is running. (${msg})`
      );
    }
  }
  return last!;
}

export async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetchApi(path, init);
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    const err = body as { detail?: string };
    const detail =
      typeof err?.detail === "string"
        ? err.detail
        : res.statusText || `HTTP ${res.status}`;
    throw new Error(formatApiError(res.status, detail));
  }
  return body;
}

export const ALGOS = [
  {
    id: "44ma",
    label: "44 MA Full Ladder",
    description: "Anti-V trend · path floor + 3-segment ladder + close buffer",
    analyzePath: "/analyze/44ma",
  },
  {
    id: "44ma_stacked_2ma",
    label: "44 MA Stacked 2MA",
    description: "MA1 > MA2@44d · RF-tuned runner-up variant",
    analyzePath: "/analyze/44ma-stacked-2ma",
  },
  {
    id: "financially_free",
    label: "Financially Free",
    description: "Midcap 150 VCP · shared cash pool",
    analyzePath: "/analyze/financially-free",
  },
  {
    id: "kali",
    label: "KALI",
    description: "PIT universe · AMO reconcile",
    analyzePath: "/analyze/kali",
  },
] as const;
