export type DemoControlsProps = {
  onCallNow: () => void;
  onHangUp: () => void;
  onSimulate: () => void;
  onClear: () => void;
  canCall: boolean;
  inCall: boolean;
};

// "Call now" is the *fallback* browser-mic path. The primary phone demo is the
// claimed Amazon Connect number; this control only appears when explicitly
// wired via `VITE_AUDIO_WS_URL`.
export function DemoControls({
  onCallNow,
  onHangUp,
  onSimulate,
  onClear,
  canCall,
  inCall,
}: DemoControlsProps) {
  return (
    <div className="controls">
      <button type="button" onClick={onSimulate} className="primary">
        Simulate
      </button>
      <button type="button" onClick={onClear} className="secondary">
        Clear
      </button>
      {inCall ? (
        <button type="button" onClick={onHangUp} className="secondary">
          Hang up (mic)
        </button>
      ) : canCall ? (
        <button
          type="button"
          onClick={onCallNow}
          className="secondary"
          title="Fallback only — use the Connect phone number for the real demo"
        >
          Call via mic
        </button>
      ) : null}
    </div>
  );
}
