# DECISIONS — open questions to confirm with the lead

From PRD §2. Each has a **proposed default** so work is never blocked; flip the status when the lead answers.

| # | Decision | Proposed default (building to this unless told otherwise) | Status |
|---|---|---|---|
| D1 | One trip per vehicle, or multiple? | **One trip per vehicle** — simplest, matches "single-leg trips, no transfer hubs". Multiple trips = stretch goal. | ⬜ pending |
| D2 | Does the vehicle's drive from its start point to the pickup count? | **Count it in the time tie-breaker only** — not in feasibility or the objective's main term. | ⬜ pending |
| D3 | Hospital capacity modeling? | **Not modeled** (no data) — stated as a limitation in the report. | ⬜ pending |
| D4 | Which input's `data_label` goes on `assignments.csv`? Each row mixes vehicle + pickup + hospital + demand, but the column holds one value (Ch2's records carry only the pickup's label — KNOWN_ISSUES #16). | **The demand row's label** — the carried passengers are the row's subject (PRD §5's manual/simulated note is about demand rows). One column can't show every source: if `source_derived` must surface in outputs it has to ride via the hospital — needs a ruling. | ⬜ pending |

## Scope guardrails (settled in the PRD, no need to re-ask)

- No transfer hubs; direct single-leg trips only (PRD note 2).
- Teammate's hub idea reduced to: priority weight for isolated/urgent zones and vulnerable passengers (PRD §5). Location space/access/flood-safety scoring belongs to Zahraa's pickup-point review.
- Inputs assume the **longer** PDF version (`demand_points.csv` + `vehicles.csv`). ⬜ worth one confirmation with the lead.

## Resolved

_(move rows here when answered)_
