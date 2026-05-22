# Amazon Connect + Lex Voice Path

The PSTN voice path is:

    Caller -> Connect phone number -> Contact flow -> Lex V2 GetCustomerInput
                                                      |
                                                      v
                                              FallbackIntent
                                                      |
                                                      v
                                              Lambda CodeHook (atrium-input-agent)
                                                      |
                                                      v
                                              Bedrock Claude Sonnet 4.6 (Converse + tools)

Lex handles ASR + TTS natively (Polly Neural voice picked from the bot locale's
`voiceSettings`). The Lambda CodeHook is invoked on every caller utterance and
returns the spoken reply text. The conversation history lives in
Lex `sessionAttributes.messages` (JSON-serialized Bedrock Converse messages list).

The Lambda returns `dialogAction.type=ElicitIntent` to keep listening, or
`Close` (set when the model calls the `end_call` tool) to hand control back to
the Connect contact flow, which then disconnects.

## Deploy sequence

Run from repo root, in this order:

    python scripts/deploy_lambda.py        # creates/updates atrium-input-agent + grants lex.amazonaws.com invoke perm
    python scripts/provision_lex.py        # creates atrium-receptionist bot, en_US locale, atrium-live alias
    python scripts/provision_connect.py    # associates the Lex bot with the Connect instance + installs the contact flow

State files written under `scripts/`:

  - `.deploy_state.json` — Lambda ARN + role
  - `.lex_state.json`    — bot ID, alias ID, alias ARN
  - `.connect_state.json`— instance, contact flow, phone number, bot alias ARN

After all three succeed, call the number printed by `provision_connect.py` and
talk to Sarah.

## Local smoke test (no phone call)

Before paying for a real call, verify the turn loop hits Bedrock + DynamoDB:

    python scripts/lex_smoke_test.py

This replays four hardcoded caller turns through the Lambda directly. Watch
the printed agent replies + the slot attributes that accumulate per turn.

## IAM expectations

The hackathon workshop role needs the following actions for the deploy scripts
to run end-to-end (in addition to the standard Lambda + IAM perms):

  - `lex:CreateBot`, `lex:CreateBotLocale`, `lex:BuildBotLocale`, `lex:CreateBotVersion`,
    `lex:CreateBotAlias`, `lex:UpdateBotAlias`, `lex:ListBots`, `lex:DescribeBot*`
  - `connect:ListInstances`, `connect:ListContactFlows`, `connect:UpdateContactFlowContent`,
    `connect:CreateContactFlow`, `connect:AssociateBot`, `connect:AssociatePhoneNumberContactFlow`,
    `connect:ListPhoneNumbersV2`

If the role lacks `lex:*` or `connect:*`, the deploy will surface
`AccessDeniedException` early — file a permission request and re-run.

## Tenant config

`Companies.companyId` is currently hardcoded as `glanz-ag` in the contact
flow's `UpdateContactAttributes` block (see `build_flow_content` in
`provision_connect.py`). For multi-tenant, either:

  - Claim one phone number per tenant and install one contact flow per number, or
  - Look up the tenant in the contact flow by dialed number (DNIS) before the
    Lex block — requires a small Lambda-data block in front of the Lex block.

Both are roadmap items; one-tenant-per-number is the simpler first step.
