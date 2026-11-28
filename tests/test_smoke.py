"""Chunk 0 smoke test: the package imports and exposes the agreed entrypoint."""

import inspect

from vehicle_allocation import run_allocation


def test_entrypoint_exists_with_agreed_signature():
    params = list(inspect.signature(run_allocation).parameters)
    assert params == ["graph", "pickups", "vehicles", "hospitals", "config"]


def test_entrypoint_not_yet_implemented():
    try:
        run_allocation(None, None, None, None, None)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("expected NotImplementedError until Chunks 2-4 land")
