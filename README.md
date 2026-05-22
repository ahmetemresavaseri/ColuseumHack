# Atrium — AI Phone Agent for Cleaning Companies

> 24/7 multilingual voice agent on AWS. Built at AWS ColuseumHack 2026.
> Cleaning companies lose revenue from missed calls — crews are scrubbing floors, not picking up the phone. Atrium picks up.

---

## The Problem

Small and mid-size cleaning companies depend on inbound phone calls for new business. When a crew is on a job, the phone rings out — and a phone call from a potential customer is almost never a callback opportunity. Every missed call is lost revenue, often a recurring B2B contract worth thousands.

The status-quo workarounds (voicemail, answering services, multi-line PBX) are slow, expensive, monolingual, and offer no structured intake. They paper over the symptom; they don't fix the funnel.

---

## The Solution

Atrium is a **24/7 multilingual AI voice agent** that picks up every call instantly, captures a structured request, answers FAQs from the company's own knowledge base, and produces a live price estimate before the caller hangs up — fully on AWS, multi-tenant by design.

### What the caller experiences

1. The call is answered within ~1.4 s, in the company's voice persona, in the caller's language.
2. The agent asks six grounded questions: **When**, **What** (service type), **Area** (m² / sqft), **Rooms**, **Urgency**, **Email**.
3. If the caller interrupts with a question ("How much per m² for window cleaning?"), the agent retrieves the answer from the company's KB rather than inventing one — and cites the source on the dashboard.
4. As soon as enough details are known, the agent quotes a ballpark price out loud.
5. On hang-up, the booking record is durable in DynamoDB and the next steps (calendar invite, email follow-up, photo-based re-pricing, invoice) are scheduled.

> **Hackathon demo scope:** only the live voice flow + Brain (live pricing) + Live Call Wall is built. Calendar, email, photo loop, invoice, and PSTN (Amazon Connect) front-end are deliberately **out of scope** and shipped as roadmap.

> **Hackathon allow-list compliance:** the entry channel for the hackathon demo is a **"Call now" button** on the Wall that opens the laptop microphone via WebRTC and streams to the same agent. Amazon Connect (real PSTN) is on the roadmap, gated on the service landing on the event's allow-list. The voice stack is **Amazon Transcribe Streaming → Bedrock Claude Sonnet 4.6 → Amazon Polly Neural** — all three are explicitly on the allow-list.

### Live Demo

- **Live Call Wall URL:** _Amplify-hosted, set during build block 4_
- **"Call now" button** on the Wall opens a browser-mic call to the agent (no phone number needed for the demo)
- **Try it in:** German, English, Spanish (one tenant per language, each with its own Polly voice)
- **PSTN drop-in (roadmap):** Amazon Connect contact flow + KVS bridge plug into the same Input Agent — only the audio ingress changes

---

## Architecture (High-Level)

```
Browser mic (WebRTC) ──PCM frames over WS── API Gateway WebSocket
                                                       │
                                                       ▼
                                            Input Agent Lambda
                                                       │
                                  ┌────────────────────┼────────────────────┐
                                  ▼                    ▼                    ▼
                          Transcribe Streaming    Claude Sonnet 4.6     Polly Neural
                          (caller → text)         (turn + tool-use)     (text → MP3)
                                                       │
                          ┌────────────┬───────────────┼────────────────┐
                          ▼            ▼               ▼                ▼
                    Bedrock KB     DynamoDB       Brain Lambda      EventBridge
                    (S3 Vectors)   (Calls,        (Claude Sonnet     (CallStarted,
                                    Bookings,      4.6 + tools)       CallEnded)
                                    Companies,
                                    Connections)
                                        │
                                        │  DynamoDB Streams
                                        ▼
                                  Fan-out Lambda
                                  (DDB stream → WS push to all
                                   Wall connections per companyId)
                                        │
                                        ▼
                            API Gateway WebSocket (Wall channel)
                                        │
                                        ▼
                                  Live Call Wall
                                  (React + Amplify Hosting)
```

Everything happens **during** the call. There is no async post-call pipeline (deferred to roadmap). The Brain Lambda is invoked as a tool by the Input Agent mid-conversation, so the price appears on the dashboard while the caller is still talking.

For the full diagram and per-service rationale, see [the plan file](i-want-to-build-radiant-adleman.md).

---

## Modules

### 1. Input Agent (Voice)
The core module. A long-running Lambda that handles the audio WebSocket: streams caller PCM into **Amazon Transcribe Streaming**, runs each final transcript turn through **Bedrock Claude Sonnet 4.6** (Converse + tool-use), and pipes the reply back as MP3 frames via **Amazon Polly Neural**. It dispatches four tool calls:

- **`kb_lookup(question)`** — retrieves top-4 chunks from the company's Bedrock Knowledge Base; citations are surfaced on the Wall.
- **`save_slot(slot, value)`** — writes an extracted slot to DynamoDB; the Wall updates via DDB Streams → Fan-out Lambda → WebSocket.
- **`compute_price(slots)`** — invokes the Brain Lambda synchronously; the live price appears on the Wall within ~2 s.
- **`end_call(reason)`** — closes the WS session and emits a logging event to EventBridge.

The agent's persona, language, Polly voice, currency, and unit system are driven entirely by `Companies` config in DynamoDB — onboarding a new tenant is three API calls.

### 2. Brain (Live Pricing)
Claude Sonnet 4.6 with tool-use. Given the extracted slots, it picks one of the fixed service types (MOVE_OUT_CLEANING, OFFICE_CLEANING, CONSTRUCTION_CLEANING, WINDOW_CLEANING, FACILITY_MAINTENANCE, …), looks up the company's price matrix and available crews from DynamoDB, and returns a structured estimate. Called mid-call, not post-call — the wall fills in before hang-up.

### Roadmap (out of hackathon scope)
- **PSTN front door (Amazon Connect)** — claimed phone number → contact flow → KVS audio bridge into the same Input Agent. Pitched verbally as a drop-in; the Input Agent's Transcribe/Claude/Polly core stays unchanged.
- **Calendar module** — generates `.ics` invite for the caller and creates a Google Calendar event on the company's internal calendar.
- **Email module** — requests photos from the customer for jobs that need on-site inspection (`Brain.needsPhotos==true`).
- **Vision-based re-pricing** — Pixtral (or Claude Vision) processes the customer's photos and updates the booking's price estimate.
- **Invoice generation** — Claude drafts the legal text in the tenant's language; reportlab renders a PDF; SES delivers it.
- **Step Functions post-call pipeline** — orchestrates the above asynchronously after the caller hangs up.

These are intentionally deferred so the hackathon team can ship a flawless voice demo. The architecture leaves clean hooks (`EventBridge "CallEnded"`) for these subscribers to attach later.

---

## AWS Services Used

All services below are on the hackathon allow-list. Services that were originally in scope but are *not* on the allow-list (Amazon Connect, Bedrock Nova Sonic, AWS AppSync, Amazon SES, Kinesis Video Streams) have been pitched as roadmap items.

| Service | Role |
|---|---|
| **API Gateway WebSocket** ×2 | Audio channel (browser ↔ Input Agent) + Wall channel (browser ↔ Fan-out Lambda) |
| **Amazon Transcribe Streaming** | Caller speech → text (partials + finals); barge-in detection |
| **Bedrock Claude Sonnet 4.6** | Voice agent turn loop (Converse + tool-use), and Brain (crew/price) |
| **Amazon Polly Neural** | Agent text → multilingual MP3 frames played back through the audio WS |
| **Bedrock Knowledge Base** | Managed RAG over company PDFs |
| **S3 Vectors** | Vector store behind the KB |
| **Bedrock AgentCore Memory** | Per-caller short-term memory across turns |
| **Bedrock AgentCore Observability** | Tool-call traces for the Wall + jury slides |
| **DynamoDB** | `Calls`, `Bookings`, `Companies`, `Crews`, `Connections` |
| **DynamoDB Streams + Fan-out Lambda** | Pushes Wall updates to the WS in lieu of AppSync (zero polling) |
| **Lambda** | Input Agent (audio WS), Brain (tool-call), Wall Fan-out (DDB stream → WS) |
| **EventBridge** | Logging bus (hooks for post-hackathon modules) |
| **S3** | KB documents, optional call recordings, web build artifacts |
| **CloudFront + Amplify Hosting** | Live Call Wall frontend (incl. the "Call now" button) |
| **CloudWatch** | Latency traces — jury evidence |
| **Amazon Lex V2** (optional) | Deterministic "end call" intent shim if the WS-only path can't handle hang-up signalling reliably |

---

## RAG Knowledge Base

Each tenant gets a metadata-filtered slice of a shared Bedrock Knowledge Base.

- **Embedding model:** `cohere.embed-multilingual-v3` (1024 dim) — multilingual wins for DE/FR/ES/EN out of the box.
- **Vector store:** S3 Vectors (with OpenSearch Serverless as a fallback).
- **Chunking:** Hierarchical 300/1500 tokens (Bedrock KB default).
- **Tenant isolation:** `companyId` baked into chunk metadata at ingest; retrieval queries always filter by it.
- **Retrieval at call time:** Top-K=4, similarity threshold 0.55; chunks are passed to Claude Sonnet 4.6 as a `<context>` block in the tool-result before the next assistant turn. Citations are stored on the call turn record so the Wall can display them.

### Seed content (per tenant)
1. `Pricelist.pdf` — hourly rates, m²/sqft tariffs by service type
2. `ServiceCatalog.pdf` — what each service actually includes
3. `FAQ.md` — 10 common caller questions, written by the tenant

---

## Data Model (Multi-Tenant)

### DynamoDB (on-demand)

- **`Calls`** — live phone session state. `PK = callId`, `SK = turn#<seq>` for transcript chunks, `SK = meta` for summary. Attributes include `transcriptChunk`, `speaker`, `citations[]`.
- **`Bookings`** — durable booking record. `PK = bookingId` (UUIDv7), `SK = current`. Attributes: `slots{when, what, area, rooms, urgency, email}`, `brain{serviceType, crew, hours, price, currency, ...}`. GSI1 on `companyId + updatedAt` for tenant-scoped queries.
- **`Companies`** — tenant config. Attributes: `name`, `tenantSlug` (used as `?company=` on the Wall URL — no PSTN mapping in the demo), `priceMatrix`, `voicePersonaPrompt`, `pollyVoiceId`, `kbId`, `locale`, `currency`, `unitSystem`, `timezone`. All locale/currency/unit/voice handling is driven from here — the rest of the stack is tenant-agnostic.
- **`Crews`** — small seed table for the Brain to allocate from.
- **`Connections`** — active WebSocket connections (replaces what AppSync would have managed). PK = `connectionId`, GSI on `companyId#kind` so the Fan-out Lambda can address all Wall subscribers for a tenant in one query.

### S3 buckets
- `s3://atrium-kb-<acct>/companies/<companyId>/` — RAG source PDFs
- `s3://atrium-recordings-<acct>/calls/<callId>.wav` — Connect drops audio here for evaluation
- `s3://atrium-web-<acct>/` — built Wall artifact (Amplify Hosting can also serve directly)

---

## How to Run / Deploy

### Prerequisites
- AWS account with Bedrock model access enabled for **Claude Sonnet 4.6** and **Cohere embed-multilingual-v3** in `us-west-2`
- Amazon Transcribe and Amazon Polly available in `us-west-2` (default-on)
- Python 3.11+, Node 20+, AWS CDK v2

### Smoke test (verify your credentials and service access)
```bash
pip install -r requirements.txt
python smoke_test.py
```
Expected: `[OK]` for STS, Bedrock models, Claude Sonnet 4.6 (responds `'PONG'`), Polly (neural MP3 stream), and Transcribe Streaming (API surface + service reachable).

### Run the Live Call Wall locally (no AWS required)

The Wall ships with a mock mode so frontend work can proceed without any
deployed resources:

```bash
cd web
npm install
npm run dev
```

If `VITE_AUDIO_WS_URL`, `VITE_WALL_WS_URL`, or `VITE_WALL_API_URL` are unset,
the Wall renders empty panes. Hit **Simulate** to play a scripted Phase 1
call (transcript + slots + citation + brain estimate); hit **Clear** to
reset.

To exercise the Python spine end-to-end without AWS:

```bash
python scripts/simulate_call_events.py --pretty
```

That emits the same `WallEvent` JSON the Lambda fan-out would post; the
Python event-builder is at `lambdas/input_agent/events.py` and mirrors
`web/src/lib/types.ts`.

### Deploy the stack
```bash
make deploy
```
This runs the CDK app at `infrastructure/cdk_app.py` and provisions DDB (incl. `Connections`), the two API Gateway WebSocket APIs (audio + wall), the three Lambdas (Input Agent / Brain / Wall Fan-out), the Bedrock KB, and the Amplify Hosting build for the Wall.

### Seed a new tenant
1. Upload the tenant's PDFs to `s3://atrium-kb-<acct>/companies/<companyId>/`
2. Trigger a KB sync (`aws bedrock-agent start-ingestion-job ...`)
3. Insert a `Companies` row with persona, Polly voice id, locale, currency, price matrix, tenant slug

Target onboarding time: **under 10 minutes per tenant**.

---

## Evaluation

Live demo scenarios (in stage order):

1. **Happy path** — caller goes through all six questions, Wall fills in live, Brain returns price within 2 s.
2. **Mid-call FAQ** — caller interrupts with a pricing question; agent answers from KB, citation appears on the Wall.
3. **Out-of-scope question** — caller asks something not in the KB; agent honestly says "I don't have that information" instead of hallucinating. **This is feature, not bug.**
4. **Multilingual** — second call in EN or ES on a different tenant; persona switches, slots are still correct.
5. **Concurrent calls** (stretch) — two phones, two Wall lanes light up in parallel.

### Targets

| Metric | Target |
|---|---|
| Time to first audio response | < 1400 ms (p50), < 2000 ms (p95) |
| Slot extraction accuracy | ≥ 5/6 |
| KB retrieval precision@4 | ≥ 0.75 |
| Hallucination on out-of-scope | 0/10 |
| Brain tool-call latency | < 2 s |
| Multilingual slot accuracy (EN+ES) | ≥ 5/6 |

Evidence committed to `/eval/` (recordings, RAG eval CSV, hallucination test results, CloudWatch screenshots).

---

## Scaling

- **Multi-tenant from Day 1.** Every resource is keyed by `companyId`. New tenants don't need infra changes.
- **Horizontal:** DynamoDB on-demand + Lambda scale without provisioning. API Gateway WebSocket handles ~10k concurrent connections per endpoint (multi-endpoint or quota raise for more). Transcribe Streaming and Polly auto-scale.
- **Global:** `Companies` is replicable via DynamoDB Global Tables. Deploy regional stacks in `us-west-2` (Americas), `eu-central-1` (EU/CH/UK), `ap-southeast-2` (APAC). Route53 latency-based routing pins voice traffic to the closest region.
- **Compliance:** GDPR via EU region, SOC2 as stack default. HIPAA-eligible services only — unlocks medical-cleaning vertical.
- **Bottlenecks:** Bedrock TPM quota (mitigated by Provisioned Throughput on Sonnet 4.6); Transcribe / Polly concurrency (soft quotas, raisable). Foundation-model lock-in is avoided by the Bedrock Converse abstraction — Sonnet 4.6 → Opus 4.6 → Llama 4 Maverick swap is a config change.
- **PSTN drop-in:** Amazon Connect contact flow + KVS bridge plug into the same Input Agent — only the audio ingress changes.

---

## Repository Layout (target)

```
ColuseumHack/
├── README.md                       (this file)
├── requirements.txt
├── smoke_test.py                   (already proves Bedrock works)
├── Makefile                        (deploy / seed / test targets)
├── infrastructure/
│   └── cdk_app.py                  (single CDK app: API GW WS x2 / Lambdas / DDB / KB)
├── lambdas/
│   ├── input_agent/
│   │   └── handler.py              (Audio WS: Transcribe Streaming + Claude Sonnet 4.6 + Polly + tool dispatcher)
│   ├── brain/
│   │   └── handler.py              (Claude Sonnet 4.6 + crew/price tool-use loop)
│   └── wall_fanout/
│       └── handler.py              (DDB Streams → WS post_to_connection for the Wall)
├── web/
│   ├── package.json
│   └── src/
│       └── CallWall.tsx            (WS subscription + 4-pane layout + WebRTC "Call now" button)
├── kb_seed/
│   └── glanz-ag/
│       ├── Pricelist.pdf
│       ├── ServiceCatalog.pdf
│       └── FAQ.md
└── eval/
    ├── recordings/
    ├── rag_eval.csv
    ├── hallucination_test.md
    └── latency.md
```

---

## Team

Built at AWS ColuseumHack 2026.

## License

TBD.
