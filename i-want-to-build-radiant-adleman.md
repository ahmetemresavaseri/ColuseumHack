# Atrium — AI Voice Phone Agent (Voice-Demo) — Hackathon Plan
**Project codename:** *atrium* | **Event:** AWS ColuseumHack | **Region:** `us-west-2` (Bedrock parity)
**Demo priority:** *It has to land live.* **100% voice-demo focus** — everything else is mocked or moved to roadmap.

---

## 0. Context & TL;DR

**Problem:** Cleaning companies lose revenue from missed inbound calls — crews are scrubbing floors, not picking up the phone.

**Solution (demo scope):** **Atrium** — a **24/7 multilingual AI phone agent** on AWS that:
1. Picks up calls (real PSTN number via Amazon Connect)
2. Captures a structured 6-slot request (When, What, area, Rooms, Urgency, Email)
3. Answers FAQs via RAG over company-specific Knowledge Base
4. Surfaces crew + price live during the call (Brain visible on the wall while caller is still speaking)
5. Visualizes everything in realtime on the **Live Call Wall**

**Explicitly out of demo scope (roadmap):** calendar sync, email module, photo loop, invoice PDF. Mentioned verbally in the pitch as "next step" but **not built** — the voice flow must be flawless.

**Demo flow:** Juror calls the number, watches the Wall light up live: transcript, extracted slots, Brain output (price + crew), and KB-grounded FAQ answers with citations. Optional second call in another language proves multilingual capability.

---

## 1. System Architecture (Voice-Demo-Focused)

```
                                EXTERNAL
                                    |
                          +---------v---------+
                          |  Customer Phone   |
                          |  (Juror calls a   |
                          |   real number)    |
                          +---------+---------+
                                    |
                                    |  PSTN
                                    v
                  +---------------------------------+
                  |   Amazon Connect (Contact Flow  |
                  |   + Phone Number)               |
                  +-----------------+---------------+
                                    |
                                    |  Audio stream (KVS)
                                    v
                  +---------------------------------+
                  |  Amazon Lex Bot                 |
                  |  (entry/exit shim)              |
                  +-----------------+---------------+
                                    |
                                    v
        +--------------------------------------------------+
        |   INPUT AGENT LAMBDA  (Python, long-running)     |
        |   - bidi stream: Connect <-> Nova Sonic          |
        |   - system prompt = persona + 6 questions + FAQ  |
        |   - tools: kb_lookup, save_slot, compute_price,  |
        |            end_call                              |
        +--+----------+---------+---------+----------+-----+
           |          |         |         |          |
           v          v         v         v          v
   +-------------+ +-----+ +---------+ +-------+ +----------+
   | Bedrock     | | KB  | | DynamoDB | | Brain | | Event-   |
   | Nova Sonic  | |+S3V | | Calls + | | Lambda| | Bridge   |
   | speech<->   | | RAG | | Slots   | | (Claude| | (logging |
   +-------------+ +-----+ +----+----+ | Sonnet | |  only)   |
                                |       | 4.6)  | +----------+
                                |       +---+---+
                                |           |
                              Streams       v
                                |       DDB write
                                v       (brain output)
                          +----------+      |
                          | AppSync  |<-----+
                          | GraphQL  |
                          | Sub      |
                          +----+-----+
                               |
                               v
                       +---------------+
                       | Live Call Wall|
                       | Amplify+React |
                       +---------------+
```

**Reading the diagram:** everything happens *during* the call. Once the caller hangs up, the show is over — no pipeline, no email, no invoice. Brain Lambda is called by the Input Agent as a tool as soon as enough slots are known, so the Wall shows the price while the caller is still talking.

---

## 2. Service-by-Service Breakdown (lean)

| Service | Role | Why |
|---|---|---|
| **Amazon Connect** | Owns phone number, contact flow, audio bridge | Only AWS service that gives you a real PSTN number in minutes |
| **Amazon Lex V2** | Entry-point bot, session holder, calls our fulfilment Lambda | Required hop between Connect and a custom voice LLM |
| **Bedrock Nova Sonic** | Speech-to-speech, multilingual, low latency | Saves the Transcribe → LLM → Polly tax (~1.5s → ~400ms) |
| **Bedrock Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6-...`) | Brain — crew/price calculation as a tool-call inside the voice loop | Already proven via `smoke_test.py`; strongest reasoning |
| **Bedrock Knowledge Base + S3 Vectors** | RAG over company PDFs (Pricelist, FAQ, Service Catalog) | Managed KB; S3 Vectors is brand-new and worth name-dropping |
| **Bedrock AgentCore Memory** | Per-caller short-term memory across turns | Hot AWS feature, clean state handler |
| **Bedrock AgentCore Observability** | Trace every tool call live for the Wall + slides | Visual jury candy + latency proof |
| **DynamoDB** | `Calls` (transcript), `Bookings` (slots+brain), `Companies` (tenant config) | Single-digit-ms, Streams trigger Wall updates |
| **DynamoDB Streams** | Push to AppSync for live UI | Zero polling, true realtime |
| **AWS AppSync** | Pushes call state to the Live Call Wall | Native subscriptions, less glue code than API GW WebSocket |
| **Lambda** | Input Agent (long-running bidi bridge), Brain (tool-call) | Pay-per-call |
| **EventBridge** | Logging bus only for `CallStarted`/`CallEnded` — no pipeline | Hooks for post-hackathon (email/invoice roadmap) |
| **S3** | `kb/` (company PDFs), `recordings/` (call audio), `web/` (Wall build) | Default |
| **S3 Vectors** | Vector store behind the KB | Name-drop in the pitch |
| **CloudFront + Amplify Hosting** | Live Call Wall frontend | Amplify gives instant HTTPS subdomain |
| **CloudWatch + AgentCore Observability** | Logs + traces for latency proof | Slide material |

**Removed from the stack** (vs original full vision): SES, Step Functions, SQS, Pixtral/Vision, Textract, Rekognition, Google Calendar API, Secrets Manager, `reportlab`. These return after the hackathon.

---

## 3. Module Breakdown (only 2 modules — Voice + Brain)

### 3.1 Input Agent (Voice) — **THE main module**
- **Backed by:** Connect → Lex → Lambda ↔ Nova Sonic + KB.Retrieve + Brain tool
- **Input:** Live audio stream (KVS from Connect)
- **Output:** Partial transcript chunks → DDB `Calls#<callId>#turn#<seq>`; slots → DDB `Bookings`; Brain output → DDB `Bookings.brain`
- **Multilingual by design:** persona, greeting, English/German/French/Spanish driven by `Companies.locale` + `Companies.voicePersonaPrompt`. Nova Sonic detects caller language in the first turn and switches.
- **System prompt contains:**
  1. Persona template from DDB per tenant (example DE: "You are Lara, receptionist at Glanz AG"; example US: "You are Sarah, the receptionist at Sparkle Cleaning")
  2. 6-slot form (When, What, Area, Rooms, Urgency, Email) with JSON-schema hints and unit conversion based on tenant locale
  3. Instruction: "As soon as you have `what` + `area`, call `compute_price` so the caller hears a ballpark before hanging up"
- **Tools:**
  - `kb_lookup(question)` → Bedrock KB.Retrieve, returns top-4 chunks
  - `save_slot(slot, value)` → DDB write, triggers Wall update via Streams
  - `compute_price(slots)` → invokes Brain Lambda sync, returns `{serviceType, crew, hours, price, currency}`, writes to `Bookings.brain`, triggers Wall update
  - `end_call(reason)` → closes session, emits EventBridge log

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
- Calendar sync (.ics + Google Calendar API)
- Email module (photo request via SES)
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
- Attrs: `name`, `phoneNumber` (→ Connect mapping), `priceMatrix{}`, `voicePersonaPrompt`, `kbId`, `locale` (de-CH, en-US, es-MX, ...), `currency`, `unitSystem` (metric/imperial), `timezone`

**`Crews`** — small static seed table for Brain tool-use (3-5 demo crews are enough)

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
   Connect ---audio--> Lex ---bidi--> Input Agent Lambda <--> Nova Sonic
                                          |  ^   ^
                                          |  |   |
                          (per turn)      |  |   |  (KB.Retrieve)
                                          v  |   |
                                       DDB Calls.put     KB query for FAQ
                                          |              |
                                          v              v
                                    DDB Streams      Bedrock KB
                                          |
                                          v
                              AppSync GraphQL Subscription
                                          |
                                          v
                                  Live Call Wall (browser)


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
                                    DDB Streams -> AppSync -> Wall
                                    (Brain pane fills in)


   On end_call tool:
   Input Agent Lambda -> EventBridge "CallEnded" (log only)
   Session closes. The demo-relevant loop ends here.
```

**Everything happens during the call.** No async pipeline, no Step Functions, no SQS — the demo loop is mouth-open until hang-up, all visible on the Wall in realtime.

---

## 6. Live Call Wall (Frontend) — **this IS the demo**

**Stack:** Vite + React + Tailwind + shadcn → built artifact → served via **Amplify Hosting** (instant HTTPS).

**Realtime mechanism:** **AWS AppSync** GraphQL subscriptions, Lambda resolver reads from DDB. DDB Streams trigger AppSync mutation → all subscribers update.
- *Why AppSync:* less plumbing than API GW WebSocket; native subscriptions; better demo story.
- *Fallback:* dumb 1Hz polling on an API GW endpoint if AppSync wiring eats too much time.

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

Wall panes (all live via AppSync):
- **Call status** (left): Active/Idle, duration, caller number
- **Transcript** (middle): Lara/Caller turns scroll in
- **Slots** (top-right): fills per turn
- **Brain** (mid-right): fills as soon as `compute_price` is called
- **KB Citations** (bottom-right): shows which chunks the agent just retrieved — **this is the RAG wow-moment**

---

## 7. RAG Setup (Exact)

- **Index:** S3 Vectors, dimension 1024 (Cohere multilingual v3)
- **Per-tenant filter:** metadata `companyId` baked into chunk metadata at ingest
- **Retrieval call** (from Input Agent as a Nova Sonic tool):
  - Top-K = 4, similarity threshold 0.55
  - Returns 4 chunks max ~800 tokens, fed into Nova Sonic as a `<context>` block before answer generation
  - Returned citations written to `Calls.turn.citations` → Wall displays them
- **Queries the agent makes mid-call:**
  - "How much is move-out cleaning per m²?"
  - "Do you offer end-of-tenancy with handover guarantee?"
  - "What's the cancellation window?"
  - "Do you service postal code XYZ?"
- **Why this matches the jury criterion:** demonstrably "meaningful RAG" — without it the agent invents prices. Citations on the Wall prove "the agent pulled this from chunk X".

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
| Time to first audio response | < 800 ms | Connect CloudWatch + AgentCore Trace |
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
- Bedrock Nova Sonic is regionally managed
- AppSync subscriptions scale to 100k concurrent clients per endpoint out-of-the-box

### Global multi-region
- `us-west-2` for the Americas, `eu-central-1` (Frankfurt) for EU/CH/UK, `ap-southeast-2` for APAC
- `Companies` table replicable via DynamoDB **Global Tables**
- Compliance: GDPR via EU region, SOC2 as stack default

### Bottlenecks and mitigations
- **Bedrock TPM quota:** Provisioned Throughput on top models, quota increase per ticket
- **Connect concurrent calls:** service quota, regionally shardable
- **Voice latency cross-region:** keep Bedrock inference in the same region as Connect
- **Foundation-model lock-in:** the Bedrock abstraction allows model swap (Claude → Nova → Llama) without code changes

---

## 10. Build Order / Time Budget (Voice-Demo only)

Assumption: **24 h hack, 3-4 people.** Numbers in hours.

| # | Block | Owner | Hours | Mock-first? |
|---|---|---|---|---|
| 0 | Repo skeleton, AWS profile pinned, smoke_test green for everyone | All | 0.5 | - |
| 1 | DDB tables + seed data (`Companies` with 1-2 tenants, `Crews`) | Backend A | 1 | - |
| 2 | KB: upload 3 PDFs (Pricelist, ServiceCatalog, FAQ), build index, smoke-test Retrieve | Backend B | 1.5 | - |
| 3 | **Brain Lambda standalone** (input = JSON slots, output = JSON brain result; tools mocked on DDB) | Backend A | 2 | Yes — Brain works before voice exists |
| 4 | **Live Call Wall v0** with hardcoded JSON from DDB; AppSync subscription works; all 4 panes (Status, Transcript, Slots, Brain, Citations) | Frontend | 4 | Yes — Wall works before agent does |
| 5 | Connect instance + claimed phone number + Lex bot + bridging Lambda (echo first, then hello-loop) | Backend B | 3 | - |
| 6 | Nova Sonic bidi integration in the Lambda; system prompt with persona + 6 questions | Backend A+B | 4 | - |
| 7 | Tools wired: `save_slot`, `kb_lookup` (with citations), `compute_price` (sync Brain call), `end_call` | Backend A | 3 | - |
| 8 | Multilingual test: second tenant with `locale=en-US`, different persona prompt | Backend B | 1.5 | - |
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
| Connect call drops or audio glitches on stage | Medium | **Browser fallback:** a "Call now" button on the Wall opens a WebRTC mic and pipes audio to the same Nova Sonic Lambda via API Gateway WebSocket. Identical UX, no PSTN. |
| Nova Sonic quality weak on Swiss-German | Medium | Force the agent to **answer in standard German** even when the caller speaks dialect; system prompt instruction. Fallback path: Transcribe + Claude + Polly Neural. |
| Bedrock throttling mid-demo | Low-Med | **Provisioned Throughput** on Sonnet 4.6 + Nova Sonic for the demo window; cache KB retrieval responses for the 5 staged questions |
| Wall doesn't update (AppSync wiring) | Medium | Polling fallback hidden behind `?poll=1` query string |
| Brain tool call is slow, voice response hangs | Medium | Brain is called *in parallel* to the voice loop (async fire); the answer is written to the Wall when ready — not blocked into the voice stream. Voice keeps replying normally. |
| KB retrieval hallucinates on an edge question | Medium | The out-of-scope test is part of the demo — if the agent honestly says "I don't know", that's a **plus** for the jury, not a bug |
| `us-west-2` latency from CH stage | Medium | Address it openly; show CloudWatch traces; explain Frankfurt roadmap |
| Multilingual switch fails (persona stays DE) | Medium | Per tenant a separate test number in the demo phone's speed dial, each with a fixed-locale persona |

---

## 13. Mapping to Jury Criteria (sanity-check)

| Criterion | Where in the plan it's evident |
|---|---|
| Clear problem + target user | Sec. 0, 11 |
| Meaningful RAG | Sec. 7 + citations on Wall + anti-hallucination test (Sec. 8 scenario 3) |
| Multimodal processing | Audio-as-modality: Nova Sonic speech-to-speech, real-time bidirectional |
| Agentic workflows | Input Agent makes 4 tool calls (kb_lookup, save_slot, compute_price, end_call); Brain is its own sub-agent with its own tools (Sec. 3.1, 3.2) |
| Working demo | Sec. 6 (Wall), Sec. 8 (5 live scenarios), Sec. 12 (fallbacks) |
| Technical creativity | Nova Sonic bidi + S3 Vectors + AgentCore Memory + live Brain call mid-conversation |
| Practical feasibility | Sec. 10 (realistic build order), Sec. 11 (all services GA) |
| Evaluation / evidence | Sec. 8 (5 metrics incl. anti-hallucination, recordings, CSV) |
| AWS usage + scaling | Sec. 2 (service table), Sec. 9 (multi-tenant, multi-region) |
| Multilingual / global-ready | Sec. 3.1 (multilingual voice), Sec. 4 (`Companies.locale`), Sec. 8 scenario 4 |

---

## 14. Critical Files for Implementation

When the team starts writing code, these are the files that exist first:

- `c:/SideQuest/ColuseumHack/infrastructure/cdk_app.py` — single CDK app: Connect/Lex/Lambdas/DDB/AppSync/KB
- `c:/SideQuest/ColuseumHack/lambdas/input_agent/handler.py` — Nova Sonic bidi bridge + tool dispatcher (kb_lookup, save_slot, compute_price, end_call)
- `c:/SideQuest/ColuseumHack/lambdas/brain/handler.py` — Claude Sonnet 4.6 with tool-use loop for crew/price
- `c:/SideQuest/ColuseumHack/web/src/CallWall.tsx` — AppSync subscription + 4-pane layout (Status, Transcript, Slots, Brain, Citations)

---

## 15. Verification (Demo-Day End-to-End Test)

Walk through before the pitch — every point must be green:

1. **Smoke test** runs (`python smoke_test.py` → Bedrock reachable, Sonnet 4.6 responds)
2. **Call the Connect number** from your own phone → persona greets in default language within 1s
3. **Open the Wall** at the Amplify URL → empty 4-pane view visible, AppSync subscription connected (browser console green)
4. **Run a test call:** all 6 slots fill in the Wall live while you speak
5. **Mid-call FAQ:** "How much is window cleaning?" → answer with price from KB, **citation appears in the Wall**
6. **Out-of-scope question:** "Do you also wash cars?" → agent says "I don't know" instead of hallucinating
7. **Brain pane:** as soon as `what` + `area` are extracted, price + crew appear in the Wall (within 2s)
8. **Hang up:** Wall shows "Call ended", transcript stays visible — no pipeline runs (by design)
9. **Multilingual test:** second call in EN — persona switches automatically, slots are extracted correctly

If any point fails → activate the corresponding fallback from Sec. 12.
