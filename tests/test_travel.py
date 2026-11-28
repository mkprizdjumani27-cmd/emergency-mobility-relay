"""Chunk 2 tests: closed-road stripping, travel time, best hospital, ``no_route``.

Covers PRD §2.1 end to end on the toy graph: strip both directions of every
closed road (multigraph-safe, never mutating the input), one Dijkstra per
pickup, nearest hospital by time (= metres, speed-invariant), ``no_route`` for
unreachable pickups, ``travel_min`` unit conversion, the ``path_distance``
seam reserved for Ch3's D2 drive-to-pickup tie-break, edge-length surfacing
(KNOWN_ISSUES #9), and a test-only cross-check against Muhammad's
``reachability_results.csv`` (lead decision 2026-11-27: allocator stays
authoritative; runtime check deferred to Ch6).
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from vehicle_allocation.loaders import (
    SchemaError,
    load_closed_roads,
    load_graph,
    load_hospitals,
    load_pickups,
    load_reachability,
)
from vehicle_allocation.models import ClosedRoad, Hospital, Pickup
from vehicle_allocation.travel import (
    PickupTravel,
    best_hospitals,
    path_distance,
    strip_closed_roads,
    travel_min,
)

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "data" / "toy"
REAL_DIR = ROOT / "data" / "musi_corridor"
REAL_GRAPH = REAL_DIR / "musi_corridor.graphml"


@pytest.fixture(scope="module")
def graph():
    return load_graph(TOY / "toy_town.graphml")


@pytest.fixture(scope="module")
def baseline(graph):
    """Baseline mode = raw graph, closed_roads ignored (toy README, Ch7)."""
    return strip_closed_roads(graph, [])


@pytest.fixture(scope="module")
def flood(graph):
    return strip_closed_roads(graph, load_closed_roads(TOY / "closed_roads.csv", graph))


@pytest.fixture(scope="module")
def pickups(graph):
    return load_pickups(TOY / "pickup_points.csv", graph)


@pytest.fixture(scope="module")
def hospitals(graph):
    return load_hospitals(TOY / "hospitals.csv", graph)


# --- stripping -------------------------------------------------------------


def test_strip_returns_copy_and_leaves_input_alone(graph):
    closed = load_closed_roads(TOY / "closed_roads.csv", graph)
    stripped = strip_closed_roads(graph, closed)
    assert stripped is not graph
    assert graph.number_of_edges() == 26  # raw toy never mutated
    assert stripped.number_of_edges() == 22  # 26 arcs - 2 roads x 2 arcs


def test_strip_removes_closed_roads_in_both_directions(graph, flood):
    for u, v in (("n2", "n9"), ("n9", "n2"), ("n2", "n5"), ("n5", "n2")):
        assert not flood.has_edge(u, v)
    assert flood.degree("n9") == 0  # spur fully cut off
    assert flood.has_edge("n5", "n6")  # unrelated roads survive


def test_strip_baseline_ignores_closed_roads(graph, baseline):
    assert baseline.number_of_edges() == 26
    assert baseline.has_edge("n2", "n9")


def test_strip_multigraph_removes_every_parallel_arc():
    # Real OSM graph is a MultiDiGraph (KNOWN_ISSUES #10); remove_edge(u, v)
    # would drop only ONE parallel arc and silently keep the road open.
    g = nx.MultiDiGraph()
    g.add_edge("a", "b", length=100.0)
    g.add_edge("a", "b", length=120.0)
    g.add_edge("b", "a", length=100.0)
    g.add_edge("a", "c", length=50.0)
    stripped = strip_closed_roads(g, [ClosedRoad(u="a", v="b", data_label="manual")])
    assert not stripped.has_edge("a", "b")
    assert not stripped.has_edge("b", "a")
    assert stripped.number_of_edges() == 1  # only a->c left
    assert g.number_of_edges() == 4  # input untouched


# --- edge-length validation (KNOWN_ISSUES #9) ------------------------------


def test_strip_surfaces_nonnumeric_edge_length_against_the_edge():
    # load_graph deliberately leaves unparseable values alone so Ch2 can
    # surface them here, naming the offending edge.
    g = nx.DiGraph()
    g.add_edge("a", "b", length="oops")
    g.add_edge("b", "a", length=10.0)
    with pytest.raises(SchemaError) as exc:
        strip_closed_roads(g, [])
    msg = str(exc.value)
    assert "length" in msg
    assert "oops" in msg
    assert "('a', 'b')" in msg


def test_strip_rejects_missing_or_negative_length():
    g = nx.DiGraph()
    g.add_edge("a", "b")  # no length at all
    with pytest.raises(SchemaError, match="length"):
        strip_closed_roads(g, [])

    g2 = nx.DiGraph()
    g2.add_edge("a", "b", length=-5.0)
    with pytest.raises(SchemaError, match="length"):
        strip_closed_roads(g2, [])


def test_bad_length_on_a_closed_edge_is_still_surfaced():
    # Validation runs on the input graph (before removal) so baseline and
    # flood modes surface identical data problems — Ch7 diffs the two modes
    # on the same inputs, so one mode crashing while the other passes would
    # break the comparison.
    g = nx.DiGraph()
    g.add_edge("a", "b", length="flooded")
    g.add_edge("b", "c", length=10.0)
    with pytest.raises(SchemaError, match="flooded"):
        strip_closed_roads(g, [ClosedRoad(u="a", v="b", data_label="manual")])
    with pytest.raises(SchemaError, match="flooded"):
        strip_closed_roads(g, [])


# --- best hospital (PRD §2.1) ----------------------------------------------


def test_best_hospital_nearest_by_time_baseline(baseline, pickups, hospitals):
    results = best_hospitals(baseline, pickups, hospitals)
    assert [r.pickup_id for r in results] == ["p1", "p2", "p3", "p4", "p5"]  # input order
    by_id = {r.pickup_id: r for r in results}

    assert by_id["p1"].hospital_id == "h1"
    assert by_id["p1"].distance_m == pytest.approx(1600)  # vs h2 at 2400 (toy README)
    assert by_id["p1"].path == ("n5", "n6", "n7")
    assert by_id["p4"].hospital_id == "h2"
    assert by_id["p4"].distance_m == pytest.approx(1400)  # toy README
    assert by_id["p5"].hospital_id == "h1"
    assert by_id["p5"].distance_m == pytest.approx(700)
    # PRD §2.1: time to EVERY reachable hospital is computed, not just the winner
    assert set(by_id["p1"].reachable) == {"h1", "h2"}
    assert by_id["p1"].reachable["h1"] == pytest.approx(1600)
    assert by_id["p1"].reachable["h2"] == pytest.approx(2400)


def test_winner_and_paths_are_internally_consistent(baseline, pickups, hospitals):
    for r in best_hospitals(baseline, pickups, hospitals):
        if r.hospital_id is None:
            continue
        # winner = min over reachable by (distance, hospital_id)
        assert r.hospital_id == min(r.reachable, key=lambda h: (r.reachable[h], h))
        # the reported distance is exactly the sum of the path's edge lengths
        total = sum(baseline.edges[u, v]["length"] for u, v in zip(r.path, r.path[1:]))
        assert total == pytest.approx(r.distance_m)


def test_island_pickup_is_no_route(baseline, pickups, hospitals):
    [p3] = [r for r in best_hospitals(baseline, pickups, hospitals) if r.pickup_id == "p3"]
    assert p3.hospital_id is None
    assert p3.distance_m is None
    assert p3.path is None
    assert p3.reachable == {}


def test_no_hospitals_means_every_pickup_is_no_route(baseline, pickups):
    results = best_hospitals(baseline, pickups, [])
    assert results
    assert all(r.hospital_id is None and r.reachable == {} for r in results)


def test_equidistant_hospitals_tie_break_by_hospital_id():
    g = nx.DiGraph()
    g.add_edge("p", "x", length=100.0)
    g.add_edge("p", "y", length=100.0)
    pickups = [Pickup("pp", "p", "simulated")]
    hospitals = [
        Hospital("hZ", "x", "source_derived"),  # listed first, must NOT win
        Hospital("hA", "y", "source_derived"),
    ]
    [r] = best_hospitals(g, pickups, hospitals)
    assert r.hospital_id == "hA"  # (distance, hospital_id) — input-order independent
    assert set(r.reachable) == {"hZ", "hA"}


def test_flood_closures_change_results(baseline, flood, pickups, hospitals):
    base = {r.pickup_id: r for r in best_hospitals(baseline, pickups, hospitals)}
    wet = {r.pickup_id: r for r in best_hospitals(flood, pickups, hospitals)}
    # spur cut by closed_roads -> p2 flips to no_route (toy README); island stays
    assert base["p2"].hospital_id == "h1"
    assert base["p2"].distance_m == pytest.approx(3000)
    assert wet["p2"].hospital_id is None
    assert wet["p3"].hospital_id is None
    # survivors keep the same best hospital and time on this toy
    for pid in ("p1", "p4", "p5"):
        assert wet[pid].hospital_id == base[pid].hospital_id
        assert wet[pid].distance_m == pytest.approx(base[pid].distance_m)


def test_flood_paths_contain_no_closed_road(graph, flood, pickups, hospitals):
    # Non-negotiable #4 at the Ch2 layer: paths are built only from the
    # stripped graph. Ch4 repeats this edge-by-edge against closed_roads.csv
    # on the emitted routes (PRD §4).
    closed = load_closed_roads(TOY / "closed_roads.csv", graph)
    forbidden = {(r.u, r.v) for r in closed} | {(r.v, r.u) for r in closed}
    checked = 0
    for r in best_hospitals(flood, pickups, hospitals):
        if r.path is None:
            continue
        for u, v in zip(r.path, r.path[1:]):
            assert (u, v) not in forbidden, f"{r.pickup_id} path uses closed road {u}-{v}"
            checked += 1
    assert checked  # p1/p4/p5 paths were all inspected


def test_best_hospitals_rejects_nodes_missing_from_graph(baseline):
    with pytest.raises(SchemaError, match="px"):
        best_hospitals(baseline, [Pickup("px", "not_a_node", "simulated")], [])
    with pytest.raises(SchemaError, match="hx"):
        best_hospitals(baseline, [], [Hospital("hx", "not_a_node", "source_derived")])


def test_data_label_propagates_from_pickups(baseline, pickups, hospitals):
    results = best_hospitals(baseline, pickups, hospitals)
    assert all(isinstance(r, PickupTravel) for r in results)
    assert [r.data_label for r in results] == [p.data_label for p in pickups]


# --- reachability cross-check (test-only; allocator stays authoritative) ----


def test_computed_reachability_matches_muhammads_file(baseline, pickups, hospitals):
    computed = {
        (r.pickup_id, h)
        for r in best_hospitals(baseline, pickups, hospitals)
        for h in r.reachable
    }
    expected = {
        (r.pickup_id, r.hospital_id)
        for r in load_reachability(TOY / "reachability_results.csv")
    }
    assert computed == expected  # baseline pairs agree with the known-good toy file


# --- travel_min + path_distance (Ch3's D2 seam) ----------------------------


def test_travel_min_converts_metres_and_speed_to_minutes():
    assert travel_min(1600, 30.0) == pytest.approx(3.2)  # v1 bus
    assert travel_min(1600, 60.0) == pytest.approx(1.6)  # v4 ambulance
    assert travel_min(700, 60.0) == pytest.approx(0.7)
    assert travel_min(1400, 40.0) == pytest.approx(2.1)
    assert travel_min(0, 30.0) == 0.0
    with pytest.raises(SchemaError, match="speed_kmh"):
        travel_min(1000, 0)  # public seam: guard the schema's float > 0 rule


def test_path_distance_measures_drive_to_pickup(baseline, flood):
    assert path_distance(baseline, "n1", "n5") == pytest.approx(2200)  # n1-n2-n5 or n1-n4-n5
    assert path_distance(baseline, "n1", "n9") == pytest.approx(2300)  # n1-n2-n9
    assert path_distance(baseline, "n1", "n1") == 0.0  # already there
    assert path_distance(flood, "n1", "n9") is None  # spur cut -> unreachable


def test_path_distance_rejects_unknown_nodes(baseline):
    with pytest.raises(SchemaError, match="zzz"):
        path_distance(baseline, "zzz", "n1")
    with pytest.raises(SchemaError, match="zzz"):
        path_distance(baseline, "n1", "zzz")


# --- package exports --------------------------------------------------------


def test_package_root_exports_travel_api():
    import vehicle_allocation as va

    for name in ("PickupTravel", "best_hospitals", "path_distance", "strip_closed_roads", "travel_min"):
        assert hasattr(va, name), f"vehicle_allocation.{name} not exported"


# --- real Musi-corridor graph (KNOWN_ISSUES #10/#11) ------------------------


@pytest.mark.skipif(
    not REAL_GRAPH.exists(),
    reason="Musi-corridor graph is gitignored; pull with scripts/pull_osm.py (KNOWN_ISSUES #10)",
)
def test_real_musi_corridor_runs_end_to_end():
    # Exercises everything the toy cannot: MultiDiGraph parallel arcs, OSM
    # string-typed lengths (#9), 38 weakly-connected components (#11), 128
    # hospitals. strip() also validates all ~47k edge lengths here.
    graph = load_graph(REAL_GRAPH)
    assert graph.is_multigraph()
    stripped = strip_closed_roads(graph, [])
    hospitals = load_hospitals(REAL_DIR / "hospitals.csv", graph)

    step = max(1, len(stripped) // 5)
    sample_nodes = sorted(stripped.nodes)[::step][:5]
    pickups = [Pickup(f"rp{i}", node, "simulated") for i, node in enumerate(sample_nodes)]

    results = best_hospitals(stripped, pickups, hospitals)
    hospital_nodes = {h.hospital_id: h.node for h in hospitals}
    pickup_nodes = {p.pickup_id: p.node for p in pickups}

    assert len(results) == 5
    assert sum(r.hospital_id is not None for r in results) >= 1  # giant component dominates
    for r in results:
        assert r.data_label == "simulated"
        if r.hospital_id is None:
            assert r.reachable == {}
            continue
        assert r.hospital_id in hospital_nodes
        assert r.path[0] == pickup_nodes[r.pickup_id]
        assert r.path[-1] == hospital_nodes[r.hospital_id]
        assert all(stripped.has_edge(u, v) for u, v in zip(r.path, r.path[1:]))
