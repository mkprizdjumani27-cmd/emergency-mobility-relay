"""Single entrypoint for the app integrator (PRD §3).

Scaffold only (Chunk 0): the signature is the contract; the body lands in
Chunks 2-4.
"""

from __future__ import annotations


def run_allocation(graph, pickups, vehicles, hospitals, config):
    """Run vehicle allocation for one scenario.

    Args:
        graph: NetworkX graph for the scenario (closed roads already removed).
        pickups: pickup points joined with demand.
        vehicles: available vehicles.
        hospitals: reachable hospitals.
        config: allocator configuration (priority weights on/off, tie-breaker).

    Returns:
        dict with keys ``assignments``, ``routes``, ``unserved``, ``summary``.
    """
    raise NotImplementedError(
        "run_allocation lands in Chunks 2-4 — see DECISIONS.md for open questions"
    )
