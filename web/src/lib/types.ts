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
  speaker: "Caller" | "Sarah";
  text: string;
};

export type Citation = {
  source: string;
  excerpt: string;
};

export type BrainEstimate = {
  serviceType: string;
  price: number;
  currency: string;
  needsPhotos: boolean;
};

export type LiveCall = {
  callId: string;
  companyName: string;
  caller: string;
  locale: string;
  startedAt: string;
  status: "Live" | "Ended";
  slots: SlotMap;
  transcript: TranscriptTurn[];
  citations: Citation[];
  brain: BrainEstimate;
};
