# Amazon Connect Notes

The README keeps Connect as a one-time manual step during the hackathon. The
existing scripts in `scripts/` provision the Stage 1/2 flow and deploy the
Input Agent Lambda.

## Flow Contract

Connect invokes `lambdas/input_agent/handler.lambda_handler` and expects a flat
string-keyed response. The Stage 2 flow speaks `$.External.greeting` and then
disconnects.

Stage 3 should replace the spoken greeting path with the Nova Sonic audio bridge,
while keeping these fields stable for logging and dashboard correlation:

- `contactId`
- `companyId`
- `callId`
- `locale`

## Manual Checklist

1. Claim or reuse an Amazon Connect phone number.
2. Associate the deployed Input Agent Lambda with the Connect instance.
3. Import/update the inbound contact flow.
4. Confirm contact attributes include `companyId`.
5. Call the number and tail `/aws/lambda/atrium-input-agent`.
