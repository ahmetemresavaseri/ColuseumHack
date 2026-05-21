import type { Citation } from "../lib/types";

export function CitationsPane({ citations }: { citations: Citation[] }) {
  return (
    <section className="panel citationsPanel">
      <p className="eyebrow">Citations</p>
      {citations.map((citation) => (
        <article key={citation.source}>
          <strong>{citation.source}</strong>
          <p>{citation.excerpt}</p>
        </article>
      ))}
    </section>
  );
}
