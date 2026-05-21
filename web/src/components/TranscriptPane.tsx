import type { TranscriptTurn } from "../lib/types";

export function TranscriptPane({ turns }: { turns: TranscriptTurn[] }) {
  return (
    <section className="panel transcriptPanel">
      <p className="eyebrow">Transcript</p>
      <div className="turnList">
        {turns.map((turn) => (
          <article key={turn.seq} className="turn">
            <strong>{turn.speaker}</strong>
            <p>{turn.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
