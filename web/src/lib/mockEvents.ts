import type { LiveCall, WallEvent } from "./types";

// Static LiveCall preserved so existing components/snapshots keep working.
export const mockCall: LiveCall = {
  callId: "demo-call-001",
  companyId: "glanz-ag",
  companyName: "Glanz AG",
  caller: "+41 44 000 00 00",
  locale: "de-CH",
  startedAt: "10:15",
  status: "Live",
  slots: {
    when: "Tomorrow morning",
    what: "MOVE_OUT_CLEANING",
    area: "85 m2",
    rooms: "4",
    urgency: "urgent",
    location: "Bahnhofstrasse 12, 8001 Zürich",
  },
  transcript: [
    { seq: 1, speaker: "Sarah", text: "Hello, Glanz AG. How can I help?" },
    { seq: 2, speaker: "Caller", text: "I need move-out cleaning tomorrow." },
  ],
  citations: [
    {
      source: "pricelist.md",
      excerpt: "Move-out cleaning starts with a base fee and area rate.",
    },
  ],
  brain: {
    serviceType: "MOVE_OUT_CLEANING",
    price: 703.13,
    currency: "CHF",
    needsPhotos: false,
  },
};

const callId = "sim-call-001";
const companyId = "glanz-ag";

function ts(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

// A scripted demo sequence used when no live wall WebSocket is configured.
// Each event is emitted with its own delay so the UI animates the way a real
// call would. The same shape comes from the Lambda fan-out in production.
export const mockEventScript: Array<{ delayMs: number; event: WallEvent }> = [
  {
    delayMs: 250,
    event: {
      type: "CallStarted",
      callId,
      companyId,
      companyName: "Glanz AG",
      caller: "+41 44 000 00 00",
      locale: "de-CH",
      timestamp: ts(0),
    },
  },
  {
    delayMs: 800,
    event: {
      type: "TranscriptTurn",
      callId,
      companyId,
      timestamp: ts(800),
      seq: 1,
      speaker: "Agent",
      text: "Hello, Glanz AG. How can I help?",
      isFinal: true,
    },
  },
  {
    delayMs: 1700,
    event: {
      type: "TranscriptTurn",
      callId,
      companyId,
      timestamp: ts(1700),
      seq: 2,
      speaker: "Caller",
      text: "I need a move-out cleaning tomorrow for 85 square meters.",
      isFinal: true,
    },
  },
  {
    delayMs: 2200,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(2200),
      slot: "what",
      value: "MOVE_OUT_CLEANING",
    },
  },
  {
    delayMs: 2400,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(2400),
      slot: "when",
      value: "tomorrow",
    },
  },
  {
    delayMs: 2700,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(2700),
      slot: "area",
      value: "85 m2",
    },
  },
  {
    delayMs: 3400,
    event: {
      type: "TranscriptTurn",
      callId,
      companyId,
      timestamp: ts(3400),
      seq: 3,
      speaker: "Caller",
      text: "Four rooms, it's urgent. The address is Bahnhofstrasse 12, 8001 Zürich.",
      isFinal: true,
    },
  },
  {
    delayMs: 3700,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(3700),
      slot: "rooms",
      value: "4",
    },
  },
  {
    delayMs: 3900,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(3900),
      slot: "urgency",
      value: "urgent",
    },
  },
  {
    delayMs: 4100,
    event: {
      type: "SlotSaved",
      callId,
      companyId,
      timestamp: ts(4100),
      slot: "location",
      value: "Bahnhofstrasse 12, 8001 Zürich",
    },
  },
  {
    delayMs: 4600,
    event: {
      type: "CitationAdded",
      callId,
      companyId,
      timestamp: ts(4600),
      source: "Pricelist.pdf p.3",
      excerpt: "Move-out cleaning starts with a base fee and area rate.",
    },
  },
  {
    delayMs: 5100,
    event: {
      type: "BrainEstimate",
      callId,
      companyId,
      timestamp: ts(5100),
      serviceType: "MOVE_OUT_CLEANING",
      price: 703.13,
      currency: "CHF",
      needsPhotos: false,
    },
  },
  {
    delayMs: 6000,
    event: {
      type: "CallEnded",
      callId,
      companyId,
      timestamp: ts(6000),
      reason: "completed",
    },
  },
];

export type MockEventHandle = { cancel: () => void };

export function playMockEventScript(
  onEvent: (event: WallEvent) => void,
): MockEventHandle {
  const timers: ReturnType<typeof setTimeout>[] = [];
  for (const step of mockEventScript) {
    timers.push(setTimeout(() => onEvent(step.event), step.delayMs));
  }
  return {
    cancel() {
      timers.forEach((t) => clearTimeout(t));
    },
  };
}
