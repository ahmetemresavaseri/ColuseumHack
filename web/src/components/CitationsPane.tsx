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
        <ul className="citationList">
          {citations.map((citation, idx) => (
            <li key={`${citation.source}-${idx}`} className="citation">
              <span className="citationSource">{citation.source}</span>
              <p className="citationExcerpt">{citation.excerpt}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
