# Checkpoint Operations SI Data Service: submission guide

This GSA hackathon prototype demonstrates two distinct, read-only workflows:
historical public TSA throughput lookup and synthetic staffing scenario analysis.
It does not connect to live TSA systems, use restricted records, or recommend
operational staffing.

## Two-minute demo

| Step | Ask | Expected result |
| --- | --- | --- |
| Public history | What did the TSA report for IAD on Dec. 30, 2018? | 27,089 reported screenings across 83 source checkpoint-hour rows, with provenance. |
| Coverage boundary | What about IAD on Dec. 29, 2018? | No observation in this sample. Do not report zero. |
| Synthetic what-if | For fictional ATL Checkpoint A Standard screening on Nov. 9, 2026, use the synthetic weekday forecast, a 30-minute p95 target, and remove one lane group each hour from 7 through 9 a.m. Compare p95 at 10 a.m. | Capacity is unchanged at 10 a.m. Modeled p95 is 63.6 minutes vs. 14.6 at baseline because backlog carries over. Not observed waits or a real forecast for ATL. |

Suggested agent instruction: report source values as returned. Do not apply
unverified corrections to stored data. Missing observations are unavailable,
not zero. Keep simulated outputs separate from observed TSA data.

## Verification and limits

- [Quickstart](QUICKSTART.md) for local stdio; [IBM toolkit](deploy/ibm/local-mcp-toolkit/README.md)
  for an authorized private sandbox. Live IBM import and agent behavior must be
  verified in that environment.
- [Public data provenance and reproduction](docs/public-throughput-demo.md).
  The small, two-day published-data sample does not feed the staffing model.
- [Evaluation and methodology](docs/evaluation.md). The packaged code was
  verified locally with 164 tests and 30 applicable deterministic benchmark
  checks; six cases required an agent and were not rerun. A clean unpack over
  MCP stdio verified 15 tools and all three demo outcomes above.
- In the earlier synthetic agent evaluation, the tools arm scored 24/24 and
  7/8 on held-out answers for the two model configurations (no-tool baseline:
  0/24 and 0/8). Boundary scores were 15/18 and 5/6, below the 90% target:
  the agent applied an unverified adjustment even though the tool returned
  the correct stored value. These are historical scores, not a new-agent run
  or a validation of operational accuracy.
- The slide deck and evaluation document are separate submission deliverables.
  The historical agent evaluation is reported separately from current tool tests.

[Official GSA event and deliverables](https://www.gsa.gov/super-intelligence/si-community-of-practice/events-and-training/mcp-server-and-si-agent-government-hackathon).
Verify the participant submission route and judge access separately. Do not
deploy the prototype publicly.
