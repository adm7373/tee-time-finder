"""Nominatim boxes worth searching for golf courses around a ZIP.

Named here (not on the event) so invoking with no arguments reproduces the
manual habit: the same default ZIP and radii, instead of whatever the caller
happened to pass.
"""

#: (label, radius miles) pairs swept by the discover Lambda on a default run.
SEARCH_BOXES: tuple[tuple[str, float], ...] = (
    ("near", 12.0),
    ("mid", 25.0),
    ("far", 40.0),
)
