import type { LiveCall } from "../lib/types";

export function CallStatusCard({ call }: { call: LiveCall }) {
  return (
    <section className="panel statusPanel">
      <p className="eyebrow">Call</p>
      <h2>{call.status}</h2>
      <dl>
        <div>
          <dt>Caller</dt>
          <dd>{call.caller}</dd>
        </div>
        <div>
          <dt>Locale</dt>
          <dd>{call.locale}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{call.startedAt}</dd>
        </div>
      </dl>
    </section>
  );
}
