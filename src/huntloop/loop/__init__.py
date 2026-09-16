"""The adaptive feedback loop's shared contracts (Phase 5).

LOOP-08 assertion: nothing in this package may reach the rubric configuration
module — every proposal is bounded by ``huntloop.loop.types``'s permitted edit
surface — and only ``huntloop.loop.rationale`` may reach the model client.
Both boundaries are enforced structurally by ``tests/loop/test_boundaries.py``.
"""
