# Candidate: door-to-gate traveler assistant

**Idea, not an operational service or validated prediction.** A traveler asks when to leave for a flight. The concept works backward from the boarding deadline, estimating drive time, parking or drop-off, bag check, security, and walking time to the gate. It shows each component, assumptions, uncertainty and a suggested departure window rather than promising arrival or making a boarding guarantee.

For a demonstration, use a clearly labeled synthetic itinerary and simulated traffic, checkpoint and flight changes. Recalculate the recommendation when a scenario changes and explain what changed. A forecast or recommendation is illustrative until tested against appropriate observed data; synthetic cases can test the arithmetic and user experience, not actual prediction accuracy or traveler outcomes.

Google Maps API is one possible future source of drive-time information, subject to suitability, terms, privacy, cost, and access review. This idea authorizes no account, credentials, billing, service configuration, or API use. The demo can use simulated drive times instead.

Future roadmap questions include whether reliable public checkpoint and flight data exist, how freshness and missing inputs are communicated, how forecast accuracy is measured, and what traveler safety margins are appropriate. Do not imply that throughput alone measures wait time.

