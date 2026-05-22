import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function POST(
  _req: Request,
  { params }: { params: { algoId: string; symbol: string } }
) {
  try {
    const sym = encodeURIComponent(params.symbol);
    const data = await apiFetch(
      `/portfolio/${params.algoId}/pending/${sym}/cancel`,
      { method: "POST" }
    );
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Cancel failed" },
      { status: 500 }
    );
  }
}
