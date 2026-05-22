import type { LiveCall } from "../lib/types";

function statusTone(status: string): "idle" | "live" | "ended" {
  const s = (status || "").toLowerCase();
  if (s.includes("end") || s.includes("hung") || s.includes("done")) return "ended";
  if (s === "idle" || s === "" || s.includes("wait")) return "idle";
  return "live";
}

export function CallStatusCard({
  call,
  wallStatus,
  audioStatus,
}: {
  call: LiveCall;
  wallStatus?: string;
  audioStatus?: string;
}) {
  const tone = statusTone(call.status);
  return (
    <section className="panel statusPanel">
      <p className="eyebrow">Call</p>
      <div className="statusHeadline">
        <span className={`statusDot statusDot--${tone}`} aria-hidden />
        <h2>{call.status || "Idle"}</h2>
      </div>
      <dl className="statusList">
        <div>
          <dt>Caller</dt>
          <dd>{call.caller || "—"}</dd>
        </div>
        <div>
          <dt>Locale</dt>
          <dd>{call.locale || "—"}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{call.startedAt || "—"}</dd>
        </div>
        {call.endedAt ? (
          <div>
            <dt>Ended</dt>
            <dd>{call.endedAt}</dd>
          </div>
        ) : null}
        {wallStatus ? (
          <div>
            <dt>Wall</dt>
            <dd>{wallStatus}</dd>
          </div>
        ) : null}
        {audioStatus ? (
          <div>
            <dt>Mic</dt>
            <dd>{audioStatus}</dd>
          </div>
        ) : null}
      </dl>
      {call.error ? <p className="errorText">{call.error}</p> : null}
    </section>
  );
}
