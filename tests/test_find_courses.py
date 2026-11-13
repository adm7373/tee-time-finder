"""Tests for the find_courses CLI's pure parts.

The alias guessing and probing live in ``app.teeitup_probe`` and are exercised
through the CLI's imports; what is pinned here is that the CLI still exposes
the shapes its users rely on.
"""

import pytest

from app.teeitup_probe import class_name_for, slug_candidates


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Norwood Country Club", "norwood-country-club"),
        ("Presidents Golf Course", "presidents-golf-course"),
        ("Pinehills Golf Club", "pinehills-golf-club"),
        ("Rockland Golf Course", "rockland-golf-course"),
    ],
)
def test_slug_matches_the_alias_teeitup_actually_uses(name, expected):
    assert expected in slug_candidates(name)


def test_alias_variants_cover_names_that_are_not_the_slug():
    # These two were only found by trial: the alias drops the leading words or
    # runs them together.
    assert "devine-golf-course" in slug_candidates("William J. Devine Golf Course")
    assert "cedarhill-golf" in slug_candidates("Cedar Hill Golf Course")


def test_candidates_are_unique_and_never_empty_strings():
    for name in ("Cedar Hill Golf Course", "A", "The Country Club"):
        candidates = slug_candidates(name)
        assert candidates
        assert all(candidates)
        assert len(candidates) == len(set(candidates))


def test_candidates_strip_punctuation():
    assert "william-j-devine-golf-course" in slug_candidates(
        "William J. Devine Golf Course"
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Norwood Country Club", "NorwoodCountryClubProvider"),
        ("Stone-E-Lea Golf Course", "StoneELeaGolfCourseProvider"),
        ("Pinehills Golf Club", "PinehillsGolfClubProvider"),
    ],
)
def test_class_name_is_a_valid_identifier(name, expected):
    assert class_name_for(name) == expected
    assert class_name_for(name).isidentifier()
