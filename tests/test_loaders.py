"""Chunk 1 tests: the toy dataset loads through validating loaders.

Covers: happy-path load of every toy file, column/type/enum validation,
``data_label`` propagation, lat/lon -> node resolution, closed-road endpoint
validation, and referential integrity across files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vehicle_allocation.loaders import (
    SchemaError,
    load_closed_roads,
    load_demand,
    load_graph,
    load_hospitals,
    load_pickups,
    load_reachability,
    load_scenario,
    load_vehicles,
)

TOY = Path(__file__).resolve().parents[1] / "data" / "toy"
VALID_LABELS = {"source_derived", "manual", "simulated"}

# Reused by the vehicles validation tests below.
VEHICLES_HEADER = "vehicle_id,type,capacity,start_node_or_lat_lon,speed_kmh,data_label\n"


@pytest.fixture(scope="module")
def graph():
    return load_graph(TOY / "toy_town.graphml")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


# --- happy path ----------------------------------------------------------


def test_load_graph_shape(graph):
    assert graph.is_directed()
    assert graph.number_of_nodes() == 11
    assert graph.number_of_edges() == 26  # 13 two-way roads -> 26 arcs
    assert set(graph.nodes) >= {"n1", "n5", "n7", "n9", "n10"}
    assert graph.nodes["n5"]["x"] == pytest.approx(78.41)
    assert graph.nodes["n5"]["y"] == pytest.approx(17.31)


def test_load_vehicles_roundtrip(graph):
    vehicles = load_vehicles(TOY / "vehicles.csv", graph)
    by_id = {v.vehicle_id: v for v in vehicles}
    assert set(by_id) == {"v1", "v2", "v3", "v4"}
    v1 = by_id["v1"]
    assert v1.type == "bus"
    assert v1.capacity == 40
    assert v1.start_node == "n1"
    assert v1.speed_kmh == pytest.approx(30.0)
    assert v1.data_label == "manual"
    assert v1.start_lat is None and v1.start_lon is None


def test_vehicle_latlon_resolves_to_nearest_node(graph):
    vehicles = load_vehicles(TOY / "vehicles.csv", graph)
    v4 = next(v for v in vehicles if v.vehicle_id == "v4")
    # "17.301,78.419" is nearest to n3 (78.42, 17.30)
    assert v4.start_node == "n3"
    assert v4.start_lat == pytest.approx(17.301)
    assert v4.start_lon == pytest.approx(78.419)


def test_load_pickups(graph):
    pickups = load_pickups(TOY / "pickup_points.csv", graph)
    assert len(pickups) == 5
    by_id = {p.pickup_id: p for p in pickups}
    assert by_id["p1"].node == "n5"
    assert by_id["p3"].node == "n10"  # island
    assert all(p.data_label == "simulated" for p in pickups)


def test_load_demand_preserves_and_defaults():
    demand = load_demand(TOY / "demand_points.csv")
    by_id = {d.pickup_id: d for d in demand}
    assert by_id["p1"].demand_count == 40
    assert by_id["p1"].vulnerable_count == 5
    assert by_id["p1"].priority_weight == pytest.approx(1.5)
    assert by_id["p4"].data_label == "manual"


def test_demand_optional_columns_default_when_absent(tmp_path):
    csv = _write(tmp_path, "demand.csv", "pickup_id,demand_count,data_label\np1,7,simulated\n")
    [d] = load_demand(csv)
    assert d.demand_count == 7
    assert d.vulnerable_count == 0
    assert d.priority_weight == pytest.approx(1.0)


def test_demand_empty_optional_cell_defaults(tmp_path):
    # SCHEMAS: "empty cell = missing" -> optional columns fall back to default.
    csv = _write(
        tmp_path,
        "demand.csv",
        "pickup_id,demand_count,vulnerable_count,priority_weight,data_label\n"
        "p1,7,,2.5,simulated\n"
        "p2,4,3,,simulated\n",
    )
    rows = {d.pickup_id: d for d in load_demand(csv)}
    assert rows["p1"].vulnerable_count == 0  # empty cell -> default
    assert rows["p1"].priority_weight == pytest.approx(2.5)
    assert rows["p2"].vulnerable_count == 3
    assert rows["p2"].priority_weight == pytest.approx(1.0)  # empty cell -> default


def test_load_hospitals(graph):
    hospitals = load_hospitals(TOY / "hospitals.csv", graph)
    assert {h.hospital_id for h in hospitals} == {"h1", "h2"}
    assert {h.node for h in hospitals} == {"n7", "n8"}
    assert all(h.data_label == "source_derived" for h in hospitals)


def test_load_closed_roads_keeps_reason_and_endpoints(graph):
    roads = load_closed_roads(TOY / "closed_roads.csv", graph)
    assert {(r.u, r.v) for r in roads} == {("n2", "n9"), ("n2", "n5")}
    spur = next(r for r in roads if (r.u, r.v) == ("n2", "n9"))
    assert spur.reason == "spur washed out"
    assert spur.data_label == "manual"


def test_load_reachability_excludes_island_pickup():
    rows = load_reachability(TOY / "reachability_results.csv")
    assert all(r.pickup_id != "p3" for r in rows)  # p3 is on the island
    assert ("p1", "h1") in {(r.pickup_id, r.hospital_id) for r in rows}
    assert all(r.data_label == "source_derived" for r in rows)


def test_load_scenario_loads_everything():
    scenario = load_scenario(TOY)
    assert scenario.graph.number_of_nodes() == 11
    assert len(scenario.pickups) == 5
    assert len(scenario.demand) == 5
    assert len(scenario.vehicles) == 4
    assert len(scenario.hospitals) == 2
    assert len(scenario.closed_roads) == 2
    assert len(scenario.reachability) == 8


def test_data_label_propagates_from_every_toy_file():
    scenario = load_scenario(TOY)
    labels = (
        [v.data_label for v in scenario.vehicles]
        + [p.data_label for p in scenario.pickups]
        + [d.data_label for d in scenario.demand]
        + [h.data_label for h in scenario.hospitals]
        + [r.data_label for r in scenario.closed_roads]
        + [r.data_label for r in scenario.reachability]
    )
    assert labels, "expected labelled rows"
    assert set(labels) <= VALID_LABELS
    # The toy deliberately mixes all three labels so none can be hardcoded.
    assert set(labels) == VALID_LABELS


def test_scenario_referential_integrity():
    scenario = load_scenario(TOY)
    pickup_ids = {p.pickup_id for p in scenario.pickups}
    hospital_ids = {h.hospital_id for h in scenario.hospitals}
    assert {d.pickup_id for d in scenario.demand} == pickup_ids
    assert all(r.pickup_id in pickup_ids for r in scenario.reachability)
    assert all(r.hospital_id in hospital_ids for r in scenario.reachability)


# --- validation: reject bad columns --------------------------------------


def test_missing_required_column_rejected(graph, tmp_path):
    csv = _write(
        tmp_path,
        "vehicles.csv",
        "vehicle_id,type,capacity,start_node_or_lat_lon,data_label\n"
        "v1,bus,40,n1,manual\n",
    )
    with pytest.raises(SchemaError, match="speed_kmh"):
        load_vehicles(csv, graph)


def test_unknown_column_rejected(graph, tmp_path):
    csv = _write(
        tmp_path,
        "vehicles.csv",
        "vehicle_id,type,capacity,start_node_or_lat_lon,speed_kmh,data_label,notes\n"
        "v1,bus,40,n1,30.0,manual,hi\n",
    )
    with pytest.raises(SchemaError, match="notes"):
        load_vehicles(csv, graph)


def test_invalid_data_label_rejected(graph, tmp_path):
    csv = _write(tmp_path, "vehicles.csv", VEHICLES_HEADER + "v1,bus,40,n1,30.0,fake_label\n")
    with pytest.raises(SchemaError, match="data_label"):
        load_vehicles(csv, graph)


def test_zero_capacity_rejected(graph, tmp_path):
    csv = _write(tmp_path, "vehicles.csv", VEHICLES_HEADER + "v1,bus,0,n1,30.0,manual\n")
    with pytest.raises(SchemaError, match="capacity"):
        load_vehicles(csv, graph)


def test_nonpositive_speed_rejected(graph, tmp_path):
    csv = _write(tmp_path, "vehicles.csv", VEHICLES_HEADER + "v1,bus,40,n1,0,manual\n")
    with pytest.raises(SchemaError, match="speed_kmh"):
        load_vehicles(csv, graph)


def test_duplicate_vehicle_id_rejected(graph, tmp_path):
    csv = _write(
        tmp_path,
        "vehicles.csv",
        VEHICLES_HEADER + "v1,bus,40,n1,30.0,manual\n" "v1,van,10,n2,40.0,manual\n",
    )
    with pytest.raises(SchemaError, match="duplicate"):
        load_vehicles(csv, graph)


def test_unknown_node_reference_rejected(graph, tmp_path):
    csv = _write(tmp_path, "vehicles.csv", VEHICLES_HEADER + "v1,bus,40,not_a_node,30.0,manual\n")
    with pytest.raises(SchemaError, match="not_a_node"):
        load_vehicles(csv, graph)


def test_negative_demand_rejected(tmp_path):
    csv = _write(tmp_path, "demand.csv", "pickup_id,demand_count,data_label\np1,-3,simulated\n")
    with pytest.raises(SchemaError, match="demand_count"):
        load_demand(csv)


def test_pickup_neither_node_nor_latlon_rejected(graph, tmp_path):
    # Has pickup_id/data_label but neither a 'node' nor a 'lat'/'lon' column.
    csv = _write(tmp_path, "pickups.csv", "pickup_id,location,data_label\np1,n5,simulated\n")
    with pytest.raises(SchemaError, match="either"):
        load_pickups(csv, graph)


def test_pickup_with_both_node_and_latlon_rejected(graph, tmp_path):
    csv = _write(
        tmp_path,
        "pickups.csv",
        "pickup_id,node,lat,lon,data_label\np1,n5,17.31,78.41,simulated\n",
    )
    with pytest.raises(SchemaError, match="not both"):
        load_pickups(csv, graph)


# --- closed-road endpoint validation --------------------------------------


def test_closed_road_with_unknown_endpoint_rejected(graph, tmp_path):
    csv = _write(tmp_path, "closed_roads.csv", "u,v,data_label\nn2,zzz,manual\n")
    with pytest.raises(SchemaError, match="zzz"):
        load_closed_roads(csv, graph)


def test_closed_road_between_nonadjacent_nodes_rejected(graph, tmp_path):
    # n1 and n11 are both in the graph but share no edge.
    csv = _write(tmp_path, "closed_roads.csv", "u,v,data_label\nn1,n11,manual\n")
    with pytest.raises(SchemaError, match="no edge"):
        load_closed_roads(csv, graph)


# --- lat/lon resolution ---------------------------------------------------


def test_pickup_latlon_resolves_to_nearest_node(graph, tmp_path):
    # nearest to (lon=78.409, lat=17.311) is n5 (78.41, 17.31)
    csv = _write(tmp_path, "pickups.csv", "pickup_id,lat,lon,data_label\npx,17.311,78.409,simulated\n")
    [p] = load_pickups(csv, graph)
    assert p.node == "n5"
    assert p.lat == pytest.approx(17.311)
    assert p.lon == pytest.approx(78.409)


def test_vehicle_latlon_tolerates_spaces(graph, tmp_path):
    csv = _write(
        tmp_path,
        "vehicles.csv",
        VEHICLES_HEADER + 'v9,boat,5,"17.301, 78.419",25.0,simulated\n',
    )
    [v] = load_vehicles(csv, graph)
    assert v.start_node == "n3"
    assert v.start_lat == pytest.approx(17.301)
    assert v.start_lon == pytest.approx(78.419)


# --- referential integrity -------------------------------------------------


def test_scenario_rejects_demand_for_unknown_pickup(tmp_path):
    dest = tmp_path / "toy"
    shutil.copytree(TOY, dest)
    with open(dest / "demand_points.csv", "a") as f:
        f.write("p999,5,0,1.0,simulated\n")
    with pytest.raises(SchemaError, match="p999"):
        load_scenario(dest)
