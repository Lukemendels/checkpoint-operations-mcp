# Ideas: the Golden Age of Air Travel

These are **brainstorming candidates**, not an approved architecture, implementation plan, TSA position, endorsement, or claim about a deployed service. The current project plan remains [PLAN.md](../PLAN.md).

Luke's proposed title: **"How TSA is using SI to advance the Golden Age of Air Travel"**. For a prototype presentation, consider **"How TSA could use SI to advance the Golden Age of Air Travel"** to avoid implying a deployed or endorsed capability.

**Possible subtitle:** "From your front door to your boarding gate, with less guesswork."

## Two sides of one journey

- [Traveler journey](traveler.md): help a traveler decide when to leave and update the recommendation when conditions change.
- [Staffing scenario planner](staffing.md): help a supervisor compare hypothetical staffing choices against a simulated checkpoint surge.
  - [Staffing tools design sketch](staffing-tools.md): proposed `staffing_*` tools, the capacity → utilization → wait-response model, the forecast input contract the staffing side needs from the wait-time prediction, and the extra synthetic fixtures it owns. Staffing is a **client** of the wait-time prediction, not a second forecaster.

**Paired demo concept:** A shared, proposed forecasting and simulation core serves two interfaces. A simulated surge raises the estimated checkpoint wait. The traveler's suggested departure time changes; a supervisor then compares a feasible hypothetical staffing adjustment and sees the modeled wait change. The supervisor approves or rejects the scenario. Every display labels the scenario, assumptions, simulated inputs, time, and uncertainty. The resulting difference is a simulation estimate, not evidence that staffing changes cause that real-world benefit.

This is a product concept for a compelling demonstration, not a commitment to build either interface or a validated forecast. A synthetic schema-shaped demo could illustrate the experience and a future implementation roadmap, but the real data schema is unverified: any contract would be **proposed**, not an exact replica. Synthetic tests cannot establish operational effectiveness. Reconcile the event's public-government-data rule with any proposed synthetic demo before selecting it for submission; do not assume eligibility or discard the concept without that decision.

## Questions for the team

1. Should a demonstration cover one airport and one checkpoint, and what decision should it show most clearly?
2. Which scenario assumptions, uncertainty ranges, and evaluation cases would make the demonstration understandable and credible?
3. What public data can be inspected and pinned, and what proposed data contract is needed where real schemas are unavailable?
4. Which staffing qualifications, breaks, labor rules, lane capacity and other feasibility constraints must a realistic scenario represent?
5. How does the public-data rule apply to this synthetic concept, and what evidence is needed to confirm eligibility?
6. IBM details remain intentionally pending owner input; what, if anything, should they change later?

