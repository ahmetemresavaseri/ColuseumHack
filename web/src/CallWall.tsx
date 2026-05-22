import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { BrainPane } from "./components/BrainPane";
import { CallStatusCard } from "./components/CallStatusCard";
import { CitationsPane } from "./components/CitationsPane";
import { DemoControls } from "./components/DemoControls";
import { SlotsPane } from "./components/SlotsPane";
import { TranscriptPane } from "./components/TranscriptPane";
import { emptyCall, reduceCall } from "./lib/callState";
import { playMockEventScript, type MockEventHandle } from "./lib/mockEvents";
import { startAudioCall, type AudioCallStatus, type AudioClientHandle } from "./lib/audioClient";
import { connectWall, type WallClientHandle, type WallConnectionStatus } from "./lib/wallClient";
import type { LiveCall, WallEvent } from "./lib/types";

type EnvBag = {
  audioWsUrl?: string;
  wallWsUrl?: string;
  wallApiUrl?: string;
  companyId: string;
  companyName: string;
};

function readEnv(): EnvBag {
  const env = (import.meta as ImportMeta & { env: Record<string, string | undefined> }).env;
  return {
    audioWsUrl: env.VITE_AUDIO_WS_URL,
    wallWsUrl: env.VITE_WALL_WS_URL,
    wallApiUrl: env.VITE_WALL_API_URL,
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
  const [wallStatus, setWallStatus] = useState<WallConnectionStatus>("idle");
  const [audioStatus, setAudioStatus] = useState<AudioCallStatus>("idle");
  const audioRef = useRef<AudioClientHandle | null>(null);
  const mockRef = useRef<MockEventHandle | null>(null);

  // Wall transport: WS if configured, polling if only REST is configured,
  // otherwise idle and the user can hit Simulate to play the local mock.
  useEffect(() => {
    if (!envBag.wallWsUrl && !envBag.wallApiUrl) {
      setWallStatus("idle");
      return;
    }
    const handle: WallClientHandle = connectWall({
      url: envBag.wallWsUrl,
      pollUrl: envBag.wallApiUrl,
      companyId: envBag.companyId,
      onEvent: (event) => dispatch({ kind: "event", event }),
      onStatus: setWallStatus,
    });
    return () => handle.close();
  }, [envBag.wallWsUrl, envBag.wallApiUrl, envBag.companyId]);

  const handleClear = useCallback(() => {
    mockRef.current?.cancel();
    mockRef.current = null;
    dispatch({ kind: "reset", call: emptyCall(envBag.companyId, envBag.companyName) });
  }, [envBag.companyId, envBag.companyName]);

  const handleSimulate = useCallback(() => {
    mockRef.current?.cancel();
    dispatch({ kind: "reset", call: emptyCall(envBag.companyId, envBag.companyName) });
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

  useEffect(() => () => {
    audioRef.current?.hangUp();
    mockRef.current?.cancel();
  }, []);

  const inCall = audioStatus === "live" || audioStatus === "connecting" || audioStatus === "requesting-mic";

  return (
    <main className="wall">
      <header className="wallHeader">
        <div>
          <p className="eyebrow">Atrium Live Call Wall</p>
          <h1>{call.companyName}</h1>
          {!envBag.audioWsUrl && !envBag.wallWsUrl && !envBag.wallApiUrl ? (
            <p className="placeholder">
              Demo mode — set <code>VITE_AUDIO_WS_URL</code> and{" "}
              <code>VITE_WALL_WS_URL</code> to connect to real AWS endpoints.
            </p>
          ) : null}
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
