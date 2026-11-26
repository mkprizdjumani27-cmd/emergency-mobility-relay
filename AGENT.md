# AGENT.md — read this first in every new session

Context file for AI agents working on this repo. **Goal:** don't drift — pick up exactly where the last session left off.

## What this project is

**Vehicle Allocation Module** for the Emergency Mobility Relay System (WFEO Engineering Hackathon 2027): given a flood scenario, assign available vehicles to pickup points so the most people reach a hospital, and report who can't be served and why.

**Source of truth (read in this order):**

1. `PRD Vehicle Allocation Module.md` — the full requirements. This file is authoritative over anything below.
2. `SCHEMAS.md` — column/ID/`data_label` contract for every input & output file (Status: PROPOSED, circulate Day 1).
3. `DECISIONS.md` — open questions for the lead, each with a proposed default.

## Repo state

```
SCHEMAS.md, DECISIONS.md      docs (Day-1 contracts)
pyproject.toml                networkx, pandas, ortools, pytest — uv-managed
vehicle_allocation/
  allocation.py               run_allocation(graph, pickups, vehicles, hospitals, config)
                              → {assignments, routes, unserved, summary}  [stub, body in Ch2-4]
tests/test_smoke.py           2 passing
```

Run: `uv sync` once, then `uv run pytest -q`.

## Chunk plan — build order & status

| # | Chunk | Status |
|---|-------|--------|
| 0 | Scaffold + spec freeze (git, pyproject, SCHEMAS.md, DECISIONS.md, entrypoint contract) | ✅ done, commit `3a03693` |
| 1 | **Toy dataset + validating loaders** — `data/toy/*` (graphml, closed_roads, pickups, demand, vehicles, hospitals, reachability) + loader that rejects bad columns and preserves `data_label`. Unblocks everything before the real graph arrives | ⬜ next |
| 2 | Travel-time + best hospital — strip closed roads, NetworkX time per pickup→hospital, nearest wins, `no_route` if unreachable | ⬜ |
| 3 | CP-SAT allocator — one trip/vehicle, capacity + remaining-demand caps, maximize weighted people served, min-time tiebreak; includes PRD §5 `priority_weight`/`vulnerable_count` (default 1.0/0 → plain version) | ⬜ |
| 4 | Routes + output layer — node path + geometry, GeoJSON/CSV, `assignments.csv`, `unserved.csv` (3 reasons), summary | ⬜ |
| 5 | Acceptance test suite — every PRD §4 criterion as pytest | ⬜ |
| 6 | Real-data integration — swap toy for `hyderabad_drive.graphml` + real files; clean call for app integrator | ⬜ |
| 7 | Baseline vs flood + report numbers — same inputs with/without closures, diff, report stats | ⬜ |

Dependencies: 0 → 1 → (2, 3) → 4 → 5 → 6 → 7. Chunks 2 and 3 are parallelizable once 1 exists.

**The user greenlights chunks manually — do not start a chunk without being told which one.**

## Non-negotiables (from the PRD — these are where drift happens)

- **No transfer hubs. Direct single-leg trips only.** The teammate's hub idea was reduced to a *priority weight* (PRD §5) — nothing more.
- **One trip per vehicle** (default for D1). Multiple trips = stretch goal only.
- **`data_label` (`source_derived` | `manual` | `simulated`) must propagate from every input through to outputs** — checked in tests.
- **No closed road may ever appear in a route** — check edge-by-edge against `closed_roads.csv`.
- Every output describing avoided roads says **"avoiding roads marked closed"** — never "safe routes".
- Hospital capacity is **not modeled** (no data) — state as a limitation, don't invent it.
- Location space/access/flood-safety scoring belongs to **Zahraa's pickup-point review**, not this module.
- Inputs follow the **longer PDF version**: `demand_points.csv` + `vehicles.csv` both exist.

## Don't re-litigate

`DECISIONS.md` holds D1–D3 (one trip/vehicle · drive-to-pickup counts in tie-break only · no hospital capacity). Proposed defaults are active — build to them, **don't re-ask**; flip status only when the lead answers.

## Skills workflow

- `/grill-me` (user-typed, model can't self-invoke) — stress-test a chunk before building.
- `/to-spec` (user-typed) — turn a discussed chunk into a formal spec; needs an issue tracker, else run `/setup-matt-pocock-skills`.
- Implement chunks with TDD where seams exist; run typecheck/single tests often, full suite at the end.
- After each chunk: **run the `code-review` skill** (standards vs. spec) before calling it done, then commit.

## KNOWN_ISSUES

Append-only log. **Log every problem hit during implementation** — env quirks, data mismatches, wrong assumptions, review findings — even after fixing, so the next session doesn't rediscover it.

Format: one row per issue; if unresolved, say what blocks it.

| # | Date | Chunk | Issue | Status / workaround |
|---|------|-------|-------|---------------------|
| 1 | 2026-11-26 | 0 | Git had no global `user.name`/`user.email`; commits use one-off `Uzain <uzain@local>` | Fixed in-session; user should run `git config user.name/user.email` for real authorship |
| 2 | 2026-11-26 | 0 | PRD's two source PDFs disagree (short version folds demand into pickup points); PRD assumes longer version | Assumed longer version — ⬜ needs one confirmation with the lead |
| 3 | 2026-11-26 | 0 | `reachability_results.csv` format unknown (Muhammad owns it); assumed per-pair rows | Unconfirmed — allocator's own NetworkX check is authoritative anyway |

_(append new rows below; renumber)_
