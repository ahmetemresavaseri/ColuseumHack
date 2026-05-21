import type { LiveCall } from "./types";

export type CallEventHandler = (call: LiveCall) => void;

export function subscribeToCalls(_handler: CallEventHandler) {
  return {
    unsubscribe() {
      // AppSync subscription wiring lands here after the API stack is deployed.
    },
  };
}
