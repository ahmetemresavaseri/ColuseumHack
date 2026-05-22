import type { SlotMap } from "../lib/types";
import { MiniCalendar } from "./MiniCalendar";
import { UrgencyIndicator, urgencyToLevel } from "./UrgencyIndicator";

const labels: Record<keyof SlotMap, string> = {
  when: "When",
  what: "What",
  area: "Area",
  rooms: "Rooms",
  urgency: "Urgency",
  location: "Additional information",
};

// Backend stores some slot values as ENUMs (e.g. MOVE_OUT_CLEANING).
// Convert any all-caps underscored value into Title Case
// ("Move Out Cleaning"). Leaves normal strings, emails, numbers alone.
function humanize(value: unknown): string {
  const s = String(value ?? "");
  if (!/^[A-Z][A-Z0-9_]+$/.test(s)) return s;
  return s
    .replace(/_/g, " ")
    .toLowerCase()
    .split(" ")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

// Best-effort date parser for the "When" slot. Backend stores ISO strings
// most of the time; mock data may also send freeform text ("tomorrow").
function tryParseDate(value: unknown): Date | null {
  if (value == null) return null;
  if (value instanceof Date) return value;
  const s = String(value);
  if (!s.trim()) return null;
  // Accept ISO YYYY-MM-DD or full datetimes.
  const d = new Date(s);
  if (!isNaN(d.getTime())) return d;
  return null;
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatWhen(value: unknown): string {
  const d = tryParseDate(value);
  if (!d) return capitalize(String(value));
  const datePart = d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  const hasTime = d.getHours() !== 0 || d.getMinutes() !== 0;
  if (!hasTime) return capitalize(datePart);
  const timePart = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
  return capitalize(`${datePart} · ${timePart}`);
}

export function SlotsPane({ slots }: { slots: SlotMap }) {
  const bookedDate = tryParseDate(slots.when);

  return (
    <section className="panel slotsPanel">
      <p className="eyebrow">Slots</p>
      <div className="slotsBody">
        <dl className="slotGrid">
          {(Object.keys(labels) as Array<keyof SlotMap>).map((key) => {
            const raw = slots[key];
            const filled =
              raw !== undefined && raw !== null && String(raw).length > 0;
            const urgencyLevel = key === "urgency" && filled ? urgencyToLevel(raw) : null;
            return (
              <div key={key} className={`slot ${filled ? "slot--filled" : "slot--pending"}`}>
                <dt>{labels[key]}</dt>
                <dd>
                  {!filled ? (
                    "Pending"
                  ) : key === "urgency" && urgencyLevel !== null ? (
                    <UrgencyIndicator level={urgencyLevel} />
                  ) : key === "when" ? (
                    formatWhen(raw)
                  ) : (
                    humanize(raw)
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
        <MiniCalendar bookedDate={bookedDate} />
      </div>
    </section>
  );
}
