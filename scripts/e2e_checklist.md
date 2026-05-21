# Atrium E2E Checklist

1. `python smoke_test.py` succeeds with AWS identity and Bedrock response.
2. `python scripts/provision_connect.py` creates or reuses the Connect setup.
3. `python scripts/deploy_lambda.py` deploys the Stage 2 Input Agent.
4. `python scripts/seed_ddb.py` seeds Companies, Crews, and PriceMatrix.
5. `python scripts/seed_kb.py` uploads FAQ, pricelist, and service catalog docs.
6. Call the claimed number and confirm Lambda logs include `STAGE2_INVOKE`.
7. Start `cd web && npm run dev` and verify the mock Wall renders.
8. Replace mock events with AppSync subscription once `AtriumApi` is deployed.
