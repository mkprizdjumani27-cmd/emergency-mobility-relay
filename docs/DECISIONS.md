# DECISIONS — open questions to confirm with the lead

From PRD §2. Each has a **proposed default** so work is never blocked; flip the status when the lead answers.

| # | Decision | Proposed default (building to this unless told otherwise) | Status |
|---|---|---|---|
| D1 | One trip per vehicle, or multiple? | **One trip per vehicle** — simplest, matches "single-leg trips, no transfer hubs". Multiple trips = stretch goal. | ⬜ pending |
| D2 | Does the vehicle's drive from its start point to the pickup count? | **Count it in the time tie-breaker only** — not in feasibility or the objective's main term. | ⬜ pending |
| D3 | Hospital capacity modeling? | **Not modeled** (no data) — stated as a limitation in the report. | ⬜ pending |
| D4 | Which input's `data_label` goes on `assignments.csv`? Each row mixes vehicle + pickup + hospital + demand, but the column holds one value (Ch2's records carry only the pickup's label — KNOWN_ISSUES #16). | **The demand row's label** — the carried passengers are the row's subject (PRD §5's manual/simulated note is about demand rows). One column can't show every source: if `source_derived` must surface in outputs it has to ride via the hospital — needs a ruling. | ⬜ pending |
| D7 | Tie-break: strictly lexicographic (never trade a weighted person for time) or blended small ε into the objective? | **Lexicographic** — PRD §2.2 calls it a "tie-breaker"; a blend could sacrifice a person for minutes. Built this way in Ch3, but it is my reading, not a lead ruling. | ⬜ pending |

## Scope guardrails (settled in the PRD, no need to re-ask)

- No transfer hubs; direct single-leg trips only (PRD note 2).
- Teammate's hub idea reduced to: priority weight for isolated/urgent zones and vulnerable passengers (PRD §5). Location space/access/flood-safety scoring belongs to Zahraa's pickup-point review.
- Inputs assume the **longer** PDF version (`demand_points.csv` + `vehicles.csv`). ⬜ worth one confirmation with the lead.

## Resolved

| # | Decision | Answer | Status |
|---|---|---|---|
| D5 | PRD §5 objective formula — how do `priority_weight` **and** `vulnerable_count` enter "weighted people served"? (asked 2026-11-27) | **Vulnerable seats count double**: value = `priority_weight` per passenger + one extra `priority_weight` per *vulnerable* passenger, counted first when a pickup is only partially served; bonus configurable, default 1.0 → a vulnerable passenger counts 2× at their pickup's weight | ✅ answered 2026-11-27 |
| D6 | Solver guarantee — prove optimality or cap the time? (asked 2026-11-27) | **12 s total budget** (middle of the proposed 10–15 s), best-found solution acceptable; `optimal=False` flags an unfinished proof. Phase 1 capped at 70% so the time tie-break always gets ≥30% | ✅ answered 2026-11-27 |
