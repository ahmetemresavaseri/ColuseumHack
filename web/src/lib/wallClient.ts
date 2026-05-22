import type { WallEvent } from "./types";

export type WallConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";

export type WallClientHandle = {
  close: () => void;
  status: () => WallConnectionStatus;
};

export type WallClientOptions = {
  url?: string;
  pollUrl?: string;
  companyId?: string;
  onEvent: (event: WallEvent) => void;
  onStatus?: (status: WallConnectionStatus) => void;
};

// Opens a Wall WebSocket (preferred) or falls back to short-interval polling
// against the Wall REST API when only `VITE_WALL_API_URL` is configured. If
// neither is set, the caller (CallWall) plays the local mock event script.
export function connectWall(opts: WallClientOptions): WallClientHandle {
  if (opts.url) {
    return connectWallWebSocket(opts.url, opts);
  }
  if (opts.pollUrl) {
    return connectWallPolling(opts.pollUrl, opts);
  }
  // No transport configured — caller is responsible for the mock fallback.
  opts.onStatus?.("idle");
  return {
    close() {},
    status: () => "idle",
  };
}

function connectWallWebSocket(
  url: string,
  opts: WallClientOptions,
): WallClientHandle {
  let status: WallConnectionStatus = "connecting";
  opts.onStatus?.(status);

  const qs = opts.companyId
    ? (url.includes("?") ? "&" : "?") + `company=${encodeURIComponent(opts.companyId)}`
    : "";
  const ws = new WebSocket(url + qs);

  ws.onopen = () => {
    status = "connected";
    opts.onStatus?.(status);
  };
  ws.onclose = () => {
    status = "disconnected";
    opts.onStatus?.(status);
  };
  ws.onerror = () => {
    status = "error";
    opts.onStatus?.(status);
  };
  ws.onmessage = (msg: MessageEvent<string>) => {
    try {
      const payload = JSON.parse(msg.data) as WallEvent;
      if (payload && typeof (payload as { type?: string }).type === "string") {
        opts.onEvent(payload);
      }
    } catch (err) {
      console.warn("[wall] dropped malformed event", err);
    }
  };

  return {
    close() {
      try {
        ws.close();
      } catch {
        // ignore
      }
    },
    status: () => status,
  };
}

function connectWallPolling(
  url: string,
  opts: WallClientOptions,
): WallClientHandle {
  let status: WallConnectionStatus = "connecting";
  opts.onStatus?.(status);
  let cancelled = false;
  let lastSeen = 0;

  const tick = async () => {
    if (cancelled) return;
    try {
      const qs = new URLSearchParams();
      if (opts.companyId) qs.set("company", opts.companyId);
      qs.set("since", String(lastSeen));
      const res = await fetch(`${url}?${qs.toString()}`);
      if (!res.ok) throw new Error(`wall_api ${res.status}`);
      const data = (await res.json()) as { events?: WallEvent[] };
      if (status !== "connected") {
        status = "connected";
        opts.onStatus?.(status);
      }
      for (const event of data.events ?? []) {
        opts.onEvent(event);
        const t = Date.parse(event.timestamp);
        if (!Number.isNaN(t) && t > lastSeen) lastSeen = t;
      }
    } catch (err) {
      console.warn("[wall] poll failed", err);
      if (status !== "error") {
        status = "error";
        opts.onStatus?.(status);
      }
    }
  };

  void tick();
  const handle = setInterval(tick, 1500);
  return {
    close() {
      cancelled = true;
      clearInterval(handle);
      status = "disconnected";
      opts.onStatus?.(status);
    },
    status: () => status,
  };
}
