import type { Citation } from "../lib/types";

export function CitationsPane({ citations }: { citations: Citation[] }) {
  return (
    <section className="panel citationsPanel">
      <p className="eyebrow">Citations</p>
      {citations.length === 0 ? (
        <p className="placeholder">
          KB citations appear here when the agent answers an FAQ.
        </p>
      ) : (
        citations.map((citation, idx) => (
          <article key={`${citation.source}-${idx}`}>
            <strong>{citation.source}</strong>
            <p>{citation.excerpt}</p>
          </article>
        ))
      )}
    </section>
  );
}
