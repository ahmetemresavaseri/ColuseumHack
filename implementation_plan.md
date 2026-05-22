# Atrium Implementation Plan

This plan aligns with the current repo scaffold and `README.md`. The goal is not
to build only a caller-facing voice agent. Atrium should become a live phone
agent plus an operational workflow system for cleaning companies.

The core design principle is:

> Nova Sonic owns the live conversation and decides when to call tools. Lambdas,
> DynamoDB, Bedrock KB, AppSync, and downstream services perform the durable and
> deterministic work.

Nova Sonic should feel like the receptionist. The backend should behave like the
operations team.

## Current Repo Boundaries

The repo is split so the team can work in parallel:

| Area | Path | Owner focus |
|---|---|---|
| Voice agent | `lambdas/input_agent/` | Connect event handling, Nova Sonic stream, slot state, tool dispatch |
| Pricing brain | `lambdas/brain/` | Service taxonomy, price estimate, crew choice, feasibility result |
| Live wall | `web/` | React dashboard, AppSync subscription, demo controls |
| Wall/API stream | `lambdas/stream_to_appsync/`, `lambdas/wall_api/` | Push call updates to the dashboard |
| Data/RAG/API/Lambda infra | `infrastructure/stacks/` | CDK resources and deployable boundaries |
| Seed content | `infrastructure/seed/` | Tenant config, crews, price matrix, FAQ, pricelist, service catalog |
| Post-call operations | `lambdas/calendar_sync/`, `lambdas/email_outbox/`, `lambdas/vision_repricing/`, `lambdas/invoice_generator/` | Calendar, email, photos, repricing, invoice, notifications |
| Evaluation | `eval/` | RAG quality, latency, hallucination, slot accuracy |
| Scripts | `scripts/` | Deploy, seed, simulate, smoke checks |

## Target Workflow

```text
Caller
  -> Amazon Connect
  -> Lex/contact-flow shim
  -> Input Agent Lambda
  -> Nova Sonic bidirectional conversation
  -> Tool dispatcher
       -> save_slot -> DynamoDB
       -> kb_lookup -> Bedrock Knowledge Base
       -> compute_price -> Brain Lambda
       -> check_calendar -> calendar_sync
       -> feasibility_assessment -> Brain Lambda / operations rules
       -> request_pictures -> email_outbox
       -> create_booking -> DynamoDB
       -> notify_driver / notify_cleaner -> operations notification tool
       -> send_email -> email_outbox / SES
       -> end_call -> EventBridge
  -> DynamoDB Streams
  -> stream_to_appsync
  -> Live Call Wall
```

The live call path should keep latency low. Work that affects the spoken
conversation must return quickly. Work that can happen after hang-up should be
emitted as events and processed asynchronously.

## Tool Contract

The Input Agent exposes tool calls to Nova Sonic through
`lambdas/input_agent/tool_dispatcher.py`.

### Live-call tools

These tools are needed during the conversation:

| Tool | Purpose | Implementation owner |
|---|---|---|
| `save_slot(slot, value)` | Persist extracted call details | `input_agent/ddb.py` |
| `kb_lookup(question)` | Answer company FAQs with citations | `input_agent/kb.py` |
| `compute_price(slots)` | Produce a live estimate | `lambdas/brain/` |
| `check_calendar(date, duration, serviceType)` | Check likely availability | `lambdas/calendar_sync/` |
| `feasibility_assessment(slots)` | Decide whether job is bookable, risky, or needs review | `lambdas/brain/` plus operations rules |
| `create_booking(slots, brainOutput)` | Create durable booking record | DynamoDB helper in `input_agent/ddb.py` |
| `end_call(reason)` | Close the live session and emit follow-up event | Input Agent + EventBridge |

### Operational tools

These tools should also be implemented, but they can run after the call unless
the demo requires them live:

| Tool | Purpose | Implementation owner |
|---|---|---|
| `send_email(email, template, payload)` | Send confirmation or follow-up | `lambdas/email_outbox/` |
| `request_pictures(email, bookingId)` | Ask customer for photos when needed | `lambdas/email_outbox/` |
| `vision_reprice(bookingId, imageRefs)` | Reprice from submitted images | `lambdas/vision_repricing/` |
| `notify_driver(bookingId)` | Notify logistics/driver role | notification helper or future `lambdas/notifications/` |
| `notify_cleaner(bookingId)` | Notify assigned crew | notification helper or future `lambdas/notifications/` |
| `generate_invoice(bookingId)` | Draft and render invoice | `lambdas/invoice_generator/` |

These are part of the product direction. They should be treated as real
workstreams, not discarded. The only distinction is whether they must block the
live conversation.

## Data Model

The README data model remains the source of truth:

- `Calls`: live call state and transcript turns.
- `Bookings`: durable booking record with slots, estimate, status, and follow-up flags.
- `Companies`: tenant config, locale, currency, persona, KB id.
- `Crews`: crew capacity and skills.
- `PriceMatrix`: seeded pricing rules by company and service type.

Recommended booking status flow:

```text
CALL_STARTED
  -> COLLECTING_SLOTS
  -> ESTIMATED
  -> AVAILABILITY_CHECKED
  -> FEASIBILITY_CHECKED
  -> BOOKING_CREATED
  -> FOLLOW_UP_PENDING
  -> CONFIRMED
```

For jobs that need photos:

```text
ESTIMATED
  -> PHOTOS_REQUESTED
  -> PHOTOS_RECEIVED
  -> REPRICED
  -> CONFIRMED
```

## Implementation Phases

### Phase 1: End-to-end live call spine

Goal: one real call produces visible state on the Live Call Wall.

Implement:

1. Connect invokes `lambdas/input_agent/handler.py`.
2. Input Agent initializes call state and company config.
3. Nova Sonic asks for required slots:
   - `when`
   - `what`
   - `area`
   - `rooms`
   - `urgency`
   - `email`
4. `save_slot` writes to DynamoDB.
5. DynamoDB Streams push changes through `stream_to_appsync`.
6. Web wall displays transcript, slots, and call status.

Success criteria:

- A teammate can call the Connect number.
- At least three slots appear on the wall during the call.
- The call can end cleanly with a durable `Bookings` record.

### Phase 2: Brain, price, and feasibility

Goal: the caller receives a useful estimate, and the business receives an
operational recommendation.

Implement:

1. Complete `lambdas/brain/pricing.py` using `PriceMatrix`.
2. Complete `lambdas/brain/service_taxonomy.py` for service classification.
3. Complete `lambdas/brain/ddb_tools.py` for crews and price data.
4. Add `feasibility_assessment`:
   - service supported or unsupported;
   - enough crew capacity or needs manual review;
   - photos required or not;
   - confidence and reason codes.
5. Return structured output to the wall and to the spoken agent.

Success criteria:

- `compute_price` returns price, currency, service type, and selected crew.
- `feasibility_assessment` returns `bookable`, `needs_review`, or `unsupported`.
- Nova Sonic can explain the estimate naturally without inventing details.

### Phase 3: RAG grounding

Goal: company-specific questions are answered from tenant content, visibly
grounded on the Live Call Wall, and refused when the tenant content does not
support an answer.

Demo behavior contract:

```text
Caller asks a company/policy/pricing question
  -> Input Agent classifies the turn as `faq_question`
  -> kb_lookup(question, companyId) retrieves tenant-scoped context
  -> agent answers in 1-2 short spoken sentences using only retrieved context
  -> citations are persisted with the agent turn or emitted as CitationAdded
  -> Live Call Wall shows the source + excerpt

If no useful tenant context is returned
  -> agent says it does not have that information
  -> no invented price, policy, service, or guarantee is spoken
```

Questions that should trigger `kb_lookup` during the live call:

- Pricing or rate questions: "How much is window cleaning per square meter?"
- Service inclusion questions: "Is handover-ready move-out cleaning included?"
- Scheduling policy questions: "Do you clean offices on weekends or evenings?"
- Photo/review questions: "Do you need pictures before confirming?"
- Service-area questions: "Do you cover this postcode?"
- Cancellation, guarantee, or tenant-policy questions.

Questions that should be refused unless the KB explicitly supports them:

- Services outside the catalog, such as car washing or appliance repair.
- Legal, medical, payment-card, or unrelated personal questions.
- Exact final quote commitments beyond the Brain estimate.

Implement:

1. Seed tenant knowledge with enough demo coverage:
   - `faq.md`
   - `pricelist.md`
   - `service_catalog.md`
   - `infrastructure/seed/knowledge_items.json` as the demo-safe fallback.
2. Configure Bedrock KB metadata with `companyId`.
3. Complete `kb_lookup` tenant filtering.
4. Keep a deterministic DynamoDB-backed `KnowledgeItems` lookup available for
   demo reliability if Bedrock KB setup or ingestion is flaky.
5. Add FAQ intent detection in the live turn path so the agent knows when to
   call `kb_lookup` instead of treating the utterance only as slot input.
6. Add an answer policy:
   - use only retrieved tenant context;
   - keep spoken answers short;
   - if retrieval is empty or low confidence, say "I don't have that
     information";
   - never invent prices, availability, guarantees, or service coverage.
7. Persist citations with the corresponding agent answer turn or emit
   `CitationAdded` events directly.
8. Surface citations on the Live Call Wall with a readable source label and
   short excerpt.
9. Add eval cases in `eval/rag_eval.csv` and `eval/hallucination_test.md`.

Demo script:

1. Caller asks: "How much is window cleaning per square meter?"
   - Expected: answer from pricelist/service content, citation appears.
2. Caller asks: "Is move-out cleaning handover-ready?"
   - Expected: answer from service catalog, citation appears.
3. Caller asks: "Can you clean offices on weekends?"
   - Expected: answer from FAQ/scheduling content, citation appears.
4. Caller asks: "Do you also wash cars?"
   - Expected: refusal: "I don't have that information."

Success criteria:

- FAQ questions trigger `kb_lookup` during the real or fallback live call path.
- In-scope FAQ answers are spoken from retrieved tenant context only.
- Citations appear on the Live Call Wall within the same demo beat as the
  answer.
- Out-of-scope questions are refused instead of hallucinated.
- RAG eval has at least 8 passing in-scope cases.
- Hallucination eval has 5/5 passing out-of-scope refusals.

### Phase 4: Calendar and booking creation

Goal: Atrium can create a believable booking workflow, not just a quote.

Implement:

1. Build `check_calendar` in `lambdas/calendar_sync/`.
2. Start with a mock calendar data source if real calendar integration is too slow.
3. Add `create_booking` to persist the final booking.
4. Record selected crew, preferred time, estimate, feasibility, and customer email.
5. Show booking state on the wall.

Success criteria:

- The agent can say whether the requested time is likely available.
- Booking status changes from `ESTIMATED` to `BOOKING_CREATED`.
- The wall shows availability and booking state.

### Phase 5: Email, photos, and notifications

Goal: Atrium triggers operational follow-up after the call.

Implement:

1. `send_email` in `lambdas/email_outbox/`.
2. `request_pictures` when `needsPhotos == true`.
3. `notify_cleaner` and `notify_driver` as notification functions.
4. Store all outgoing messages in a durable outbox table or booking event list.
5. Use SES or a hackathon-safe mock sender depending on AWS readiness.

Success criteria:

- Customer receives or mock-receives confirmation.
- Photo request is generated for jobs requiring visual review.
- Crew/driver notification payloads are created from booking data.

### Phase 6: Vision repricing and invoice

Goal: show the system continues after the phone call.

Implement:

1. `vision_reprice` in `lambdas/vision_repricing/`.
2. Update the booking estimate from uploaded image references.
3. `generate_invoice` in `lambdas/invoice_generator/`.
4. Add generated invoice metadata back to the booking.

Success criteria:

- A booking can move from `PHOTOS_REQUESTED` to `REPRICED`.
- Invoice metadata exists for a confirmed booking.

## Hackathon Demo Path

The fastest convincing demo is:

1. Caller phones the agent.
2. Agent collects slots in German or English.
3. Wall updates live.
4. Caller asks one FAQ; wall shows citation.
5. Agent computes price.
6. Agent checks availability.
7. Agent says whether the job is feasible.
8. Booking is created.
9. Confirmation/photo-request/crew-notification events appear on the wall or in logs.

This demonstrates more than a voice agent. It demonstrates a live operations
workflow coordinated by voice.

## Parallel Work Assignments

Recommended split:

| Person/agent | Files | Deliverable |
|---|---|---|
| Voice | `lambdas/input_agent/` | Nova Sonic stream, tool calls, slot state |
| Brain | `lambdas/brain/` | Pricing, crews, feasibility |
| Infra | `infrastructure/stacks/` | DDB, Lambdas, AppSync, KB, EventBridge |
| Wall | `web/`, `lambdas/stream_to_appsync/`, `lambdas/wall_api/` | Live dashboard |
| Ops | `lambdas/calendar_sync/`, `lambdas/email_outbox/` | Availability, email, photo request, notifications |
| Eval/demo | `eval/`, `scripts/` | Seed data, simulated events, scoring, demo checklist |

## Guardrails

- Keep deterministic business actions out of prompts.
- Prefer tool calls for price, booking, email, availability, and notifications.
- Keep the live call path fast; move non-blocking work to EventBridge/outbox.
- Always persist before speaking a final commitment to the caller.
- Treat missing KB data as unknown, not as a prompt-writing opportunity.
- Keep tenant-specific behavior in `Companies`, seed files, and KB metadata.

## What To Implement Next

The most useful next implementation task is:

1. Complete `create_booking` and booking status updates in `lambdas/input_agent/ddb.py`.
2. Add `check_calendar` as a mock-backed tool in `lambdas/calendar_sync/`.
3. Add `feasibility_assessment` to `lambdas/brain/`.
4. Extend `tool_dispatcher.py` to call those tools.
5. Extend the wall mock/types to display booking, availability, and follow-up events.

That keeps the plan ambitious while still giving the hackathon team a path to a
working demo.
