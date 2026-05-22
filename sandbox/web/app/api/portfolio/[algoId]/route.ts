import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function GET(
  _req: Request,
  { params }: { params: { algoId: string } }
) {
  try {
    const data = await apiFetch(`/portfolio/${params.algoId}`);
    return NextResponse.json(data);
  } catch (e) {
    const message = e instanceof Error ? e.message : "Failed";
    const status = /unavailable|Cannot reach API/i.test(message) ? 503 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
