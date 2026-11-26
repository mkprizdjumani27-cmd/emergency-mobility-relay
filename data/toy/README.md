# Toy dataset — `toy_town`

A synthetic flood scenario so Chunks 2–5 aren't blocked waiting for the real
`hyderabad_drive.graphml`. Everything here is `simulated`/`manual` (see each
file's `data_label`).

Load it with:

```python
from vehicle_allocation import load_scenario
scenario = load_scenario("data/toy")
```

## Graph (`toy_town.graphml`)

Directed graph, 11 nodes / 26 arcs (13 two-way roads, both arcs present).
Node attributes `x` (lon) and `y` (lat); edge attribute `length` (metres).

- `n1..n6` — a 2×3 street grid (the main town).
- `n7` — hospital **h1** (close to `n6`, so it wins for pickups in the grid).
- `n8` — hospital **h2** (farther, via `n1`/`n4`).
- `n9` — a dead-end spur off `n2`.
- `n10`–`n11` — a disconnected island (no hospital).

## What each pickup demonstrates

| Pickup | Node | Demand | Demonstrates |
|---|---|---|---|
| `p1` | `n5` (grid centre) | 40 | Nearest-hospital choice (`h1`, 1600 m vs `h2` 2400 m); demand exactly equals `v1`'s capacity 40. |
| `p2` | `n9` (spur) | 30 | **Closure-sensitive**: reachable in baseline, `no_route` once `closed_roads` removes `n2–n9`. |
| `p3` | `n10` (island) | 8 | **Always `no_route`** — island has no hospital, with or without closures. |
| `p4` | `n1` | 20 | Picks the *other* hospital (`h2`, 1400 m). |
| `p5` | `n3` | 1 | **Demand of 1** edge case; `v4` (ambulance, cap 2) is larger than demand. |

## Vehicles

| Vehicle | Start | Capacity | Note |
|---|---|---|---|
| `v1` bus | `n1` | 40 | Capacity exactly equals `p1` demand. |
| `v2` van | `n4` | 15 | |
| `v3` boat | `n6` | 20 | |
| `v4` ambulance | `"17.301,78.419"` → resolves to `n3` | 2 | Exercises **lat/lon → nearest-node** resolution. |

Total capacity 77 < reachable baseline demand (91) → **`insufficient_fleet`** is
demonstrated, in addition to the `no_route` cases.

## Closed roads (the "flood" scenario)

| Edge | Effect |
|---|---|
| `n2–n9` | Cuts the spur → `p2` becomes `no_route` (baseline vs flood, Ch7). |
| `n2–n5` | Forces a detour across the grid (routes must avoid it, Ch4). |

Rows are **unordered** — each blocks that road in both directions. The raw
`toy_town.graphml` still contains these roads; the allocator strips them at
travel-time time (Ch2), so "baseline" = ignore `closed_roads.csv`.
