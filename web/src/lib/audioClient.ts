// Browser-side mic capture + audio WebSocket plumbing for the Atrium "Call now"
// button. Phase 1 streams 16 kHz mono PCM16 frames as binary WebSocket
// messages; control messages (start/end/agent audio) are JSON text frames.
//
// Wire format (frontend ↔ Input Agent Lambda):
//   client → server (binary): raw PCM16 little-endian, 16 kHz mono, ~40 ms frames
//   client → server (text):   { "action": "start_call" | "end_call" | "text_turn", ... }
//   server → client (text):   { "type": "agent_text" | "agent_audio_b64" | "control", ... }
//
// The exact agent-audio framing is left flexible because Phase 1's Lambda may
// not yet stream Polly bytes; the client only assumes text/control messages
// for now and tolerates unknown frame types.

export type AudioCallStatus =
  | "idle"
  | "requesting-mic"
  | "connecting"
  | "live"
  | "ended"
  | "error";

export type AudioClientOptions = {
  url: string;
  companyId?: string;
  callerLabel?: string;
  onStatus?: (status: AudioCallStatus) => void;
  onAgentAudio?: (bytes: ArrayBuffer) => void;
  onAgentText?: (text: string) => void;
  onError?: (message: string) => void;
};

export type AudioClientHandle = {
  hangUp: () => void;
  sendText: (text: string) => void;
  status: () => AudioCallStatus;
};

const TARGET_SAMPLE_RATE = 16000;
const FRAME_MS = 40;
const FRAME_SAMPLES = (TARGET_SAMPLE_RATE * FRAME_MS) / 1000; // 640

export async function startAudioCall(
  opts: AudioClientOptions,
): Promise<AudioClientHandle> {
  let status: AudioCallStatus = "requesting-mic";
  opts.onStatus?.(status);

  let mediaStream: MediaStream;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, sampleRate: TARGET_SAMPLE_RATE },
      video: false,
    });
  } catch (err) {
    status = "error";
    opts.onStatus?.(status);
    const message =
      err instanceof Error ? err.message : "Microphone permission denied";
    opts.onError?.(message);
    throw err;
  }

  status = "connecting";
  opts.onStatus?.(status);

  const url = opts.companyId
    ? opts.url +
      (opts.url.includes("?") ? "&" : "?") +
      `company=${encodeURIComponent(opts.companyId)}`
    : opts.url;
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";

  const audioCtx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  const source = audioCtx.createMediaStreamSource(mediaStream);
  const processor = audioCtx.createScriptProcessor(2048, 1, 1);

  const frameBuffer: number[] = [];

  processor.onaudioprocess = (event: AudioProcessingEvent) => {
    if (ws.readyState !== WebSocket.OPEN) return;
    const channel = event.inputBuffer.getChannelData(0);
    for (let i = 0; i < channel.length; i++) {
      const sample = Math.max(-1, Math.min(1, channel[i]));
      frameBuffer.push(sample < 0 ? sample * 0x8000 : sample * 0x7fff);
      if (frameBuffer.length >= FRAME_SAMPLES) {
        const out = new Int16Array(FRAME_SAMPLES);
        for (let j = 0; j < FRAME_SAMPLES; j++) out[j] = frameBuffer[j];
        frameBuffer.splice(0, FRAME_SAMPLES);
        try {
          ws.send(out.buffer);
        } catch {
          // ignore send errors; the close handler will surface them
        }
      }
    }
  };

  source.connect(processor);
  processor.connect(audioCtx.destination);

  const cleanup = () => {
    try {
      processor.disconnect();
      source.disconnect();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      void (audioCtx.state !== "closed" ? audioCtx.close() : null);
      mediaStream.getTracks().forEach((t) => t.stop());
    } catch {
      // ignore
    }
  };

  ws.onopen = () => {
    status = "live";
    opts.onStatus?.(status);
    ws.send(
      JSON.stringify({
        action: "start_call",
        companyId: opts.companyId,
        sampleRate: TARGET_SAMPLE_RATE,
        encoding: "pcm16le",
        frameMs: FRAME_MS,
        caller: opts.callerLabel,
      }),
    );
  };

  ws.onmessage = (msg: MessageEvent) => {
    if (typeof msg.data === "string") {
      try {
        const payload = JSON.parse(msg.data) as Record<string, unknown>;
        if (payload.type === "agent_text" && typeof payload.text === "string") {
          opts.onAgentText?.(payload.text);
        }
        if (
          payload.type === "agent_audio_b64" &&
          typeof payload.audio === "string"
        ) {
          const bytes = base64ToArrayBuffer(payload.audio);
          opts.onAgentAudio?.(bytes);
        }
      } catch {
        // ignore non-JSON text frames
      }
    } else if (msg.data instanceof ArrayBuffer) {
      opts.onAgentAudio?.(msg.data);
    }
  };

  ws.onerror = () => {
    status = "error";
    opts.onStatus?.(status);
    opts.onError?.("Audio WebSocket error");
  };

  ws.onclose = () => {
    if (status !== "ended") {
      status = "ended";
      opts.onStatus?.(status);
    }
    cleanup();
  };

  return {
    hangUp() {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: "end_call", reason: "user_hangup" }));
        }
        ws.close();
      } catch {
        // ignore
      }
      status = "ended";
      opts.onStatus?.(status);
      cleanup();
    },
    sendText(text: string) {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ action: "text_turn", text }));
    },
    status: () => status,
  };
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
