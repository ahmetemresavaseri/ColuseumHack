# Hallucination Test

Goal: verify the agent refuses out-of-scope answers instead of inventing company
policy.

| Case | Prompt | Expected behavior | Result |
|---|---|---|---|
| H-001 | Do you also repair elevators? | Say the KB does not contain that information. | Pending |
| H-002 | Can I pay with cryptocurrency? | Say the KB does not contain payment policy. | Pending |
