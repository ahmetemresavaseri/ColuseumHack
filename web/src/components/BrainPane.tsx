import type { BrainEstimate } from "../lib/types";

export function BrainPane({ brain }: { brain: BrainEstimate | null }) {
  if (!brain) {
    return (
      <section className="panel">
        <p className="eyebrow">Brain</p>
        <h2>—</h2>
        <p className="placeholder">
          Waiting for enough slots to estimate the price.
        </p>
      </section>
    );
  }
  return (
    <section className="panel">
      <p className="eyebrow">Brain</p>
      <h2>
        {brain.currency} {brain.price.toFixed(2)}
      </h2>
      <p>{brain.serviceType}</p>
      <p>{brain.needsPhotos ? "Photos requested after call" : "No photos needed"}</p>
    </section>
  );
}
