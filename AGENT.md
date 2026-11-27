# AGENT.md — read this first in every new session

Context file for AI agents working on this repo. **Goal:** don't drift — pick up exactly where the last session left off.

## What this project is

**Vehicle Allocation Module** for the Emergency Mobility Relay System (WFEO Engineering Hackathon 2027): given a flood scenario, assign available vehicles to pickup points so the most people reach a hospital, and report who can't be served and why.

**Source of truth (read in this order):**

1. `docs/PRD Vehicle Allocation Module.md` — the full requirements. This file is authoritative over anything below.
2. `docs/SCHEMAS.md` — column/ID/`data_label` contract for every input & output file (Status: PROPOSED, circulate Day 1).
3. `docs/DECISIONS.md` — open questions for the lead, each with a proposed default.

## Repo state

```
docs/                         PRD, SCHEMAS.md, DECISIONS.md, PROGRESS.md (plain-language feature
                              status snapshot — refresh its built/remaining sections when a milestone
                              lands), source PDFs (Day-1 contracts; moved here from repo root)
pyproject.toml                networkx, pandas, ortools, pytest — uv-managed
data/toy/                     toy scenario: toy_town.graphml + 6 CSVs + README.md (controlled edge cases)
data/musi_corridor/           REAL Musi-corridor pull: musi_corridor.graphml (18k nodes) + hospitals.csv (128)
                              [gitignored ~17MB — regenerate: uv run --with osmnx scripts/pull_osm.py]
scripts/pull_osm.py           build-time OSM pull tool — `uv run --with osmnx scripts/pull_osm.py`
vehicle_allocation/
  allocation.py               run_allocation(graph, pickups, vehicles, hospitals, config)
                              → {assignments, routes, unserved, summary}  [stub, body in Ch2-4]
  models.py                   dataclasses (Vehicle, Pickup, Demand, Hospital, ClosedRoad, ...) + DataLabel
  loaders.py                  validating loaders → dataclasses; SchemaError on any contract breach;
                              load_graph also normalizes OSM string-typed attributes (see KNOWN_ISSUES #9)
  travel.py                   Chunk 2: strip_closed_roads (copy; both arcs incl. all parallel; validates
                              edge length on input graph), best_hospitals (Dijkstra per pickup → nearest
                              by metres, no_route = None-triple), travel_min(distance_m, speed_kmh),
                              path_distance (Ch3's D2 drive-to-pickup tie-break seam)
tests/test_smoke.py           2 passing
tests/test_loaders.py         Chunk 1 loader tests
tests/test_travel.py          Chunk 2 travel tests + real-graph smoke (skips if data/musi_corridor absent)
```

Run: `uv sync` once, then `uv run pytest -q` (53 passing).

## Chunk plan — build order & status

| # | Chunk | Status |
|---|-------|--------|
| 0 | Scaffold + spec freeze (git, pyproject, SCHEMAS.md, DECISIONS.md, entrypoint contract) | ✅ done, commit `3a03693` |
| 1 | **Toy dataset + validating loaders** — `data/toy/*` (graphml, closed_roads, pickups, demand, vehicles, hospitals, reachability) + loader that rejects bad columns and preserves `data_label`. Unblocks everything before the real graph arrives | ✅ done, commit `a45993f` |
| 2 | Travel-time + best hospital — strip closed roads, NetworkX time per pickup→hospital, nearest wins, `no_route` if unreachable | ✅ done, commit `6510814` |
| 3 | CP-SAT allocator — one trip/vehicle, capacity + remaining-demand caps, maximize weighted people served, min-time tiebreak; includes PRD §5 `priority_weight`/`vulnerable_count` (default 1.0/0 → plain version) | ⬜ next |
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
4. **Update this file:** flip the chunk's status in the table, append anything learned to KNOWN_ISSUES (including minor quirks — especially wrong assumptions that were proven correct or false). If user-facing status changed, refresh the built/remaining sections of `docs/PROGRESS.md` too.
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

`docs/DECISIONS.md` holds D1–D3 (one trip/vehicle · drive-to-pickup counts in tie-break only · no hospital capacity). Proposed defaults are active — build to them, **don't re-ask**; flip status only when the lead answers.

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
| 9 | 2026-11-27 | 1 | **Toy→real attribute-type trap:** OSM graphml (osmnx) round-trips *every* attribute as a string (edge `length` = `'7.2086...'`); the toy's `length` was `int`. Ch2 travel-time arithmetic would pass on the toy and crash on real data | Fixed: `load_graph` now coerces `x`,`y`,`length`,`street_count`,`oneway`,`reversed` to native types (unparseable values left alone, never crash the load). Regression-tested by `test_load_graph_normalizes_string_typed_attributes` |
| 10 | 2026-11-27 | 1 | Real data pulled **ahead of Ch6** per user (lives in Hyderabad; toy diverges "night and day" from reality): `data/musi_corridor/` = Musi river corridor bbox, 18,282 nodes / 47,066 edges (MultiDiGraph, real one-ways) + 128 hospitals (Point+Polygon → `representative_point()`). Written in lat/lon form so the loader's nearest-node resolution is exercised | Regenerate/widen: `uv run --with osmnx scripts/pull_osm.py [--bbox L B R T --out DIR --name BASE]`. **osmnx is a build-time tool only** — deliberately not a runtime dep (loaders read graphml with networkx) |
| 11 | 2026-11-27 | 1 | Real graph has **38 weakly-connected components** (largest 18,202 + tiny pockets of 2–8 nodes) because `pull_osm.py` uses `retain_all=True` (kept deliberately — real disconnected pockets aid `no_route` realism) | If the small fragments prove noisy, re-pull with `retain_all=False` (largest component only); disconnection can then come from `closed_roads` alone |
| 12 | 2026-11-27 | 2 | Ch0 `run_allocation` docstring says the caller passes the graph "closed roads already removed"; Ch2 instead strips at travel time on a copy (Ch7 needs the raw graph for both baseline AND flood runs — a pre-stripped graph can't be un-stripped) | Flagged; entrypoint untouched this chunk — fix the wording when Ch4 wires the body |
| 13 | 2026-11-27 | 2 | `MultiDiGraph.remove_edge(u, v)` deletes only ONE parallel arc — stripping must enumerate keys or a closed road stays half-open on the real OSM graph (the DiGraph toy hides this) | Fixed + regression-tested: `test_strip_multigraph_removes_every_parallel_arc` |
| 14 | 2026-11-27 | 2 | Best-hospital argmin is **speed-invariant** (time = length ÷ speed; speed uniform per vehicle ⇒ nearest-by-time == nearest-by-metres for every vehicle). Lead-confirmed in grill: travel.py stores metres, `travel_min()` converts per matched vehicle | Settled — Ch3/Ch4 must NOT re-derive this or build a (pickup × vehicle) time matrix |
| 15 | 2026-11-27 | 2 | `no_route` reaches Ch2's record only as the **None-triple** (`hospital_id`/`distance_m`/`path` all `None` — lead-approved record shape); the `reason` token belongs to Ch4's `unserved.csv` enum (`no_route`\|`insufficient_fleet`\|`capacity_exhausted`) | Ch4 maps the triple → `reason="no_route"`; don't invent a second marker |
| 16 | 2026-11-27 | 2 | Review: `PickupTravel` carries only the pickup's `data_label` — hospital/vehicle labels have no path to outputs yet | Output label rule unresolved → added DECISIONS **D4**; Ch4 must answer before writing `assignments.csv` |
| 17 | 2026-11-27 | 2 | Reachability cross-check landed **test-only** on the toy (lead decision 2026-11-27); runtime comparison against `reachability_results.csv` still deferred — real file format unconfirmed (see #3/#4) | Test pins baseline pairs == the toy file's 8 rows; revisit the runtime check in Ch6 |
| 18 | 2026-11-27 | 2 | Review: validating only the *stripped* graph let a bad `length` on a closed edge pass in flood mode but fail in baseline — would break Ch7's both-modes diff | Fixed: `strip_closed_roads` validates the input graph before removal; `test_bad_length_on_a_closed_edge_is_still_surfaced` pins both modes |

_(append new rows below; renumber)_
