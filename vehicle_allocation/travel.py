"""Travel-time layer (Chunk 2): closed-road stripping + best hospital per pickup.

Implements PRD §2.1: for each pickup, compute travel time to every reachable
hospital on the scenario graph (NetworkX), pick the nearest by time, and mark
the pickup ``no_route`` when none is reachable.

Design notes (settled with the lead, 2026-11-27):

* **Strip, don't mutate.** :func:`strip_closed_roads` returns a *copy* with
  every closed road removed in **both** directions — all parallel arcs on
  multigraphs (the real OSM graph is a MultiDiGraph; the toy is a DiGraph and
  would hide a one-arc-only removal). Pass an empty list for the baseline run:
  Ch7 needs the same raw graph with and without closures.
* **Metres, not minutes, at this layer.** A vehicle's speed is uniform across
  the graph, so time = length / speed scales every route of one vehicle
  equally: the nearest hospital *by time* is the nearest *by metres* for every
  vehicle. Results therefore store ``distance_m``; call :func:`travel_min` with
  the matched vehicle's ``speed_kmh`` when minutes are needed
  (assignments.csv ``est_travel_min``, PRD §3).
* **Edge lengths are validated once, here.** KNOWN_ISSUES #9: ``load_graph``
  leaves unparseable ``length`` values alone so this layer can surface them
  against the offending edge (``SchemaError``) instead of failing somewhere
  inside Dijkstra. Validation runs on the *input* graph, before any removal,
  so baseline and flood modes surface identical data problems — Ch7 diffs the
  two modes on the same inputs, so one mode crashing while the other passes
  would break the comparison.
* **Ties** on equal distance break by ``hospital_id`` so results never depend
  on input order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, NamedTuple

import networkx as nx

from vehicle_allocation.loaders import SchemaError
from vehicle_allocation.models import ClosedRoad, DataLabel, Hospital, Pickup

__all__ = [
    "PickupTravel",
    "best_hospitals",
    "path_distance",
    "strip_closed_roads",
    "travel_min",
]


class _Winner(NamedTuple):
    """Best hospital so far, ordered (distance_m, hospital_id, node)."""

    distance_m: float
    hospital_id: str
    node: str


def _require_node(graph: nx.Graph, node: str, *, who: str) -> None:
    """Raise :class:`SchemaError` unless ``node`` exists in ``graph``.

    Unknown nodes must fail loudly: a hospital node missing from the graph
    would otherwise read as "unreachable" and silently become ``no_route``.
    """
    if node not in graph:
        raise SchemaError(f"{who}: node {node!r} is not in the graph")


@dataclass(frozen=True)
class PickupTravel:
    """PRD §2.1 travel-time result for one pickup.

    ``hospital_id`` / ``distance_m`` / ``path`` are ``None`` together when no
    hospital is reachable — the ``no_route`` marker (PRD §4; Ch4 turns it into
    an ``unserved.csv`` row). ``reachable`` keeps *every* reachable hospital's
    distance in metres (PRD §2.1) for cross-checks and reporting.
    ``data_label`` is the pickup's label so provenance survives (PRD §4).
    """

    pickup_id: str
    hospital_id: str | None
    distance_m: float | None
    path: tuple[str, ...] | None
    reachable: dict[str, float]
    data_label: DataLabel


def _validate_edge_lengths(graph: nx.Graph) -> None:
    """Raise :class:`SchemaError` if any edge has an unusable ``length``.

    Usable = a finite number >= 0 (bools excluded). Reports up to five
    offending edges by ``(u, v)`` and value so the caller can fix the graphml
    against the right edge (KNOWN_ISSUES #9).
    """
    if graph.is_multigraph():
        edges = ((u, v, d) for u, v, _, d in graph.edges(keys=True, data=True))
    else:
        edges = ((u, v, d) for u, v, d in graph.edges(data=True))

    bad: list[tuple[str, str, object]] = []
    for u, v, data in edges:
        if "length" not in data:
            bad.append((u, v, "<missing>"))
            continue
        val = data["length"]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            bad.append((u, v, val))
        elif not math.isfinite(val) or val < 0:
            bad.append((u, v, val))

    if bad:
        shown = ", ".join(f"({u!r}, {v!r}): {val!r}" for u, v, val in bad[:5])
        more = f" ... and {len(bad) - 5} more" if len(bad) > 5 else ""
        raise SchemaError(
            f"graph: {len(bad)} edge(s) with unusable 'length' "
            f"(need a finite number >= 0): {shown}{more}"
        )


def strip_closed_roads(graph: nx.Graph, closed_roads: Iterable[ClosedRoad]) -> nx.Graph:
    """Return a copy of ``graph`` with every closed road removed.

    Rows are unordered: ``(u, v)`` blocks that road in **both** directions,
    including *all* parallel arcs on multigraphs (loaders keep the endpoints
    verbatim; removing both arcs is this function's job — see
    ``load_closed_roads``). The input graph is never mutated.

    Also validates edge ``length`` on ``graph`` *before* removal
    (KNOWN_ISSUES #9), so this is the scenario entry point for both modes:
    baseline = ``[]``, flood = the loaded ``closed_roads`` — both surface
    identical data problems.
    """
    _validate_edge_lengths(graph)
    stripped = graph.copy()
    for road in closed_roads:
        for u, v in ((road.u, road.v), (road.v, road.u)):
            if not stripped.has_edge(u, v):
                continue
            if stripped.is_multigraph():
                stripped.remove_edges_from([(u, v, key) for key in stripped[u][v]])
            else:
                stripped.remove_edge(u, v)
    return stripped


def best_hospitals(
    graph: nx.Graph,
    pickups: Iterable[Pickup],
    hospitals: Iterable[Hospital],
) -> list[PickupTravel]:
    """Pick the nearest reachable hospital for every pickup (PRD §2.1).

    One Dijkstra per pickup on ``weight="length"`` (the graph must come from
    :func:`strip_closed_roads`: closed roads removed, lengths validated).
    Results follow ``pickups`` order. Winner = smallest ``distance_m``, ties
    broken by ``hospital_id``. A pickup that reaches no hospital gets
    ``hospital_id=None`` — the ``no_route`` marker.
    """
    pickups = list(pickups)
    hospitals = list(hospitals)
    for p in pickups:
        _require_node(graph, p.node, who=f"pickup {p.pickup_id!r}")
    for h in hospitals:
        _require_node(graph, h.node, who=f"hospital {h.hospital_id!r}")

    out: list[PickupTravel] = []
    for p in pickups:
        dist, paths = nx.single_source_dijkstra(graph, p.node, weight="length")
        reachable: dict[str, float] = {}
        winner: _Winner | None = None
        for h in hospitals:
            d = dist.get(h.node)
            if d is None:
                continue  # this hospital is unreachable from the pickup
            reachable[h.hospital_id] = d
            key = _Winner(d, h.hospital_id, h.node)
            if winner is None or key < winner:
                winner = key

        if winner is None:
            out.append(PickupTravel(p.pickup_id, None, None, None, {}, p.data_label))
        else:
            d, hospital_id, node = winner
            out.append(
                PickupTravel(
                    p.pickup_id,
                    hospital_id,
                    d,
                    tuple(paths[node]),
                    reachable,
                    p.data_label,
                )
            )
    return out


def travel_min(distance_m: float, speed_kmh: float) -> float:
    """Convert a distance in metres + a vehicle speed to travel time in minutes.

    ``speed_kmh`` comes from ``vehicles.csv`` (schema: float > 0, enforced by
    the loader); guarded here too because this is a public seam.
    """
    if not speed_kmh > 0:
        raise SchemaError(f"travel_min: speed_kmh must be > 0 (got {speed_kmh!r})")
    return distance_m / 1000.0 / speed_kmh * 60.0


def path_distance(graph: nx.Graph, u: str, v: str) -> float | None:
    """Shortest ``u`` -> ``v`` distance in metres, or ``None`` if unreachable.

    Generic from->to seam on the stripped graph — Ch3 uses it for D2's
    drive-to-pickup tie-break (``vehicle.start_node`` -> ``pickup.node``).
    Unknown nodes raise rather than silently reading as unreachable.
    """
    who = f"path_distance({u!r} -> {v!r})"
    _require_node(graph, u, who=who)
    _require_node(graph, v, who=who)
    try:
        return float(nx.shortest_path_length(graph, u, v, weight="length"))
    except nx.NetworkXNoPath:
        return None
