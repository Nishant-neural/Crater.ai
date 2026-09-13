"""Tests for the pure graph-traversal logic (no DB/Anthropic needed)."""
from backend.schematic.graph import trace_path


def test_trace_direct_connection():
    edges = [("Power", "K17"), ("K17", "Motor")]
    assert trace_path(edges, "Power", "K17") == ["Power", "K17"]


def test_trace_multi_hop():
    edges = [("Power", "K17"), ("K17", "X12"), ("X12", "Motor")]
    assert trace_path(edges, "Power", "Motor") == ["Power", "K17", "X12", "Motor"]


def test_trace_same_node():
    assert trace_path([("A", "B")], "A", "A") == ["A"]


def test_trace_unreachable():
    edges = [("A", "B"), ("C", "D")]
    assert trace_path(edges, "A", "D") is None


def test_trace_undirected():
    # Edge stored as (Motor, K17) should still let us trace K17 -> Motor.
    edges = [("Motor", "K17")]
    assert trace_path(edges, "K17", "Motor") == ["K17", "Motor"]


def test_trace_picks_shortest_path():
    # A->B->C->D is longer than the direct A->D edge.
    edges = [("A", "B"), ("B", "C"), ("C", "D"), ("A", "D")]
    assert trace_path(edges, "A", "D") == ["A", "D"]
