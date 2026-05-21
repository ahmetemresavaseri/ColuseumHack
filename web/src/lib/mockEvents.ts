import type { LiveCall } from "./types";

export const mockCall: LiveCall = {
  callId: "demo-call-001",
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
    email: "customer@example.com",
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
