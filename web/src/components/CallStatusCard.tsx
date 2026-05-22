import type { LiveCall } from "../lib/types";

export function CallStatusCard({
  call,
  wallStatus,
  audioStatus,
}: {
  call: LiveCall;
  wallStatus?: string;
  audioStatus?: string;
}) {
  return (
    <section className="panel statusPanel">
      <p className="eyebrow">Call</p>
      <h2>{call.status}</h2>
      <dl>
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
