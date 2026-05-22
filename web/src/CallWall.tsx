import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { BrainPane } from "./components/BrainPane";
import { CallStatusCard } from "./components/CallStatusCard";
import { CitationsPane } from "./components/CitationsPane";
import { DemoControls } from "./components/DemoControls";
import { SlotsPane } from "./components/SlotsPane";
import { TranscriptPane } from "./components/TranscriptPane";
import { emptyCall, reduceCall } from "./lib/callState";
import { playMockEventScript, type MockEventHandle } from "./lib/mockEvents";
import {
  startAudioCall,
  type AudioCallStatus,
  type AudioClientHandle,
} from "./lib/audioClient";
import {
  connectWall,
  type WallClientHandle,
  type WallConnectionStatus,
} from "./lib/wallClient";
import {
  subscribeToWallEvents,
  type AppSyncStatus,
  type AppSyncSubscriptionHandle,
} from "./lib/appsync";
import type { LiveCall, WallEvent } from "./lib/types";

type EnvBag = {
  appsyncUrl?: string;
  appsyncApiKey?: string;
  wallWsUrl?: string;
  wallApiUrl?: string;
  audioWsUrl?: string;
  phoneNumber?: string;
  companyId: string;
  companyName: string;
};

// Render "+15612820331" as "+1 561 282 0331" (or fall back to as-is).
function formatPhone(raw: string): string {
  const digits = raw.replace(/[^\d]/g, "");
  if (digits.length === 11 && digits.startsWith("1")) {
    return `+1 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  }
  return raw;
}

function readEnv(): EnvBag {
  const env = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env;
  return {
    appsyncUrl: env.VITE_APPSYNC_GRAPHQL_URL,
    appsyncApiKey: env.VITE_APPSYNC_API_KEY,
    wallWsUrl: env.VITE_WALL_WS_URL,
    wallApiUrl: env.VITE_WALL_API_URL,
    audioWsUrl: env.VITE_AUDIO_WS_URL,
    phoneNumber: env.VITE_CONNECT_PHONE_NUMBER,
    companyId: env.VITE_COMPANY_ID || "glanz-ag",
    companyName: env.VITE_COMPANY_NAME || "Glanz AG",
  };
}

type Action =
  | { kind: "event"; event: WallEvent }
  | { kind: "reset"; call: LiveCall };

function reducer(state: LiveCall, action: Action): LiveCall {
  switch (action.kind) {
    case "event":
      return reduceCall(state, action.event);
    case "reset":
      return action.call;
  }
}

export default function CallWall() {
  const envBag = useMemo(readEnv, []);
  const [call, dispatch] = useReducer(
    reducer,
    undefined,
    () => emptyCall(envBag.companyId, envBag.companyName),
  );
  const [wallStatus, setWallStatus] = useState<string>("idle");
  const [audioStatus, setAudioStatus] = useState<AudioCallStatus>("idle");
  const audioRef = useRef<AudioClientHandle | null>(null);
  const mockRef = useRef<MockEventHandle | null>(null);

  // Transport preference: AppSync subscription (default) > Wall WS > Wall REST
  // polling > idle (mock-only). Pick whichever is configured.
  useEffect(() => {
    const onEvent = (event: WallEvent) => dispatch({ kind: "event", event });
    if (envBag.appsyncUrl && envBag.appsyncApiKey) {
      const handle: AppSyncSubscriptionHandle = subscribeToWallEvents({
        graphqlUrl: envBag.appsyncUrl,
        apiKey: envBag.appsyncApiKey,
        companyId: envBag.companyId,
        onEvent,
        onStatus: (s: AppSyncStatus) => setWallStatus(`appsync:${s}`),
      });
      return () => handle.close();
    }
    if (envBag.wallWsUrl || envBag.wallApiUrl) {
      const handle: WallClientHandle = connectWall({
        url: envBag.wallWsUrl,
        pollUrl: envBag.wallApiUrl,
        companyId: envBag.companyId,
        onEvent,
        onStatus: (s: WallConnectionStatus) => setWallStatus(`wall:${s}`),
      });
      return () => handle.close();
    }
    setWallStatus("idle");
  }, [
    envBag.appsyncUrl,
    envBag.appsyncApiKey,
    envBag.wallWsUrl,
    envBag.wallApiUrl,
    envBag.companyId,
  ]);

  const handleClear = useCallback(() => {
    mockRef.current?.cancel();
    mockRef.current = null;
    dispatch({
      kind: "reset",
      call: emptyCall(envBag.companyId, envBag.companyName),
    });
  }, [envBag.companyId, envBag.companyName]);

  const handleSimulate = useCallback(() => {
    mockRef.current?.cancel();
    dispatch({
      kind: "reset",
      call: emptyCall(envBag.companyId, envBag.companyName),
    });
    mockRef.current = playMockEventScript((event) =>
      dispatch({ kind: "event", event }),
    );
  }, [envBag.companyId, envBag.companyName]);

  const handleCallNow = useCallback(async () => {
    if (!envBag.audioWsUrl || audioRef.current) return;
    try {
      const handle = await startAudioCall({
        url: envBag.audioWsUrl,
        companyId: envBag.companyId,
        callerLabel: "browser-mic",
        onStatus: setAudioStatus,
        onError: (message) =>
          dispatch({
            kind: "event",
            event: {
              type: "Error",
              callId: call.callId || "",
              companyId: envBag.companyId,
              timestamp: new Date().toISOString(),
              message,
            },
          }),
      });
      audioRef.current = handle;
    } catch {
      audioRef.current = null;
    }
  }, [envBag.audioWsUrl, envBag.companyId, call.callId]);

  const handleHangUp = useCallback(() => {
    audioRef.current?.hangUp();
    audioRef.current = null;
  }, []);

  useEffect(
    () => () => {
      audioRef.current?.hangUp();
      mockRef.current?.cancel();
    },
    [],
  );

  const inCall =
    audioStatus === "live" ||
    audioStatus === "connecting" ||
    audioStatus === "requesting-mic";

  return (
    <main className="wall">
      <header className="wallHeader">
        <div className="headerLeft">
          <h1>{call.companyName}</h1>
          {envBag.phoneNumber ? (
            <a className="callCta" href={`tel:${envBag.phoneNumber}`}>
              <span className="callCtaIcon" aria-hidden>
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z" />
                </svg>
              </span>
              <span className="callCtaLead">Speak to Atrium</span>
              <span className="callCtaNumber">{formatPhone(envBag.phoneNumber)}</span>
            </a>
          ) : (
            <p className="placeholder">
              Phone number not configured —{" "}
              <code>VITE_CONNECT_PHONE_NUMBER</code>. The Wall still listens for
              live events from AppSync.
            </p>
          )}
        </div>
        <DemoControls
          onCallNow={handleCallNow}
          onHangUp={handleHangUp}
          onSimulate={handleSimulate}
          onClear={handleClear}
          canCall={Boolean(envBag.audioWsUrl)}
          inCall={inCall}
        />
      </header>
      <section className="grid">
        <CallStatusCard
          call={call}
          wallStatus={wallStatus === "idle" ? undefined : wallStatus}
          audioStatus={audioStatus === "idle" ? undefined : audioStatus}
        />
        <TranscriptPane turns={call.transcript} />
        <SlotsPane slots={call.slots} />
        <BrainPane brain={call.brain} />
        <CitationsPane citations={call.citations} />
      </section>
    </main>
  );
}
