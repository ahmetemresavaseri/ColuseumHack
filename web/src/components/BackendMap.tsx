import { Fragment, type JSX } from "react";

/* ──────────────────────────────────────────────────────────────────────────────
   Atrium Backend Flow — mirrors README "Architecture (High-Level)".

   Phase 1: Input        Caller dials → Amazon Connect → Lex V2 (ASR + slots)
   Phase 2: Agent        Input Agent Lambda — per-turn orchestrator
   Phase 3: Tools        Knowledge Items · DynamoDB · Brain · EventBridge
   Phase 4: Live UI      DDB Streams → AppSync → Live Call Wall

   Cross-cutting: CloudWatch · S3 · EventBridge warmers.
   ────────────────────────────────────────────────────────────────────────────── */

type Card = {
  id: string;
  title: string;
  sub: string;
  badge: string;
  icon: JSX.Element;
  hero?: boolean;
};

type Phase = {
  label: string;
  sub: string;
  rows: Card[][];
  bidi?: boolean;          // draws a ⇄ arrow between cards instead of a one-way one
};

const PHASES: Phase[] = [
  {
    label: "Input",
    sub: "Caller dials the published number. Audio enters the agent.",
    rows: [
      [
        { id: "pstn",    title: "PSTN Caller",     sub: "Inbound phone call",                                    badge: "Trigger",     icon: <IconPhone /> },
        { id: "connect", title: "Amazon Connect",  sub: "Phone number · contact flow · audio bridge",            badge: "AWS Service", icon: <IconHeadset /> },
        { id: "lex",     title: "Amazon Lex V2",   sub: "Speech recognition · slot elicitation · Polly TTS",     badge: "AWS Service", icon: <IconEar /> },
      ],
    ],
  },
  {
    label: "Agent",
    sub: "Lex code-hook invokes the Lambda on every turn. It drives the conversation phase machine, validates slots, routes FAQs to the knowledge base, and calls the pricing brain when ready.",
    rows: [
      [
        {
          id: "input-agent",
          title: "Input Agent Lambda",
          sub: "Per-turn orchestrator · slot validation · phase machine (collect → estimate → confirm → questions)",
          badge: "AI Agent",
          icon: <IconAtom />,
          hero: true,
        },
      ],
    ],
  },
  {
    label: "Tools",
    sub: "The agent calls these tools mid-conversation while the caller is still speaking.",
    rows: [
      [
        { id: "kb",          title: "Knowledge Items",  sub: "Tenant-scoped FAQ / services / pricing · keyword retrieval · refusal on low confidence", badge: "RAG",         icon: <IconBook /> },
        { id: "ddb",         title: "DynamoDB",          sub: "Calls · Bookings · Companies · Crews · PriceMatrix",                                     badge: "Database",    icon: <IconDatabase /> },
        { id: "brain",       title: "Pricing Brain",     sub: "Deterministic formula · crew assignment · feasibility verdict",                          badge: "AI Tool",     icon: <IconCalculator /> },
        { id: "eventbridge", title: "EventBridge",       sub: "CallStarted · CallEnded · Lambda warmers",                                              badge: "AWS Service", icon: <IconBolt /> },
      ],
    ],
  },
  {
    label: "Live UI",
    sub: "Slot writes propagate to the dashboard in real time — zero polling.",
    rows: [
      [
        { id: "streams", title: "DynamoDB Streams", sub: "Change capture on every write",                       badge: "AWS Service", icon: <IconStream /> },
        { id: "appsync", title: "AWS AppSync",      sub: "GraphQL subscription · API-key auth",                 badge: "AWS Service", icon: <IconCloud /> },
        { id: "wall",    title: "Live Call Wall",   sub: "React · Amplify Hosting · this app",                  badge: "Frontend",    icon: <IconMonitor /> },
      ],
    ],
  },
];

const CROSS_CUTTING = [
  { label: "Amazon CloudWatch",  sub: "Lambda logs · contact-flow traces · latency evidence" },
  { label: "Amazon S3",          sub: "KB seed docs · call recordings · Wall build artifact" },
  { label: "EventBridge warmer", sub: "4-min cron keeps Input Agent + Brain Lambdas hot" },
  { label: "Amazon Polly",       sub: "Neural TTS — agent voice, rendered inside Lex" },
];

export default function BackendMap() {
  return (
    <div className="bmap-scene">
      <header className="bmap-header">
        <div>
          <p className="bmap-eyebrow">Project preview</p>
          <h1 className="bmap-title">Atrium Backend Flow</h1>
        </div>
      </header>

      <div className="bmap-flow">
        {PHASES.map((phase, idx) => (
          <PhaseBlock key={phase.label} phase={phase} stepIdx={idx} />
        ))}

        <aside className="bmap-cross">
          <div className="bmap-cross-title">Cross-cutting infrastructure</div>
          <ul className="bmap-cross-list">
            {CROSS_CUTTING.map((c) => (
              <li key={c.label}>
                <strong>{c.label}</strong>
                <span>{c.sub}</span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}

function PhaseBlock({ phase, stepIdx }: { phase: Phase; stepIdx: number }) {
  return (
    <section
      className="bmap-phase"
      style={{ animationDelay: `${stepIdx * 200}ms` }}
    >
      <div className="bmap-phase-label">
        <span className="bmap-phase-num">{stepIdx + 1}</span>
        <div>
          <div className="bmap-phase-title">{phase.label}</div>
          <div className="bmap-phase-sub">{phase.sub}</div>
        </div>
      </div>
      <div className="bmap-phase-rows">
        {phase.rows.map((row, ri) => (
          <Row key={ri} cards={row} bidi={!!phase.bidi} />
        ))}
      </div>
    </section>
  );
}

function Row({ cards, bidi }: { cards: Card[]; bidi: boolean }) {
  return (
    <div className="bmap-row">
      {cards.map((card, i) => (
        <Fragment key={card.id}>
          <CardBox card={card} delay={i * 100} />
          {i < cards.length - 1 ? (bidi ? <ArrowBidi /> : <Arrow />) : null}
        </Fragment>
      ))}
    </div>
  );
}

function CardBox({ card, delay }: { card: Card; delay: number }) {
  return (
    <div
      className={`bmap-card${card.hero ? " bmap-agent" : ""}`}
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="bmap-card-head">
        <div className="bmap-icon">{card.icon}</div>
        <div className="bmap-badge">{card.badge}</div>
      </div>
      <div className="bmap-card-title">{card.title}</div>
      <div className="bmap-card-sub">{card.sub}</div>
    </div>
  );
}

function Arrow() {
  return (
    <svg className="bmap-arrow" viewBox="0 0 36 12" width="36" height="12" aria-hidden>
      <line x1="0" y1="6" x2="28" y2="6" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 3" />
      <polyline points="26,2 32,6 26,10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function ArrowBidi() {
  return (
    <svg className="bmap-arrow" viewBox="0 0 48 14" width="48" height="14" aria-hidden>
      <line x1="6" y1="7" x2="42" y2="7" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 3" />
      <polyline points="40,3 46,7 40,11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <polyline points="8,3 2,7 8,11"    fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/* ──────────── Inline SVG icons ──────────── */

function svgWrap(children: JSX.Element) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function IconPhone() {
  return svgWrap(
    <path d="M5 4h3l2 5-2.5 1.5a11 11 0 0 0 6 6L15 14l5 2v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z" />,
  );
}
function IconHeadset() {
  return svgWrap(
    <>
      <path d="M4 13a8 8 0 0 1 16 0v4a2 2 0 0 1-2 2h-1v-6h3" />
      <path d="M4 13v4a2 2 0 0 0 2 2h1v-6H4" />
    </>,
  );
}
function IconEar() {
  return svgWrap(
    <>
      <path d="M8 4a5 5 0 0 1 9 3v2c0 4-2.5 5-2.5 7A2.5 2.5 0 0 1 12 18.5" />
      <path d="M10 9a2 2 0 0 1 4 0" />
    </>,
  );
}
function IconAtom() {
  return svgWrap(
    <>
      <circle cx="12" cy="12" r="1.2" fill="currentColor" />
      <ellipse cx="12" cy="12" rx="9" ry="3.4" />
      <ellipse cx="12" cy="12" rx="9" ry="3.4" transform="rotate(60 12 12)" />
      <ellipse cx="12" cy="12" rx="9" ry="3.4" transform="rotate(120 12 12)" />
    </>,
  );
}
function IconBrain() {
  return svgWrap(
    <>
      <path d="M9 4.5a2.5 2.5 0 0 0-2.5 2.5v1A2.5 2.5 0 0 0 4 10.5v3A2.5 2.5 0 0 0 6.5 16v.5A2.5 2.5 0 0 0 9 19h.5V4.5H9Z" />
      <path d="M15 4.5a2.5 2.5 0 0 1 2.5 2.5v1A2.5 2.5 0 0 1 20 10.5v3A2.5 2.5 0 0 1 17.5 16v.5A2.5 2.5 0 0 1 15 19h-.5V4.5H15Z" />
    </>,
  );
}
function IconBook() {
  return svgWrap(
    <>
      <path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5Z" />
      <path d="M4 19a2 2 0 0 0 2 2h12" />
    </>,
  );
}
function IconCalculator() {
  return svgWrap(
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <rect x="7.5" y="5.5" width="9" height="3" rx="0.5" />
      <circle cx="9" cy="12.5" r="0.6" fill="currentColor" />
      <circle cx="12" cy="12.5" r="0.6" fill="currentColor" />
      <circle cx="15" cy="12.5" r="0.6" fill="currentColor" />
      <circle cx="9" cy="16" r="0.6" fill="currentColor" />
      <circle cx="12" cy="16" r="0.6" fill="currentColor" />
      <circle cx="15" cy="16" r="0.6" fill="currentColor" />
    </>,
  );
}
function IconDatabase() {
  return svgWrap(
    <>
      <ellipse cx="12" cy="5" rx="7" ry="2.5" />
      <path d="M5 5v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V5" />
      <path d="M5 11v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-6" />
    </>,
  );
}
function IconBolt() {
  return svgWrap(
    <path d="M13 3 5 13h6l-2 8 8-10h-6l2-8Z" />,
  );
}
function IconStream() {
  return svgWrap(
    <>
      <path d="M3 7c3 0 3 2 6 2s3-2 6-2 3 2 6 2" />
      <path d="M3 12c3 0 3 2 6 2s3-2 6-2 3 2 6 2" />
      <path d="M3 17c3 0 3 2 6 2s3-2 6-2 3 2 6 2" />
    </>,
  );
}
function IconCloud() {
  return svgWrap(
    <path d="M7 18a4 4 0 0 1-.4-7.97A5 5 0 0 1 16 9.6a3.5 3.5 0 0 1 .5 6.94H7Z" />,
  );
}
function IconMonitor() {
  return svgWrap(
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8" />
      <path d="M12 16v4" />
    </>,
  );
}
