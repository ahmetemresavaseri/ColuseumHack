# Hallucination Test

Goal: verify the agent refuses out-of-scope answers instead of inventing
company policy. Cases below are run by `scripts/run_rag_eval.py` against the
deterministic `kb_lookup`; a pass means the response contains the refusal
sentinel (`I don't have that information.`) and emits **no** citations.

| Case | Prompt | Expected behavior | Result |
|---|---|---|---|
| H-001 | Do you also repair elevators? | Refusal, no citations. | Pending |
| H-002 | Can I pay with cryptocurrency? | Refusal, no citations. | Pending |
| H-003 | Do you also wash cars? | Refusal, no citations. | Pending |
| H-004 | Can you do my taxes? | Refusal, no citations. | Pending |
| H-005 | Do you sell vacuum cleaners? | Refusal, no citations. | Pending |
