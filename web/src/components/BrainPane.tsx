import type { BrainEstimate, FeasibilityStatus } from "../lib/types";

const FEAS_LABEL: Record<FeasibilityStatus, string> = {
  bookable: "Bookable",
  needs_review: "Needs review",
  unsupported: "Unsupported",
};

const REASON_LABEL: Record<string, string> = {
  no_crew_assigned: "No crew assigned",
  photos_required: "Photos required",
  large_area: "Large area",
  large_rooms: "Many rooms",
  over_capacity: "Over daily capacity",
  unknown_service: "Service not recognized",
  service_not_offered: "Service not offered",
};

export function BrainPane({ brain }: { brain: BrainEstimate | null }) {
  if (!brain) {
    return (
      <section className="panel brainPanel">
        <p className="eyebrow">Total Cost</p>
        <h2 className="brainPriceIdle">—</h2>
        <p className="placeholder">
          Waiting for enough slots to estimate the price.
        </p>
      </section>
    );
  }
  const feas = brain.feasibility;
  return (
    <section className="panel brainPanel">
      <p className="eyebrow">Total Cost</p>
      <h2>
        {brain.currency} {brain.price.toFixed(2)}
      </h2>
      {feas ? (
        <div className="feasibility">
          <span className={`feasChip feasChip--${feas.status}`}>
            {FEAS_LABEL[feas.status]}
          </span>
          {feas.reasons.length > 0 ? (
            <ul className="feasReasons">
              {feas.reasons.map((r) => (
                <li key={r}>{REASON_LABEL[r] ?? r}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
