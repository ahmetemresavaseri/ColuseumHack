import type { LiveCall, SlotKey, SlotMap, WallEvent } from "./types";

const EMPTY_SLOTS: SlotMap = {
  when: "",
  what: "",
  area: "",
  rooms: "",
  urgency: "",
  location: "",
};

const SLOT_KEYS: SlotKey[] = [
  "when",
  "what",
  "area",
  "rooms",
  "urgency",
  "location",
];

export function emptyCall(companyId: string, companyName: string): LiveCall {
  return {
    callId: "",
    companyId,
    companyName,
    caller: "",
    locale: "",
    startedAt: "",
    status: "Idle",
    slots: { ...EMPTY_SLOTS },
    transcript: [],
    citations: [],
    brain: null,
  };
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function reduceCall(call: LiveCall, event: WallEvent): LiveCall {
  switch (event.type) {
    case "CallStarted":
      return {
        ...call,
        callId: event.callId,
        companyId: event.companyId,
        companyName: event.companyName ?? call.companyName,
        caller: event.caller ?? call.caller,
        locale: event.locale ?? call.locale,
        startedAt: formatTime(event.timestamp),
        endedAt: undefined,
        status: "Live",
        slots: { ...EMPTY_SLOTS },
        transcript: [],
        citations: [],
        brain: null,
        agentSpeaking: false,
        error: undefined,
      };

    case "TranscriptTurn": {
      // Upsert by `seq` so partials can be replaced by finals later.
      const existingIdx = call.transcript.findIndex(
        (turn) => turn.seq === event.seq,
      );
      const turn = {
        seq: event.seq,
        speaker: event.speaker,
        text: event.text,
      } as const;
      const transcript =
        existingIdx >= 0
          ? call.transcript.map((t, i) => (i === existingIdx ? turn : t))
          : [...call.transcript, turn];
      return { ...call, transcript };
    }

    case "SlotSaved": {
      if (!SLOT_KEYS.includes(event.slot)) return call;
      return {
        ...call,
        slots: { ...call.slots, [event.slot]: event.value },
      };
    }

    case "CitationAdded": {
      const exists = call.citations.some(
        (c) => c.source === event.source && c.excerpt === event.excerpt,
      );
      if (exists) return call;
      return {
        ...call,
        citations: [
          ...call.citations,
          { source: event.source, excerpt: event.excerpt },
        ],
      };
    }

    case "BrainEstimate":
      return {
        ...call,
        brain: {
          serviceType: event.serviceType,
          price: event.price,
          currency: event.currency,
          needsPhotos: event.needsPhotos ?? false,
          feasibility: event.feasibility,
        },
      };

    case "CallEnded":
      return {
        ...call,
        status: "Ended",
        endedAt: formatTime(event.timestamp),
        agentSpeaking: false,
      };

    case "AgentSpeakingStart":
      return { ...call, agentSpeaking: true };

    case "AgentSpeakingEnd":
      return { ...call, agentSpeaking: false };

    case "Error":
      return { ...call, status: "Error", error: event.message };

    default:
      return call;
  }
}
