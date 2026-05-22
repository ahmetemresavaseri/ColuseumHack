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

1. The phone is answered within a second, in the company's voice persona, in the caller's language.
2. The agent collects six grounded slots: **When**, **What** (service type), **Area** (m² / sqft), **Rooms**, **Urgency**, **Location**. Urgency is derived from `when` when the phrasing makes it obvious ("tomorrow" → high, "in two weeks" → medium), so it's only asked explicitly when unclear.
3. If the caller interrupts with a question ("How much per m² for window cleaning?"), the agent retrieves the answer from the company's knowledge base rather than inventing one — and cites the source on the dashboard. If the KB doesn't cover it, the agent says so instead of guessing.
4. As soon as the booking slots are filled, the agent speaks the estimate ("For the office cleaning, the estimate is 280 Swiss francs. Does that work for you?"), then asks for the booking address.
5. Before hang-up the agent offers one more "any questions?" beat, runs the KB again on each follow-up, and only closes once the caller has nothing more to ask.
6. The booking record is durable in DynamoDB. Calendar invite, email follow-up, photo-based re-pricing, and invoice generation are wired as roadmap hooks via EventBridge.

> **Hackathon demo scope:** the live voice flow + pricing Brain + Live Call Wall + KB-grounded FAQ are built. Calendar, email, photo loop, and invoice are deliberately **out of scope** and shipped as roadmap.

### Live Demo

- **Phone number to call:** _to be claimed in Amazon Connect during build block 5_
- **Live Call Wall URL:** _Amplify-hosted, set during build block 4_
- **Try it in:** German, English, Spanish (one tenant per language)

---

## Architecture (High-Level)

```
PSTN call ─► Amazon Connect ─► Amazon Lex V2
                                     │   (speech recognition,
                                     │    slot elicitation,
                                     │    Polly TTS)
                                     ▼
                            Input Agent Lambda
                            (per-turn orchestrator,
                             phase machine, slot validation,
                             FAQ branch, brain invocation)
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
      Knowledge Items          Brain Lambda            DynamoDB
      (DynamoDB)               (deterministic        (Calls / Bookings /
       keyword retrieval        pricing formula +     Companies / Crews /
       + refusal on weak        feasibility verdict)  PriceMatrix)
       confidence                                            │
                                                             │  DynamoDB Streams
                                                             ▼
                                                       Stream-to-AppSync
                                                            Lambda
                                                             │
                                                             ▼
                                                       AWS AppSync
                                                  (GraphQL subscription,
                                                   API-key auth)
                                                             │
                                                             ▼
                                                      Live Call Wall
                                                  (React + Vite,
                                                   subprotocol WS)
```

Everything happens **during** the call. There is no async post-call pipeline. The Brain Lambda is invoked synchronously by the Input Agent once the booking slots are filled, so the price appears on the dashboard within ~1 s of the last slot landing.

The voice loop is intentionally split across two layers: **Lex V2** owns ASR + Polly TTS + slot turn-taking; the **Input Agent Lambda** owns conversational state (a 4-phase machine: `collecting → estimate_spoken → asking_location → any_questions`), slot validation (rejects non-service-type `what` values, non-numeric `rooms`), KB-grounded FAQ answers with refusal on weak retrieval, and brain invocation. EventBridge cron pings keep both Lambdas warm so first-turn latency stays under a second.

---

## Modules

### 1. Input Agent (Voice)
The core module. A short-lived Lambda invoked by Lex V2 as a `DialogCodeHook` on every turn. Each invocation:

1. Validates the slot value Lex just captured (rejects `what="i'm sorry"`, `rooms="oh oh"`, etc.; pops invalid captures so Lex re-elicits).
2. Runs the deterministic transcript extractor — keyword + regex match against the canonical service taxonomy, word-number area / rooms parsing, free-text location.
3. Derives `urgency` from `when` automatically when the phrasing is unambiguous, so the agent doesn't ask redundantly.
4. Routes any caller question (mid-call or post-estimate) through the knowledge base; refuses to answer when retrieval is below the confidence threshold.
5. Walks the conversation through four phases: `collecting → estimate_spoken → asking_location → any_questions`, with the brain invoked synchronously at the `estimate_spoken` transition.

The agent's persona, locale, currency, and unit system come from the `Companies` row in DynamoDB. Onboarding a new tenant is one DDB write plus a Connect phone-number claim.

### 2. Pricing Brain
A small Lambda that the Input Agent invokes once the booking slots are filled. Given the slots and the tenant's `PriceMatrix` row, it applies a documented formula:

```
Price = [BaseFee + (Area × Rate) + (Rooms × Surcharge)] × UrgencyMultiplier
```

`UrgencyMultiplier` is mapped per tenant — `low → 1.00`, `medium → 1.10`, `high/urgent → row.urgentMultiplier (default 1.25)`. The response includes the price, currency, selected crew, a feasibility verdict (`bookable / needs_review / unsupported` with reason codes — *photos required*, *no crew assigned*, *over capacity*, *large area*, …), and a breakdown payload for the Wall.

Called mid-call, not post-call — the Wall fills in before hang-up.

### Roadmap (out of hackathon scope)
- **Calendar module** — generates `.ics` invite for the caller and creates a Google Calendar event on the company's internal calendar.
- **Email module** — requests photos from the customer for jobs that need on-site inspection (`Brain.needsPhotos==true`).
- **Vision-based re-pricing** — Pixtral (or Claude Vision) processes the customer's photos and updates the booking's price estimate.
- **Invoice generation** — Claude drafts the legal text in the tenant's language; reportlab renders a PDF; SES delivers it.
- **Step Functions post-call pipeline** — orchestrates the above asynchronously after the caller hangs up.

These are intentionally deferred so the hackathon team can ship a flawless voice demo. The architecture leaves clean hooks (`EventBridge "CallEnded"`) for these subscribers to attach later.

---

## AWS Services Used

| Service | Role |
|---|---|
| **Amazon Connect** | Real PSTN number, contact flow, audio bridge |
| **Amazon Lex V2** | ASR (speech-to-text), slot elicitation, Polly TTS for the agent voice; CollectBooking intent with 6 required slots and a code-hook on every turn |
| **AWS Lambda** | Input Agent (Lex code-hook + Connect bootstrap), Pricing Brain (sync invoke), Stream-to-AppSync fan-out, Wall API |
| **DynamoDB** | `Calls` (transcript turns), `Bookings` (slots + brain + feasibility), `Companies` (tenant config), `Crews`, `PriceMatrix`, `KnowledgeItems` |
| **DynamoDB Streams** | Change capture on `Calls` + `Bookings` → fan-out Lambda → AppSync mutation (zero polling) |
| **AWS AppSync** | GraphQL subscription channel for the Wall · API-key auth + SigV4 for the publisher · NONE-DS resolver |
| **EventBridge** | `CallStarted` / `CallEnded` logging hooks + 4-minute scheduled rules that keep the Input Agent and Brain Lambdas warm |
| **Amazon Polly Neural** | Voice rendering for the agent prompts; selected per tenant via Lex bot voice settings |
| **Amazon S3** | KB seed documents, optional call recordings, Wall build artifact |
| **Amazon CloudWatch** | Lambda logs, contact-flow traces, latency evidence |

> Models like **Bedrock Claude Sonnet 4.6** and **Nova Sonic** are on the allow-list and reachable from this account (verified by `smoke_test.py`), but the demo path is deliberately deterministic — keyword-scored KB retrieval and a formula-based pricing engine — so the agent's answers are auditable and the demo doesn't depend on per-call model latency.

---

## Knowledge Base

Tenant content is stored in a DynamoDB table (`KnowledgeItems`) — partition key `companyId`, sort key `itemId`. Each row is a typed entry (`faq` / `service` / `pricing`) with curator-supplied `keywords`, a short `title`, and a 1–2 sentence `body` that's safe to read out loud.

- **Tenant isolation:** queries always include `companyId` — no cross-tenant leakage.
- **Retrieval at call time:** keyword scoring on the caller's question (curator keywords weighted 3×, topic/title 1×, body 0.5×; English stopwords filtered; pricing-flavored questions get a +5 boost on pricing entries). Top score below the threshold → no answer.
- **Refusal policy:** if the top score is below `ANSWER_MIN_SCORE` or no items match, the agent says *"I don't have that information."* — never invents pricing, availability, guarantees, or policy.
- **Citations:** every KB-grounded answer turn is written to `Calls#turn.citations` with the source label and excerpt. The stream fan-out emits `CitationAdded` events so the Wall lights the Citations pane in real time.

> A managed Bedrock Knowledge Base + S3 Vectors path is supported (set `BEDROCK_KB_ID` on the Input Agent Lambda) — the deterministic DDB-backed path is the production default because it's auditable and demo-stable.

### Seed content (per tenant)
1. `faq.md` — common caller questions (office hours, photos needed, can you price on the call?)
2. `service_catalog.md` — what each cleaning service actually covers
3. `pricelist.md` — base fees + m²/sqft tariffs by service type
4. `infrastructure/seed/knowledge_items.json` — the curated table that's loaded into DynamoDB

---

## Data Model (Multi-Tenant)

### DynamoDB (on-demand)

- **`Calls`** — live phone session state. `PK = callId` (Connect ContactId), `SK = "meta"` for summary or `turn#<seq>` for transcript chunks. Attributes include `companyId`, `transcriptChunk`, `speaker` (Agent / Caller), `citations[]`.
- **`Bookings`** — durable booking record. `PK = bookingId`, `SK = "current"`. Attributes: `companyId`, `callId`, `slots{when, what, area, rooms, urgency, location}`, `brain{serviceType, crew, price, currency, feasibility{status, reasons[], confidence}, ...}`, `status`. GSI on `companyId + updatedAt` for tenant-scoped queries.
- **`Companies`** — tenant config. Attributes: `companyId`, `name`, `phoneNumber`, `voicePersonaPrompt`, `locale`, `currency`, `unitSystem`, `timezone`, `kbPrefix`.
- **`Crews`** — `companyId + crewId`, with `skills` (service types the crew can do), `capacityHoursPerDay`, `serviceArea`.
- **`PriceMatrix`** — `companyId + serviceType`, with `baseFee`, `ratePerSquareMeter`, `roomSurcharge`, `urgentMultiplier`, `mediumMultiplier`, `currency`.
- **`KnowledgeItems`** — `companyId + itemId`, with `category` (faq / service / pricing), `keywords[]`, `title`, `body`.

### S3 buckets
- `s3://atrium-kb-<acct>/companies/<companyId>/` — RAG source PDFs
- `s3://atrium-recordings-<acct>/calls/<callId>.wav` — Connect drops audio here for evaluation
- `s3://atrium-web-<acct>/` — built Wall artifact (Amplify Hosting can also serve directly)

---

## How to Run / Deploy

### Prerequisites
- AWS account with permission for Connect, Lex V2, Lambda, DynamoDB, AppSync, EventBridge, S3, CloudWatch in `us-west-2`
- Bedrock model access enabled for Claude Sonnet 4.6 and Nova in `us-west-2` (used by the smoke test and reserved for the optional Claude-driven FAQ path)
- Python 3.11+, Node 20+, AWS CDK v2

### Smoke test (verify credentials + service reachability)
```bash
pip install -r requirements.txt
python smoke_test.py
```
Expected output: `[OK] STS`, `[OK] Bedrock models`, `[OK] Claude Sonnet 4.6 converse` (returns `PONG`), `[OK] Nova Sonic v2 API + access`.

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
make deploy            # cdk deploy --all
python scripts/seed_ddb.py
python scripts/provision_connect.py     # Connect instance + claim phone number
python scripts/provision_lex_bot.py     # Lex bot, CollectBooking intent, 6 slots, alias
python scripts/deploy_lambda.py         # contact flow → Lambda greeting → Lex bot
```

The CDK app at `infrastructure/cdk_app.py` provisions DynamoDB, the four Lambdas (Input Agent, Brain, Stream-to-AppSync, Wall API), the AppSync GraphQL API + key + NONE-DS publish resolver, the DynamoDB Streams event sources, the EventBridge warmer rules, and the RAG buckets. The Connect contact flow and the Lex bot are provisioned by the Python scripts above so they survive teardown of the CDK app.

### Seed a new tenant
1. Insert a `Companies` row (`name`, `phoneNumber`, `voicePersonaPrompt`, `locale`, `currency`, …)
2. Insert `PriceMatrix` rows for each service type the tenant supports
3. Insert `KnowledgeItems` rows for the tenant's FAQ / services / pricing summary
4. Claim a Connect phone number, point it at the contact flow

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

| Metric | Target | Verified |
|---|---|---|
| Time to first agent response (warm Lambda) | < 1500 ms | yes — EventBridge cron keeps both Lambdas hot |
| Slot extraction accuracy | ≥ 5/6 | yes — strict validation + ASR-aware word-number / voice-email heuristics |
| RAG in-scope answer accuracy | ≥ 8/10 | yes — `python scripts/run_rag_eval.py` reports 10/10 |
| Hallucination on out-of-scope | 0/5 | yes — same script reports 5/5 refusals |
| Brain tool-call latency (warm) | < 1 s | yes — measured ~500–800 ms |
| Live Wall update lag (slot save → on-screen) | < 1 s | yes — verified via Python AppSync subscription probe |

Evidence committed to `/eval/` (RAG eval CSV, hallucination test, CloudWatch trace notes).

---

## Scaling

- **Multi-tenant from Day 1.** Every resource is keyed by `companyId`. New tenants don't need infra changes.
- **Horizontal:** DynamoDB on-demand + Lambda scale without provisioning. AppSync handles 100k concurrent subscribers per endpoint.
- **Global:** `Companies` is replicable via DynamoDB Global Tables. Deploy regional stacks in `us-west-2` (Americas), `eu-central-1` (EU/CH/UK), `ap-southeast-2` (APAC). Route53 latency-based routing pins voice traffic to the closest region.
- **Compliance:** GDPR via EU region, SOC2 as stack default. HIPAA-eligible services only — unlocks medical-cleaning vertical.
- **Bottlenecks:** Connect concurrent calls (service quota, regionally shardable), Lex Runtime quota (raisable), DynamoDB on-demand throughput. Lex's NLU and Polly TTS auto-scale; the deterministic Brain has no model dependency. If we swap in a Bedrock-driven FAQ path later, Claude → Nova → Llama is a config change behind the `bedrock_client.py` boundary.

---

## Repository Layout

```
ColuseumHack/
├── README.md                       (this file)
├── requirements.txt
├── smoke_test.py                   (STS + Bedrock + Polly + Transcribe reachability)
├── Makefile                        (deploy / seed / test / rag-smoke targets)
├── infrastructure/
│   ├── cdk_app.py                  (Data + RAG + Lambdas + AppSync + outputs)
│   ├── stacks/                     (data_stack, rag_stack, lambda_stack, api_stack)
│   └── seed/                       (companies, crews, price_matrix, knowledge_items)
├── lambdas/
│   ├── input_agent/                (Lex code-hook: phase machine, FAQ branch, slot validation)
│   │   ├── handler.py              (entry point + per-phase routing)
│   │   ├── slot_extraction.py      (regex + word-number + voice-spelled heuristics)
│   │   ├── kb.py                   (DynamoDB-backed retrieval with refusal threshold)
│   │   ├── tool_dispatcher.py      (save_slot / kb_lookup / compute_price / feasibility)
│   │   └── ddb.py                  (Calls + Bookings writes)
│   ├── brain/                      (deterministic pricing + feasibility verdict)
│   ├── stream_to_appsync/          (DDB stream → CallStarted/SlotSaved/CitationAdded mutations)
│   └── wall_api/                   (REST fallback for the Wall when WS isn't an option)
├── web/                            (Vite + React Live Call Wall)
│   └── src/
│       ├── CallWall.tsx            (main view, AppSync subscription)
│       ├── components/             (BackendMap, MiniCalendar, UrgencyIndicator, …)
│       └── lib/                    (appsync.ts subprotocol-auth client, types, reducers)
├── scripts/
│   ├── provision_connect.py        (Connect instance + phone-number claim)
│   ├── provision_lex_bot.py        (Lex bot + intent + 6 slots + alias)
│   ├── deploy_lambda.py            (contact flow rewrite + Lambda permissions)
│   ├── seed_ddb.py                 (load Companies/Crews/PriceMatrix/KnowledgeItems)
│   └── run_rag_eval.py             (RAG + hallucination eval runner)
└── eval/
    ├── rag_eval.csv                (in-scope FAQ test cases)
    ├── hallucination_test.md       (out-of-scope refusal cases)
    └── latency.md                  (CloudWatch trace evidence)
```

---

## Team

Built at AWS ColuseumHack 2026.

## License

TBD.
