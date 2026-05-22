import type { LiveCall } from "./types";

// Legacy stub kept for any import that still references it; the live wall now
// uses `wallClient.ts` (WS or REST polling). Future Wall list views can call
// the Wall API directly via `VITE_WALL_API_URL`.
export async function fetchCalls(apiUrl?: string): Promise<LiveCall[]> {
  if (!apiUrl) return [];
  try {
    const res = await fetch(apiUrl);
    if (!res.ok) return [];
    const data = (await res.json()) as { calls?: LiveCall[] };
    return data.calls ?? [];
  } catch {
    return [];
  }
}
