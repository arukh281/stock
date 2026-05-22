import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

export async function GET(
  _req: Request,
  { params }: { params: { runId: string } }
) {
  try {
    const data = await apiFetch(`/runs/id/${params.runId}`);
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
