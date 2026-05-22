import type { TranscriptTurn } from "../lib/types";

function speakerTone(speaker: string): "agent" | "caller" | "system" {
  const s = (speaker || "").toLowerCase();
  if (s.includes("caller") || s.includes("user") || s.includes("customer")) return "caller";
  if (s.includes("system") || s.includes("event")) return "system";
  return "agent";
}

export function TranscriptPane({ turns }: { turns: TranscriptTurn[] }) {
  return (
    <section className="panel transcriptPanel">
      <p className="eyebrow">Transcript</p>
      <div className="turnList">
        {turns.length === 0 ? (
          <p className="placeholder">
            No turns yet — the transcript fills in live as the call runs.
          </p>
        ) : (
          turns.map((turn) => (
            <article key={turn.seq} className={`turn turn--${speakerTone(turn.speaker)}`}>
              <span className="turnSpeaker">{turn.speaker}</span>
              <p>{turn.text}</p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
