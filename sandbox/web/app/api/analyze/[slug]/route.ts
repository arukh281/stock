import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

const SLUG_TO_PATH: Record<string, string> = {
  "44ma": "/analyze/44ma",
  "44ma-stacked-2ma": "/analyze/44ma-stacked-2ma",
  "financially-free": "/analyze/financially-free",
  kali: "/analyze/kali",
};

export async function POST(
  _req: Request,
  { params }: { params: { slug: string } }
) {
  const path = SLUG_TO_PATH[params.slug];
  if (!path) {
    return NextResponse.json({ error: "Unknown algo" }, { status: 404 });
  }
  try {
    const data = await apiFetch(path, { method: "POST" });
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
