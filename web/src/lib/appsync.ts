// Minimal AppSync real-time client (no Amplify dependency).
//
// AppSync GraphQL subscriptions use a sub-protocol over a WebSocket connection
// to `wss://<host>.appsync-realtime-api.<region>.amazonaws.com/graphql`.
// Reference: https://docs.aws.amazon.com/appsync/latest/devguide/real-time-websocket-client.html
//
// This file is the primary live transport for the Live Call Wall in Phase 1.
// `VITE_APPSYNC_GRAPHQL_URL` + `VITE_APPSYNC_API_KEY` are required for the
// production deployment; without them the Wall falls back to the polling
// `VITE_WALL_API_URL` (see `wallClient.ts`) or mock mode.

import type { WallEvent } from "./types";

export type AppSyncStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";

export type AppSyncSubscriptionHandle = {
  close: () => void;
  status: () => AppSyncStatus;
};

export type AppSyncOptions = {
  graphqlUrl: string;
  apiKey: string;
  companyId: string;
  onEvent: (event: WallEvent) => void;
  onStatus?: (status: AppSyncStatus) => void;
};

const ON_WALL_EVENT_SUBSCRIPTION = `
subscription OnWallEvent($companyId: ID!) {
  onWallEvent(companyId: $companyId) {
    callId
    companyId
    timestamp
    type
    payload
  }
}
`.trim();

function realtimeHost(graphqlUrl: string): string {
  const url = new URL(graphqlUrl);
  // appsync-api.<region>.amazonaws.com/graphql  ->  appsync-realtime-api.<region>.amazonaws.com/graphql
  url.host = url.host.replace("appsync-api", "appsync-realtime-api");
  return url.toString().replace(/^https/, "wss");
}

function base64UrlEncode(input: string): string {
  // btoa is fine for ASCII payloads (header/payload are JSON).
  const b64 = btoa(input);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function authHeader(graphqlUrl: string, apiKey: string): Record<string, string> {
  return {
    host: new URL(graphqlUrl).host,
    "x-api-key": apiKey,
  };
}

export function subscribeToWallEvents(opts: AppSyncOptions): AppSyncSubscriptionHandle {
  let status: AppSyncStatus = "connecting";
  opts.onStatus?.(status);

  // AppSync real-time WebSocket auth is passed as an extra subprotocol named
  // `header-<base64url(JSON header)>` alongside `graphql-ws`. The older
  // ?header=&payload= URL-query approach now gets rejected with
  // "Required headers are missing." in this AppSync API.
  const headerSubprotocol = `header-${base64UrlEncode(
    JSON.stringify(authHeader(opts.graphqlUrl, opts.apiKey)),
  )}`;
  const wsUrl = realtimeHost(opts.graphqlUrl);

  const ws = new WebSocket(wsUrl, ["graphql-ws", headerSubprotocol]);
  const subId = crypto.randomUUID?.() ?? `sub-${Math.random().toString(36).slice(2)}`;
  let pingTimer: ReturnType<typeof setInterval> | null = null;

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "connection_init" }));
  };

  ws.onmessage = (msg: MessageEvent<string>) => {
    let payload: { type?: string; payload?: unknown; id?: string };
    try {
      payload = JSON.parse(msg.data);
    } catch {
      return;
    }

    if (payload.type === "connection_ack") {
      ws.send(
        JSON.stringify({
          id: subId,
          type: "start",
          payload: {
            data: JSON.stringify({
              query: ON_WALL_EVENT_SUBSCRIPTION,
              variables: { companyId: opts.companyId },
            }),
            extensions: {
              authorization: authHeader(opts.graphqlUrl, opts.apiKey),
            },
          },
        }),
      );
      status = "connected";
      opts.onStatus?.(status);
      // AppSync drops idle connections; ping every ~4 min keeps it alive.
      pingTimer = setInterval(() => {
        try {
          ws.send(JSON.stringify({ type: "ka" }));
        } catch {
          // ignore
        }
      }, 240_000);
      return;
    }

    if (payload.type === "data" && payload.payload) {
      const result = (payload.payload as { data?: { onWallEvent?: WallRow } }).data?.onWallEvent;
      if (!result) return;
      const event = inflateWallEvent(result);
      if (event) opts.onEvent(event);
      return;
    }

    if (payload.type === "error" || payload.type === "connection_error") {
      status = "error";
      opts.onStatus?.(status);
    }
  };

  ws.onclose = () => {
    if (pingTimer) clearInterval(pingTimer);
    status = status === "error" ? "error" : "disconnected";
    opts.onStatus?.(status);
  };

  ws.onerror = () => {
    status = "error";
    opts.onStatus?.(status);
  };

  return {
    close() {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ id: subId, type: "stop" }));
        }
        ws.close();
      } catch {
        // ignore
      }
      if (pingTimer) clearInterval(pingTimer);
    },
    status: () => status,
  };
}

// ---------------------------------------------------------------------------
// AppSync delivers a flat `{ type, payload (AWSJSON string), ... }` shape;
// inflate it back into the strongly-typed WallEvent union used by the reducer.
// ---------------------------------------------------------------------------

type WallRow = {
  callId: string;
  companyId: string;
  timestamp: string;
  type: string;
  payload?: string | Record<string, unknown> | null;
};

function inflateWallEvent(row: WallRow): WallEvent | null {
  const base = {
    callId: row.callId,
    companyId: row.companyId,
    timestamp: row.timestamp,
  };
  const payload = parsePayload(row.payload);

  switch (row.type) {
    case "CallStarted":
      return {
        ...base,
        type: "CallStarted",
        companyName: payload.companyName as string | undefined,
        caller: payload.caller as string | undefined,
        locale: payload.locale as string | undefined,
      };
    case "TranscriptTurn":
      return {
        ...base,
        type: "TranscriptTurn",
        seq: Number(payload.seq) || 0,
        speaker: (payload.speaker as "Caller" | "Agent") || "Caller",
        text: String(payload.text || ""),
        isFinal: payload.isFinal !== false,
      };
    case "SlotSaved":
      return {
        ...base,
        type: "SlotSaved",
        slot: payload.slot as
          | "when"
          | "what"
          | "area"
          | "rooms"
          | "urgency"
          | "location",
        value: String(payload.value || ""),
        bookingId: payload.bookingId as string | undefined,
      };
    case "CitationAdded":
      return {
        ...base,
        type: "CitationAdded",
        source: String(payload.source || ""),
        excerpt: String(payload.excerpt || ""),
      };
    case "CallEnded":
      return {
        ...base,
        type: "CallEnded",
        reason: payload.reason as string | undefined,
      };
    case "AgentSpeakingStart":
      return { ...base, type: "AgentSpeakingStart" };
    case "AgentSpeakingEnd":
      return { ...base, type: "AgentSpeakingEnd" };
    case "BrainEstimate": {
      const feasibilityRaw = payload.feasibility as
        | { status?: string; reasons?: string[]; confidence?: number }
        | undefined;
      const feasibility = feasibilityRaw?.status
        ? {
            status: feasibilityRaw.status as
              | "bookable"
              | "needs_review"
              | "unsupported",
            reasons: feasibilityRaw.reasons ?? [],
            confidence: Number(feasibilityRaw.confidence ?? 0),
          }
        : undefined;
      return {
        ...base,
        type: "BrainEstimate",
        serviceType: String(payload.serviceType || ""),
        price: Number(payload.price) || 0,
        currency: String(payload.currency || ""),
        needsPhotos: Boolean(payload.needsPhotos),
        feasibility,
      };
    }
    case "Error":
      return { ...base, type: "Error", message: String(payload.message || "") };
    default:
      return null;
  }
}

function parsePayload(raw: WallRow["payload"]): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  return raw;
}

// Legacy stub kept for any import that still references it.
export type CallEventHandler = (call: import("./types").LiveCall) => void;
export function subscribeToCalls(_handler: CallEventHandler) {
  return {
    unsubscribe() {
      // intentional no-op; use subscribeToWallEvents instead.
    },
  };
}
