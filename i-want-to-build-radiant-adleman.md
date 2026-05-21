# Atrium — AI Voice Phone Agent (Voice-Demo) — Hackathon Plan
**Project codename:** *atrium* | **Event:** AWS ColuseumHack | **Region:** `us-west-2` (Bedrock parity)
**Demo priority:** *It has to land live.* **100% voice-demo focus** — everything else is mocked or moved to roadmap.

> **Service allow-list compliance (2026-05-22):** Atrium uses ONLY services on the hackathon allow-list.
> What got swapped out vs. the original vision (Connect / Nova Sonic / AppSync are *not* on the list):
> - **Front door:** Browser-WebRTC + API Gateway WebSocket (not Amazon Connect / PSTN)
> - **Voice loop:** Amazon Transcribe Streaming → Bedrock Claude Sonnet 4.6 → Amazon Polly Neural (not Bedrock Nova Sonic)
> - **Live Wall realtime:** API Gateway WebSocket + DynamoDB Streams via Lambda fan-out (not AppSync)
> - **PSTN as a roadmap item** — pitched verbally as "Connect drop-in once it's on the list".

---

## 0. Context & TL;DR

**Problem:** Cleaning companies lose revenue from missed inbound calls — crews are scrubbing floors, not picking up the phone.

**Solution (demo scope):** **Atrium** — a **24/7 multilingual AI voice agent** on AWS that:
1. Answers a call in a browser (juror clicks "Call now" on the Live Call Wall) — production-path is PSTN, swapped here only because Amazon Connect is not on the event allow-list
2. Captures a structured 6-slot request (When, What, area, Rooms, Urgency, Email)
3. Answers FAQs via RAG over company-specific Knowledge Base
4. Surfaces crew + price live during the call (Brain visible on the wall while caller is still speaking)
5. Visualizes everything in realtime on the **Live Call Wall**

**Explicitly out of demo scope (roadmap):** calendar sync, email module, photo loop, invoice PDF, PSTN/Amazon Connect drop-in. Mentioned verbally in the pitch as "next step" but **not built** — the voice flow must be flawless.

**Demo flow:** Juror clicks "Call now" on the Wall, talks into the browser mic, watches the Wall light up live: transcript, extracted slots, Brain output (price + crew), and KB-grounded FAQ answers with citations. Optional second call in another language (different tenant) proves multilingual capability.

---

## 1. System Architecture (Voice-Demo-Focused)

```
                                EXTERNAL
                                    |
                          +---------v---------+
                          |  Juror's browser  |
                          |  ("Call now" btn  |
                          |   on the Wall)    |
                          +---------+---------+
                                    |
                                    |  WebRTC mic capture
                                    |  (PCM frames over WS)
                                    v
                  +---------------------------------+
                  |  API Gateway WebSocket          |
                  |  $connect / $message / $disco   |
                  +-----------------+---------------+
                                    |
                                    v
        +--------------------------------------------------+
        |   INPUT AGENT LAMBDA  (Python, long-running)     |
        |   - Transcribe Streaming bidi (PCM in / partial   |
        |     + final transcripts out)                      |
        |   - On final transcript turn:                     |
        |       Claude Sonnet 4.6 (tool-use via Converse)   |
        |   - Polly Neural TTS for agent replies            |
        |     (audio frames pushed back through WS)         |
        |   - tools: kb_lookup, save_slot, compute_price,   |
        |            end_call                               |
        +--+----------+---------+---------+----------+-----+
           |          |         |         |          |
           v          v         v         v          v
   +-------------+ +-----+ +----------+ +-------+ +----------+
   | Bedrock     | | KB  | | DynamoDB | | Brain | | Event-   |
   | Transcribe  | |+S3V | | Calls +  | | Lambda| | Bridge   |
   | + Claude +  | | RAG | | Bookings | |(Claude| | (logging |
   | Polly       | |     | | Companies| | Sonnet| |  only)   |
   +-------------+ +-----+ +----+-----+ | 4.6)  | +----------+
                                |       +---+---+
                              Streams       |
                                |           v
                                v       DDB write
                          +-----------+      |
                          | Fan-out   |<-----+
                          | Lambda    |
                          | (DDB Strm |
                          |  -> WS    |
                          |  postToCon)
                          +-----+-----+
                                |
                                |  WebSocket push (one connection per
                                |   open Wall browser, looked up by
                                |   companyId index in DDB Connections)
                                v
                        +---------------+
                        | Live Call Wall|
                        | Amplify+React |
                        +---------------+
```

**Reading the diagram:** everything happens *during* the call. Once the caller hangs up, the show is over — no pipeline, no email, no invoice. Brain Lambda is called by the Input Agent as a tool as soon as enough slots are known, so the Wall shows the price while the caller is still talking.

**Two WebSocket APIs (intentional):** one carries caller-audio (the WebRTC mic / TTS playback channel); the other carries Wall-updates (transcript chunks, slots, brain output). Both terminate on API Gateway WebSocket, both back-ended by Lambda. No PSTN, no AppSync, no Connect-Lex hop — every box in the diagram is on the hackathon allow-list.

---

## 2. Service-by-Service Breakdown (lean, allow-list-compliant)

| Service | Role | Why |
|---|---|---|
| **Browser WebRTC (frontend)** | Captures juror mic, plays Polly TTS back | Replaces PSTN front door; Connect is not on the allow-list |
| **API Gateway WebSocket** (×2) | (a) Audio channel browser ↔ Input Agent; (b) Wall channel browser ↔ Fan-out Lambda | Bi-directional, native AWS, on allow-list |
| **Amazon Transcribe Streaming** | Caller speech → text (partials + finals) | On the allow-list; cancellable on barge-in |
| **Bedrock Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`) | Voice agent's brain (turn-by-turn responses + tool-use); also powers the Brain Lambda | Already proven via `smoke_test.py`; only Anthropic model on the allow-list aside from Opus 4.6 |
| **Amazon Polly Neural** | Agent text → MP3 frames, multilingual voices | On the allow-list; `Joanna` (en-US), `Vicki` (de-DE), `Lupe` (es-US) — one voice per tenant |
| **Bedrock Knowledge Base + S3 Vectors** | RAG over company PDFs (Pricelist, FAQ, Service Catalog) | KB is part of Bedrock; S3 Vectors is explicitly on the allow-list |
| **Bedrock AgentCore Memory** | Per-caller short-term memory across turns | AgentCore is explicitly on the allow-list |
| **Bedrock AgentCore Observability** | Trace every tool call live for the Wall + slides | Visual jury candy + latency proof |
| **DynamoDB** | `Calls` (transcript), `Bookings` (slots+brain), `Companies` (tenant config), `Connections` (active Wall WS sessions) | Single-digit-ms, Streams trigger Wall updates |
| **DynamoDB Streams + Fan-out Lambda** | Stream → small Lambda that pushes to all WS connections subscribed to that `companyId` | Replaces AppSync (not on allow-list); ~30 lines of code |
| **Lambda** | Input Agent (audio ↔ Transcribe ↔ Claude ↔ Polly), Brain (tool-call sub-agent), Fan-out (DDB → WS) | Pay-per-call; long-running Input Agent invoked via WS |
| **EventBridge** | Logging bus only for `CallStarted`/`CallEnded` — no pipeline | Hooks for post-hackathon (email/invoice roadmap) |
| **S3** | `kb/` (company PDFs), `recordings/` (full-call audio, optional), `web/` (Wall build) | Default |
| **S3 Vectors** | Vector store behind the KB | On the allow-list |
| **CloudFront + Amplify Hosting** | Live Call Wall frontend (incl. the WebRTC "Call now" button) | Amplify gives instant HTTPS subdomain |
| **CloudWatch + AgentCore Observability** | Logs + traces for latency proof | Slide material |
| **Amazon Lex V2** | Optional intent shim if we need a deterministic "end the call" intent | Lex V2 is on the allow-list; only used if WS-only flow can't reliably handle hang-up signalling |

**Explicitly NOT in this stack** (not on the hackathon allow-list — flagged so nobody reaches for them mid-hack): Amazon Connect, Q in Connect, Bedrock Nova Sonic, AppSync, Amazon SES, Kinesis Video Streams.

**Also out of scope but allow-listed (would be fine to add later):** SES isn't on the list; Step Functions, SQS, Pixtral/Vision, Textract, Rekognition are — but stay out to keep the demo scope small. Google Calendar API and `reportlab` are non-AWS and out of demo scope regardless.

---

## 3. Module Breakdown (only 2 modules — Voice + Brain)

### 3.1 Input Agent (Voice) — **THE main module**
- **Backed by:** Browser WebRTC → API GW WebSocket → Lambda ↔ (Transcribe Streaming, Claude Sonnet 4.6 Converse + tool-use, Polly Neural) + KB.Retrieve + Brain tool
- **Input:** PCM16 audio frames (~20 ms each) over WebSocket, captured by the browser via `getUserMedia` + `AudioWorklet`
- **Output (back over the same WS):** Polly MP3 frames + control messages (`agent_speaking_start/end`, `final_transcript`). Side effects: partial transcript chunks → DDB `Calls#<callId>#turn#<seq>`; slots → DDB `Bookings`; Brain output → DDB `Bookings.brain`
- **Multilingual by design:** persona, greeting, English/German/French/Spanish driven by `Companies.locale` + `Companies.voicePersonaPrompt`. Transcribe Streaming is invoked with `LanguageCode=<tenant>`; Polly voice + Claude system-prompt language are picked from the same `Companies` row.
- **Turn loop (Lambda pseudo-code):**
  1. WS `$connect` resolves `companyId` from query string, writes a `Connections` row, opens a Transcribe Streaming bidi stream, sends the greeting via Polly
  2. On each WS `audio` message → forward PCM to Transcribe
  3. On Transcribe `final` event → call Claude Sonnet 4.6 `Converse` with tool-use (system prompt = persona + 6-slot schema + KB-grounding instruction)
  4. Claude → either text reply (Polly → WS) or tool call (`kb_lookup`/`save_slot`/`compute_price`/`end_call`)
  5. On `end_call` tool or WS `$disconnect` → close Transcribe, emit EventBridge `CallEnded`
- **System prompt contains:**
  1. Persona template from DDB per tenant (example DE: "You are Lara, receptionist at Glanz AG"; example US: "You are Sarah, the receptionist at Sparkle Cleaning")
  2. 6-slot form (When, What, Area, Rooms, Urgency, Email) with JSON-schema hints and unit conversion based on tenant locale
  3. Instruction: "As soon as you have `what` + `area`, call `compute_price` so the caller hears a ballpark before hanging up"
- **Tools:**
  - `kb_lookup(question)` → Bedrock KB.Retrieve, returns top-4 chunks
  - `save_slot(slot, value)` → DDB write, triggers Wall update via Streams → Fan-out Lambda → WS
  - `compute_price(slots)` → invokes Brain Lambda sync, returns `{serviceType, crew, hours, price, currency}`, writes to `Bookings.brain`, triggers Wall update
  - `end_call(reason)` → closes WS session, emits EventBridge log

### 3.2 Brain (tool-call from Input Agent, no separate endpoint)
- **Backed by:** Lambda + Claude Sonnet 4.6 with tool-use
- **Trigger:** invoked by the Input Agent during the call (not async after)
- **Internal tools Brain can call:**
  - `get_available_crews(date, duration)` → DDB `Crews`
  - `get_price_matrix(serviceType)` → DDB `Companies.priceMatrix`
- **Output:**
  ```
  {
    serviceType: "MOVE_OUT_CLEANING" | "OFFICE_CLEANING" | "CONSTRUCTION_CLEANING" |
                 "WINDOW_CLEANING" | "FACILITY_MAINTENANCE" | ...,
    crewId: "crew_03",
    estimatedHours: 4.5,
    price: 540,
    currency: "CHF",
    priceBreakdown: {...}
  }
  ```
- **Service-type taxonomy is fixed** (list provided by user, mapped to English keys) — Brain must pick exactly one.

### Intentionally NOT in demo scope (mention as "next step" in the pitch)
- **PSTN / Amazon Connect drop-in** (once Connect lands on the allow-list — the architecture is designed so the WebRTC channel can be swapped for a Connect contact-flow + KVS bridge without changing the Input Agent's Transcribe/Claude/Polly core)
- Calendar sync (.ics + Google Calendar API)
- Email module (photo request via SES — also not on the allow-list)
- Vision-based re-pricing (Pixtral on customer photos)
- Invoice generation (PDF via reportlab)
- Step Functions post-call pipeline

---

## 4. Data Model (Minimal)

### DynamoDB Tables (on-demand)

**`Calls`** — live phone session state
- PK: `callId` (str) | SK: `turn#<seq>` for transcript turns, `meta` for summary
- Attrs: `startedAt`, `endedAt`, `phoneNumber`, `companyId`, `transcriptChunk`, `speaker(agent|caller)`, `citations[]`

**`Bookings`** — slots + Brain output
- PK: `bookingId` (UUIDv7) | SK: `current`
- Attrs: `companyId`, `callId`, `slots{when, what, area, rooms, urgency, email}`, `brain{serviceType, crew, hours, price, currency, ...}`, `updatedAt`
- GSI1: `companyId` + `updatedAt` (Wall query: "today's bookings for company X")

**`Companies`** — tenant config
- PK: `companyId` | SK: `profile`
- Attrs: `name`, `tenantSlug` (used as `?company=` on the Wall URL — no PSTN mapping in this demo), `priceMatrix{}`, `voicePersonaPrompt`, `pollyVoiceId` (e.g. `Joanna`, `Vicki`, `Lupe`), `kbId`, `locale` (de-CH, en-US, es-MX, ...), `currency`, `unitSystem` (metric/imperial), `timezone`

**`Crews`** — small static seed table for Brain tool-use (3-5 demo crews are enough)

**`Connections`** — active WebSocket connections (replaces what AppSync would have managed for us)
- PK: `connectionId` (WS connection id from API GW) | SK: `meta`
- Attrs: `companyId`, `kind` (`audio` | `wall`), `callId` (set on audio connections), `connectedAt`, `ttl` (auto-clean idle rows)
- GSI1: `companyId#kind` → list of subscribers per tenant, used by Fan-out Lambda

### S3 Buckets (only 3)
- `s3://atrium-kb-<acct>/companies/<companyId>/...pdf` — RAG source
- `s3://atrium-recordings-<acct>/calls/<callId>.wav` — Connect dumps here (for eval)
- `s3://atrium-web-<acct>/` — Wall build (or direct via Amplify Hosting)

### Knowledge Base Ingestion Format
- One KB with metadata filter on `companyId` (scales better than KB-per-tenant)
- Source: `s3://atrium-kb-<acct>/companies/<companyId>/`
- Chunking: **Hierarchical 300/1500 tokens** (Bedrock KB default)
- Embedding model: `cohere.embed-multilingual-v3`
- Vector store: **S3 Vectors** (fallback OpenSearch Serverless)
- Demo seed files for the hackathon company:
  1. `Pricelist.pdf` (hourly rates, m²/sqft tariffs)
  2. `ServiceCatalog.pdf` (what's included in each service)
  3. `FAQ.md` (10 typical caller questions — will catch most mid-call FAQs)

---

## 5. Call Flow (no Step Functions needed)

```
   Browser mic --PCM frames over WS--> API GW WS --> Input Agent Lambda
                                                          |
                                          (Transcribe Streaming bidi)
                                                          |
                                          (final transcript per turn)
                                                          v
                                       DDB Calls.put     KB query for FAQ
                                          |              |
                                          v              v
                                    DDB Streams      Bedrock KB Retrieve
                                          |
                                          v
                                  Fan-out Lambda (DDB stream consumer)
                                          |
                                          | apigw.post_to_connection(...)
                                          v
                                  Live Call Wall (browser)


   Per turn, after the final transcript:
   Input Agent Lambda --(Converse + tool-use)--> Claude Sonnet 4.6
                                          |
                                          | text reply
                                          v
                                Polly Neural (synthesize_speech)
                                          |
                                          | MP3 frames
                                          v
                              API GW WS --> Browser (audio playback)


   Tool: compute_price (called mid-call when enough slots are known):
   Input Agent Lambda --(sync invoke)--> Brain Lambda
                                          |
                                          v
                                    Claude Sonnet 4.6 + DDB tools
                                          |
                                          v
                                    DDB Bookings.brain.put
                                          |
                                          v
                          DDB Streams -> Fan-out Lambda -> WS -> Wall
                                    (Brain pane fills in)


   On end_call tool OR WS $disconnect:
   Input Agent Lambda -> EventBridge "CallEnded" (log only)
   Transcribe stream closed, Polly idle, Connections row deleted.
   The demo-relevant loop ends here.
```

**Everything happens during the call.** No async pipeline, no Step Functions, no SQS — the demo loop is mouth-open until hang-up, all visible on the Wall in realtime.

---

## 6. Live Call Wall (Frontend) — **this IS the demo**

**Stack:** Vite + React + Tailwind + shadcn → built artifact → served via **Amplify Hosting** (instant HTTPS).

**Realtime mechanism:** **API Gateway WebSocket** (the Wall channel — separate from the audio WS used by the "Call now" button). On `$connect`, the browser passes `?company=<tenantSlug>`; the Lambda authorizer resolves it to `companyId` and writes a row to `Connections`. DDB Streams on `Calls` and `Bookings` invoke a small **Fan-out Lambda** that does `apigatewaymanagementapi.post_to_connection(...)` to every active Wall connection for the matching `companyId`.
- *Why API GW WebSocket:* AppSync is not on the allow-list; API GW WebSocket is. ~30 lines of fan-out code total.
- *Fallback:* dumb 1Hz polling on a REST endpoint if the WS wiring eats too much time.

**The "Call now" button** opens a *second* WebSocket (the audio channel) to the Input Agent Lambda, with PCM16 capture via `AudioWorklet`. Polly MP3 replies are buffered into an `AudioContext` and played back. The juror talks into their laptop mic — no PSTN, no phone, but the demo on-screen is indistinguishable from a real call (we still display a "+41 79 xxx" placeholder for theatre).

**Layout:**
```
+------------------------------------------------------------+
| ATRIUM   |  Live Call Wall                       [Demo Co] |
+----------+--------------------------+-----------------------+
|          |                          |  EXTRACTED SLOTS      |
| CALL     |  Lara: Hello, Glanz AG  |  When:  2026-05-23   |
| ACTIVE   |  Caller: I need a...    |  What:  Move-Out      |
|  00:42   |  Lara: How many m2?     |  Area:  85 m2         |
|          |  Caller: 85 sqm         |  Rooms: 3.5           |
| caller:  |  Caller: Window prices? |  Urg:   Medium        |
| +41 79.. |  Lara: [KB] CHF 12/m2   |  Mail:  a@b.ch        |
|          |  ...                    +-----------------------+
|          |                          |  BRAIN OUTPUT (LIVE)  |
|          |                          |  Service: MOVE-OUT    |
|          |                          |  Crew:    Team 3      |
|          |                          |  Hours:   4.5         |
|          |                          |  Price:   CHF 540     |
|          |                          +-----------------------+
|          |                          |  KB CITATIONS         |
|          |                          |  [1] Pricelist p.3    |
|          |                          |  [2] FAQ #7           |
+----------+--------------------------+-----------------------+
```

Wall panes (all live via the Wall WebSocket):
- **Call status** (left): Active/Idle, duration, caller number
- **Transcript** (middle): Lara/Caller turns scroll in
- **Slots** (top-right): fills per turn
- **Brain** (mid-right): fills as soon as `compute_price` is called
- **KB Citations** (bottom-right): shows which chunks the agent just retrieved — **this is the RAG wow-moment**

---

## 7. RAG Setup (Exact)

- **Index:** S3 Vectors, dimension 1024 (Cohere multilingual v3)
- **Per-tenant filter:** metadata `companyId` baked into chunk metadata at ingest
- **Retrieval call** (from Input Agent as a Claude Sonnet 4.6 tool):
  - Top-K = 4, similarity threshold 0.55
  - Returns 4 chunks max ~800 tokens, fed into Claude as a `<context>` block in the tool-result before the next assistant turn
  - Returned citations written to `Calls.turn.citations` → Wall displays them
- **Queries the agent makes mid-call:**
  - "How much is move-out cleaning per m²?"
  - "Do you offer end-of-tenancy with handover guarantee?"
  - "What's the cancellation window?"
  - "Do you service postal code XYZ?"
- **Why this matches the jury criterion:** demonstrably "meaningful RAG" — without it the agent invents prices. Citations on the Wall prove "the agent pulled this from chunk X".

> **Latency note:** because we lost Nova Sonic's speech-to-speech advantage, the round-trip is Transcribe (~200-400 ms after end-of-speech) + Claude (~400-800 ms first token) + Polly (~100-200 ms first byte) ≈ **800-1400 ms time-to-first-audio**. Mitigations: stream Claude tokens; start Polly on the first sentence boundary; pre-warm Lambda; keep a "thinking…" earcon ready for any gap > 1.2 s.

---

## 8. Evaluation Plan (Voice-Focused)

### Live scenarios (run in this order on stage)
1. **Happy path call** — Juror calls, Lara greets in <1s, 6 slots fill in the Wall live during the call, Brain pane shows price/crew as soon as enough slots are known.
2. **Mid-call FAQ** — Juror interrupts with "How much per m² for window cleaning?" — agent retrieves from KB, answers correctly, **Wall shows the KB citation live**.
3. **Out-of-scope question** — Juror asks something NOT in the KB ("Do you also wash cars?") — agent honestly says "I don't have that information" instead of hallucinating. **Proves anti-hallucination via RAG.**
4. **Multilingual** — Second call in EN or ES. Same number, different tenant with `locale="en-US"` — persona switches, slots are extracted correctly.
5. **Concurrent calls** (stretch) — Two phones call at once, two Wall lanes light up.

### Measured metrics (show as a slide)
| Metric | Target | How measured |
|---|---|---|
| Time to first audio response | < 1400 ms (p50), < 2000 ms (p95) | WS round-trip timestamps + AgentCore Trace |
| Slot extraction accuracy | >= 5/6 on test calls | Manual rubric on 10 recorded calls |
| KB retrieval precision@4 | >= 0.75 | Hand-graded 20 Q/A pairs |
| Hallucination rate on out-of-scope questions | 0/10 | Hand-checked on 10 trick questions |
| Brain tool-call latency (compute_price) | < 2 s | CloudWatch |
| Multilingual slot accuracy (EN+ES) | >= 5/6 | Manual rubric on 5 test calls per language |

### Recorded evidence committed to the repo
- `/eval/recordings/call_de_01.wav` ... `call_de_10.wav`, `call_en_01.wav` ... `call_es_05.wav`
- `/eval/rag_eval.csv` (20 Q/A with expected vs retrieved)
- `/eval/hallucination_test.md` (10 out-of-scope questions with answers)
- `/eval/latency.md` (CloudWatch trace screenshots)

---

## 9. Scaling Story (Technical — brief)

### Multi-tenant from Day 1
- Everything keyed by `companyId`: own Connect number, own KB (metadata filter), own voice persona, own locale
- Onboarding a new tenant = 3 API calls (Connect number claim, KB ingest, Companies row insert)

### Horizontal scaling
- DynamoDB on-demand + Lambda scale horizontally without provisioning
- Bedrock (Claude + KB), Transcribe Streaming, and Polly are all regionally managed and auto-scale
- API Gateway WebSocket handles up to ~10k concurrent connections per endpoint out-of-the-box (multi-endpoint or quota raise for more)

### Global multi-region
- `us-west-2` for the Americas, `eu-central-1` (Frankfurt) for EU/CH/UK, `ap-southeast-2` for APAC
- `Companies` table replicable via DynamoDB **Global Tables**
- Compliance: GDPR via EU region, SOC2 as stack default

### Bottlenecks and mitigations
- **Bedrock TPM quota:** Provisioned Throughput on Sonnet 4.6, quota increase per ticket
- **Transcribe / Polly concurrency:** soft service quotas, raisable per ticket
- **Voice latency cross-region:** keep Bedrock + Transcribe + Polly in the same region as the WS endpoint
- **Foundation-model lock-in:** the Bedrock Converse abstraction allows model swap (Sonnet 4.6 → Opus 4.6 → Llama 4 Maverick — all on the allow-list) without code changes
- **PSTN drop-in (post-hackathon):** Amazon Connect contact flow + KVS bridge plug into the same Input Agent — only the audio ingress changes

---

## 10. Build Order / Time Budget (Voice-Demo only)

Assumption: **24 h hack, 3-4 people.** Numbers in hours.

| # | Block | Owner | Hours | Mock-first? |
|---|---|---|---|---|
| 0 | Repo skeleton, AWS profile pinned, smoke_test green for everyone (STS + Bedrock + Polly + Transcribe) | All | 0.5 | - |
| 1 | DDB tables + seed data (`Companies` with 1-2 tenants, `Crews`, `Connections`) | Backend A | 1 | - |
| 2 | KB: upload 3 PDFs (Pricelist, ServiceCatalog, FAQ), build index, smoke-test Retrieve | Backend B | 1.5 | - |
| 3 | **Brain Lambda standalone** (input = JSON slots, output = JSON brain result; tools mocked on DDB) | Backend A | 2 | Yes — Brain works before voice exists |
| 4 | **Live Call Wall v0** + Wall WebSocket (API GW WS) + Fan-out Lambda; all 4 panes (Status, Transcript, Slots, Brain, Citations) driven by hardcoded DDB rows | Frontend + Backend A | 4 | Yes — Wall works before agent does |
| 5 | Audio WebSocket (API GW WS) + Input Agent Lambda skeleton: PCM in → Transcribe Streaming → text echo back via Polly | Backend B | 3 | - |
| 6 | Claude Sonnet 4.6 Converse integration in the Input Agent; system prompt with persona + 6 questions; barge-in handling | Backend A+B | 4 | - |
| 7 | Tools wired: `save_slot`, `kb_lookup` (with citations), `compute_price` (sync Brain call), `end_call` | Backend A | 3 | - |
| 8 | Multilingual test: second tenant with `locale=en-US`, different Polly voice + persona prompt | Backend B | 1.5 | - |
| 9 | End-to-end rehearsal x3, latency measurement, fallbacks tested, Wall animations polished | All | 3 | - |
| 10 | Slides + 60-sec script + scaling-story polish | Lead | 1.5 | - |

**Total: ~25 h** — radical scope cut buys breathing room vs. earlier plans.

**Mock-first principle:** Brain + Wall ship first with hardcoded JSON before voice hangs off them. The Wall is the integration-test surface.

---

## 11. README Skeleton (sections only — fill in during the hack)

```
# Atrium — AI Phone Agent for Cleaning Companies
> 24/7 multilingual Voice Agent on AWS. Built at AWS ColuseumHack 2026.

## Problem
   - Cleaning companies lose revenue from missed calls
   - Crews are scrubbing floors, not picking up the phone
## Solution (90-second pitch)
   - Voice agent, multilingual, multi-tenant, RAG-grounded
## Live Demo
   - Phone number to call (any language)
   - URL of the Live Call Wall
## Architecture
   - System diagram
   - Service map
   - Multi-tenant by design
## The Voice Agent
   - System prompt strategy
   - 6-slot form
   - Tool use (kb_lookup, save_slot, compute_price, end_call)
   - Multilingual handling
## RAG Knowledge Base
   - Per-tenant, multilingual (Cohere embed-multilingual-v3)
   - S3 Vectors
   - Citation display
## Brain (Live Pricing)
   - How Brain is called mid-call, not post-call
## Data Model (Multi-tenant)
## How to run locally / deploy to AWS
   - Prereqs (AWS account, Bedrock model access, Connect instance)
   - `make deploy`
   - Seeding a new tenant in <10 minutes
## Evaluation
   - Latency
   - Slot accuracy (across 3 languages)
   - RAG eval
   - Anti-hallucination eval
## Scaling
   - Multi-tenant from Day 1
   - Global multi-region
## Roadmap (out of hackathon scope)
   - Calendar sync (.ics + Google Calendar)
   - Email module (photo request)
   - Vision-based re-pricing (Pixtral)
   - Invoice PDF generation
## Team
## License
```

---

## 12. Risks & Mocks (the demo-day survival kit)

| Risk | Likelihood | Mitigation / Fallback |
|---|---|---|
| Browser mic permission denied on stage laptop | Low | Pre-grant permission in the demo browser profile; second laptop pre-warmed; fall back to a pre-recorded call video as last resort |
| WebRTC audio glitches on conference Wi-Fi | Medium | Use the speaker's hotspot; PCM frame size = 20 ms (small) to mask packet loss; client-side jitter buffer ~100 ms |
| Swiss-German recognition weak in Transcribe | Medium | Force the agent to **answer in standard German** even when the caller speaks dialect; system prompt instruction. Use `LanguageCode=de-CH` if available, else `de-DE` |
| Bedrock throttling mid-demo | Low-Med | **Provisioned Throughput** on Sonnet 4.6 for the demo window; cache KB retrieval responses for the 5 staged questions |
| Wall doesn't update (WS fan-out wiring) | Medium | Polling fallback hidden behind `?poll=1` query string; the Wall poll a `GET /calls/active` REST endpoint at 1 Hz |
| Brain tool call is slow, voice response hangs | Medium | Brain is called *in parallel* to the voice loop (async fire); the answer is written to the Wall when ready — not blocked into the voice stream. Voice keeps replying normally. |
| KB retrieval hallucinates on an edge question | Medium | The out-of-scope test is part of the demo — if the agent honestly says "I don't know", that's a **plus** for the jury, not a bug |
| `us-west-2` latency from CH stage | Medium | Address it openly; show CloudWatch traces; explain Frankfurt roadmap |
| Multilingual switch fails (persona stays DE) | Medium | Per tenant a separate Wall URL in the demo browser's speed dial, each with a fixed-locale persona |
| **Allow-list discovery mid-demo** (jury notices a service we shouldn't be using) | **Resolved** | Stack audited 2026-05-22 — only allow-listed services in the build; Connect / Nova Sonic / AppSync / SES are explicitly pitched as **roadmap items**, not part of the demo |

---

## 13. Mapping to Jury Criteria (sanity-check)

| Criterion | Where in the plan it's evident |
|---|---|
| Clear problem + target user | Sec. 0, 11 |
| Meaningful RAG | Sec. 7 + citations on Wall + anti-hallucination test (Sec. 8 scenario 3) |
| Multimodal processing | Audio-as-modality: Transcribe Streaming (caller speech) + Polly Neural (agent voice), real-time bidirectional over WebRTC |
| Agentic workflows | Input Agent makes 4 tool calls (kb_lookup, save_slot, compute_price, end_call); Brain is its own sub-agent with its own tools (Sec. 3.1, 3.2) |
| Working demo | Sec. 6 (Wall), Sec. 8 (5 live scenarios), Sec. 12 (fallbacks) |
| Technical creativity | WebRTC ↔ Transcribe Streaming ↔ Claude Sonnet 4.6 with tool-use ↔ Polly Neural ↔ WebRTC (full duplex via API GW WS) + S3 Vectors + AgentCore Memory + live Brain call mid-conversation |
| Practical feasibility | Sec. 10 (realistic build order), Sec. 11 (all services GA) |
| Evaluation / evidence | Sec. 8 (5 metrics incl. anti-hallucination, recordings, CSV) |
| AWS usage + scaling | Sec. 2 (service table), Sec. 9 (multi-tenant, multi-region) |
| Multilingual / global-ready | Sec. 3.1 (multilingual voice), Sec. 4 (`Companies.locale`), Sec. 8 scenario 4 |

---

## 14. Critical Files for Implementation

When the team starts writing code, these are the files that exist first:

- `c:/SideQuest/ColuseumHack/infrastructure/cdk_app.py` — single CDK app: API GW WS (audio + wall), Lambdas, DDB, KB
- `c:/SideQuest/ColuseumHack/lambdas/input_agent/handler.py` — Audio-WS handler: Transcribe Streaming bridge + Claude Converse loop + Polly TTS + tool dispatcher (kb_lookup, save_slot, compute_price, end_call)
- `c:/SideQuest/ColuseumHack/lambdas/brain/handler.py` — Claude Sonnet 4.6 with tool-use loop for crew/price
- `c:/SideQuest/ColuseumHack/lambdas/wall_fanout/handler.py` — DDB Streams consumer that pushes updates to all Wall WS connections for the matching `companyId`
- `c:/SideQuest/ColuseumHack/web/src/CallWall.tsx` — WS subscription + 4-pane layout (Status, Transcript, Slots, Brain, Citations) + "Call now" WebRTC mic capture button

---

## 15. Verification (Demo-Day End-to-End Test)

Walk through before the pitch — every point must be green:

1. **Smoke test** runs (`python smoke_test.py` → STS + Bedrock + Sonnet 4.6 + Polly + Transcribe all green)
2. **Open the Wall** at the Amplify URL → empty 4-pane view visible, Wall WebSocket connected (browser console green)
3. **Click "Call now"** → browser grants mic, audio WS opens, persona greets in tenant's default language within ~1.4 s
4. **Run a test call:** all 6 slots fill in the Wall live while you speak
5. **Mid-call FAQ:** "How much is window cleaning?" → answer with price from KB, **citation appears in the Wall**
6. **Out-of-scope question:** "Do you also wash cars?" → agent says "I don't know" instead of hallucinating
7. **Brain pane:** as soon as `what` + `area` are extracted, price + crew appear in the Wall (within 2s)
8. **End the call** (agent fires `end_call` or juror clicks "Hang up"): Wall shows "Call ended", transcript stays visible — no pipeline runs (by design)
9. **Multilingual test:** open a second tab on the `en-US` tenant's Wall URL, click "Call now" → persona switches, Polly voice changes, slots are extracted correctly

If any point fails → activate the corresponding fallback from Sec. 12.
