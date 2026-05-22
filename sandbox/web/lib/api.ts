const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.ANALYZE_API_KEY || "";

export async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    const err = body as { detail?: string };
    throw new Error(err?.detail || res.statusText || `HTTP ${res.status}`);
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
