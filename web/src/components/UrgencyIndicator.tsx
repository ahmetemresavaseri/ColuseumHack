/**
 * Compact 3-bar signal-style indicator for the "Urgency" slot.
 * Heights ascend left→right; filled bars darken with urgency level.
 *   Low    = 1/3 bars, very pale action green
 *   Medium = 2/3 bars, mid action green
 *   High   = 3/3 bars, full action green
 */

export type UrgencyLevel = 1 | 2 | 3;

const LEVEL_LABEL: Record<UrgencyLevel, string> = {
  1: "Low",
  2: "Medium",
  3: "High",
};

export function urgencyToLevel(raw: unknown): UrgencyLevel | null {
  const s = String(raw ?? "").toLowerCase().trim();
  if (!s) return null;
  if (s === "low" || s === "1" || s === "calm") return 1;
  if (s === "medium" || s === "med" || s === "2" || s === "normal") return 2;
  if (s === "high" || s === "3" || s === "urgent" || s === "asap") return 3;
  return null;
}

export function UrgencyIndicator({ level }: { level: UrgencyLevel }) {
  return (
    <div className="urgencyBars" data-level={level} aria-label={`Urgency ${LEVEL_LABEL[level]}`}>
      <span className={`urgencyBar urgencyBar-1 ${level >= 1 ? "filled" : ""}`} />
      <span className={`urgencyBar urgencyBar-2 ${level >= 2 ? "filled" : ""}`} />
      <span className={`urgencyBar urgencyBar-3 ${level >= 3 ? "filled" : ""}`} />
      <span className="urgencyLabel">{LEVEL_LABEL[level]}</span>
    </div>
  );
}
