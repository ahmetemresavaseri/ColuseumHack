import type { SlotMap } from "../lib/types";

const labels: Record<keyof SlotMap, string> = {
  when: "When",
  what: "What",
  area: "Area",
  rooms: "Rooms",
  urgency: "Urgency",
  location: "Location",
};

export function SlotsPane({ slots }: { slots: SlotMap }) {
  return (
    <section className="panel">
      <p className="eyebrow">Slots</p>
      <dl className="slotGrid">
        {(Object.keys(labels) as Array<keyof SlotMap>).map((key) => (
          <div key={key}>
            <dt>{labels[key]}</dt>
            <dd>{slots[key] || "Pending"}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
