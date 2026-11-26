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
data/toy/                     toy scenario: toy_town.graphml + 6 CSVs + README.md
vehicle_allocation/
  allocation.py               run_allocation(graph, pickups, vehicles, hospitals, config)
                              → {assignments, routes, unserved, summary}  [stub, body in Ch2-4]
  models.py                   dataclasses (Vehicle, Pickup, Demand, Hospital, ClosedRoad, ...) + DataLabel
  loaders.py                  validating loaders → dataclasses; SchemaError on any contract breach
tests/test_smoke.py           2 passing
tests/test_loaders.py         Chunk 1 loader tests
```

Run: `uv sync` once, then `uv run pytest -q` (30 passing).

## Chunk plan — build order & status

| # | Chunk | Status |
|---|-------|--------|
| 0 | Scaffold + spec freeze (git, pyproject, SCHEMAS.md, DECISIONS.md, entrypoint contract) | ✅ done, commit `3a03693` |
| 1 | **Toy dataset + validating loaders** — `data/toy/*` (graphml, closed_roads, pickups, demand, vehicles, hospitals, reachability) + loader that rejects bad columns and preserves `data_label`. Unblocks everything before the real graph arrives | ✅ done, commit `a45993f` |
| 2 | Travel-time + best hospital — strip closed roads, NetworkX time per pickup→hospital, nearest wins, `no_route` if unreachable | ⬜ next (parallel with 3) |
| 3 | CP-SAT allocator — one trip/vehicle, capacity + remaining-demand caps, maximize weighted people served, min-time tiebreak; includes PRD §5 `priority_weight`/`vulnerable_count` (default 1.0/0 → plain version) | ⬜ next (parallel with 2) |
| 4 | Routes + output layer — node path + geometry, GeoJSON/CSV, `assignments.csv`, `unserved.csv` (3 reasons), summary | ⬜ |
| 5 | Acceptance test suite — every PRD §4 criterion as pytest | ⬜ |
| 6 | Real-data integration — swap toy for `hyderabad_drive.graphml` + real files; clean call for app integrator | ⬜ |
| 7 | Baseline vs flood + report numbers — same inputs with/without closures, diff, report stats | ⬜ |

Dependencies: 0 → 1 → (2, 3) → 4 → 5 → 6 → 7. Chunks 2 and 3 are parallelizable once 1 exists.

**The user greenlights chunks manually — do not start a chunk without being told which one.**

### Session protocol (every session, every chunk)

1. **Kickoff:** user says something like "read AGENT.md, build Chunk N" (or just "next chunk" — resolve N from the status table below).
2. **Build** the chunk with TDD; run `uv run pytest -q` often.
3. **Before calling it done:** run the `code-review` skill against the last commit, fix findings.
4. **Update this file:** flip the chunk's status in the table, append anything learned to KNOWN_ISSUES (including minor quirks — especially wrong assumptions that were proven correct or false).
5. **Commit** the chunk + this file update together, so `git log` = chunk history.

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
| 4 | 2026-11-26 | 1 | Review: `load_reachability` hard-requires `pickup_id,hospital_id,data_label`, but SCHEMAS allows "per-pair **or** per-pickup summary" (Muhammad's file unconfirmed) | Kept strict — file is cross-check only and a strict parse surfaces ID/column mismatches (PRD §7). Revisit in Ch6 when the real file arrives; see `load_reachability` docstring |
| 5 | 2026-11-26 | 1 | Review: `load_closed_roads` didn't validate endpoints; now requires `u`,`v` be graph nodes with an edge between them (either direction) | Fixed. Rows are **unordered** — the actual "block both directions" removal is Ch2's job, not the loader's. A closed row naming a road absent from the graph now raises `SchemaError` (intended) |
| 6 | 2026-11-26 | 1 | Nearest-node resolution is Euclidean on lon/lat degrees (no haversine/CRS) | Fine for the toy's dense grid; revisit in Ch6 if the real graph spans a wide area |
| 7 | 2026-11-26 | 1 | Review suspected `test_pickup_neither_node_nor_latlon_rejected` passed for the wrong reason (rejected as unknown-column) | Proven **false**: the node/lat-lon form check runs *before* `_check_columns`. Test now pins the branch with `match="either"` |
| 8 | 2026-11-26 | 1 | `data/toy/README.md` added to record *why* each pickup is placed where it is (which one demonstrates `no_route`, closure-sensitivity, demand-of-1, etc.) | Not an issue — kept so Chunks 2–5 don't have to reverse-engineer the toy's intent |

_(append new rows below; renumber)_
