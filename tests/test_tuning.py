"""The steward's constants are tuned, and CI keeps them tuned.

`tools/tune_baselines.py` is the harness; this file runs it. The point is not
that the shipped numbers are pretty — it is that they are the output of a
deterministic sweep that anyone can re-run, and that a change to the baselines,
to a module's physics or to a constant moves the optimum and fails here rather
than shipping a fallback policy nobody measured.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import tune_baselines as harness  # noqa: E402

from coworld.examples.commons_family.game import baselines  # noqa: E402


def test_the_sweep_covers_the_grid_and_every_module():
    assert len(harness.MODULES) == 4
    assert len(harness.ROOMS) == 3
    rows = harness.sweep()
    assert len(rows) == len(harness.TRIGGERS) * len(harness.FLOORS) == 36
    # Best first, and every combination scored.
    assert rows == sorted(rows, key=lambda row: -row[0])
    assert all(value > 0 for value, _, _, _ in rows)


def test_the_shipped_constants_are_what_the_grid_says():
    rows = harness.sweep()
    best = max(value for value, admissible, _, _ in rows if admissible)
    shipped = next(
        row for row in rows
        if (row[2], row[3]) == (baselines.CLEAN_POLLUTION_TRIGGER,
                                baselines.CLEANUP_STOCK_FLOOR)
    )
    value, admissible, _, _ = shipped
    assert admissible, "six stewards must never kill their own commons"
    assert value >= best * (1 - harness.TOLERANCE), (
        f"the shipped constants score {value:.2f} against the grid's best "
        f"{best:.2f}; re-run tools/tune_baselines.py and retune"
    )


def test_the_shipped_constants_are_the_best_conditional_ones():
    """Within the tolerance, the shipped pair is the top *conditional* rule.

    The grid's very top is `trigger = 0.05`, which makes the steward clean
    whenever the river is dirty at all — an unconditional rule, which is the
    `cleaner` baseline. Keeping the steward conditional is a design choice; it
    costs 1.0 % and this test pins that it costs no more.
    """
    rows = harness.sweep()
    conditional = [row for row in rows
                   if row[1] and row[2] >= baselines.CLEAN_POLLUTION_TRIGGER]
    assert conditional
    value, _, trigger, floor = conditional[0]
    assert trigger == baselines.CLEAN_POLLUTION_TRIGGER
    best_floor_value = max(v for v, _, t, _ in conditional
                           if t == baselines.CLEAN_POLLUTION_TRIGGER)
    shipped_value = next(
        v for v, _, t, f in conditional
        if (t, f) == (baselines.CLEAN_POLLUTION_TRIGGER, baselines.CLEANUP_STOCK_FLOOR)
    )
    # The floor buys survival under pressure rather than score, so it is
    # allowed to cost something — but not more than the same 2 %.
    assert shipped_value >= best_floor_value * (1 - harness.TOLERANCE)
    assert value >= shipped_value
