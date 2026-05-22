// AppSync is not on the hackathon allow-list. This module is kept as a
// compatibility shim so older imports continue to resolve; new code should
// use `wallClient.ts` (API Gateway WebSocket) for live wall updates.
import type { LiveCall } from "./types";

export type CallEventHandler = (call: LiveCall) => void;

export function subscribeToCalls(_handler: CallEventHandler) {
  return {
    unsubscribe() {
      // intentional no-op
    },
  };
}
