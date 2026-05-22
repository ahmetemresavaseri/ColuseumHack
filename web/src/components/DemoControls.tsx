export type DemoControlsProps = {
  onCallNow: () => void;
  onHangUp: () => void;
  onSimulate: () => void;
  onClear: () => void;
  canCall: boolean;
  inCall: boolean;
};

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
      {inCall ? (
        <button type="button" onClick={onHangUp} className="primary">
          Hang up
        </button>
      ) : (
        <button
          type="button"
          onClick={onCallNow}
          disabled={!canCall}
          className="primary"
          title={
            canCall
              ? "Open mic and connect to the Atrium voice agent"
              : "Set VITE_AUDIO_WS_URL to enable Call now"
          }
        >
          Call now
        </button>
      )}
      <button type="button" onClick={onSimulate}>
        Simulate
      </button>
      <button type="button" onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
