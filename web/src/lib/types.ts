export type SlotKey =
  | "when"
  | "what"
  | "area"
  | "rooms"
  | "urgency"
  | "email";

export type SlotMap = {
  when: string;
  what: string;
  area: string;
  rooms: string;
  urgency: string;
  email: string;
};

export type TranscriptTurn = {
  seq: number;
  speaker: "Caller" | "Agent" | "Sarah";
  text: string;
};

export type Citation = {
  source: string;
  excerpt: string;
};

export type FeasibilityStatus = "bookable" | "needs_review" | "unsupported";

export type Feasibility = {
  status: FeasibilityStatus;
  reasons: string[];
  confidence: number;
};

export type BrainEstimate = {
  serviceType: string;
  price: number;
  currency: string;
  needsPhotos: boolean;
  feasibility?: Feasibility;
};

export type CallStatus = "Idle" | "Live" | "Ended" | "Error";

export type LiveCall = {
  callId: string;
  companyId: string;
  companyName: string;
  caller: string;
  locale: string;
  startedAt: string;
  endedAt?: string;
  status: CallStatus;
  slots: SlotMap;
  transcript: TranscriptTurn[];
  citations: Citation[];
  brain: BrainEstimate | null;
  agentSpeaking?: boolean;
  error?: string;
};

// ---------------------------------------------------------------------------
// Shared event contract
// ---------------------------------------------------------------------------
//
// These event shapes are mirrored in `lambdas/input_agent/events.py` so the
// frontend, the wall fan-out path, and the Input Agent Lambda all speak the
// same JSON. Phase 1 only needs the events listed here; future phases append
// new `type` values without breaking compatibility.

export type WallEventBase = {
  callId: string;
  companyId: string;
  timestamp: string;
};

export type CallStartedEvent = WallEventBase & {
  type: "CallStarted";
  companyName?: string;
  caller?: string;
  locale?: string;
};

export type TranscriptTurnEvent = WallEventBase & {
  type: "TranscriptTurn";
  seq: number;
  speaker: "Caller" | "Agent";
  text: string;
  isFinal?: boolean;
};

export type SlotSavedEvent = WallEventBase & {
  type: "SlotSaved";
  slot: SlotKey;
  value: string;
  bookingId?: string;
};

export type CitationAddedEvent = WallEventBase & {
  type: "CitationAdded";
  source: string;
  excerpt: string;
};

export type CallEndedEvent = WallEventBase & {
  type: "CallEnded";
  reason?: string;
};

export type AgentSpeakingStartEvent = WallEventBase & {
  type: "AgentSpeakingStart";
};

export type AgentSpeakingEndEvent = WallEventBase & {
  type: "AgentSpeakingEnd";
};

export type ErrorEvent = WallEventBase & {
  type: "Error";
  message: string;
};

export type BrainEstimateEvent = WallEventBase & {
  type: "BrainEstimate";
  serviceType: string;
  price: number;
  currency: string;
  needsPhotos?: boolean;
  feasibility?: Feasibility;
};

export type WallEvent =
  | CallStartedEvent
  | TranscriptTurnEvent
  | SlotSavedEvent
  | CitationAddedEvent
  | CallEndedEvent
  | AgentSpeakingStartEvent
  | AgentSpeakingEndEvent
  | ErrorEvent
  | BrainEstimateEvent;

export type WallEventType = WallEvent["type"];
