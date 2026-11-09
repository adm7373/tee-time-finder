"""Tests for ZIP/course geocoding, the DynamoDB cache and radius filtering.

DynamoDB is faked with an in-memory table so the store's read-through behaviour
can be exercised without AWS.
"""

from datetime import date, time

import pytest

from app import geo, providers
from app.geo import Coordinates, GeocodeStore, Geocoder, distance_miles
from app.models import SearchRequest, TeeTimeOption

QUINCY = Coordinates(lat=42.2461701, lon=-71.0057746, display_name="02169, Quincy")
NORWOOD = Coordinates(lat=42.1986317, lon=-71.1820973, display_name="Norwood CC")
PLYMOUTH = Coordinates(lat=41.9584, lon=-70.6673, display_name="Plymouth CC")


class FakeTable:
    """Stands in for a boto3 DynamoDB Table."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.gets = 0
        self.puts = 0

    def get_item(self, Key):  # noqa: N803 - mirrors boto3's signature
        self.gets += 1
        item = self.items.get(Key["pk"])
        return {"Item": item} if item else {}

    def put_item(self, Item):  # noqa: N803
        self.puts += 1
        self.items[Item["pk"]] = Item


# --- distance ----------------------------------------------------------------


def test_distance_to_self_is_zero():
    assert distance_miles(QUINCY, QUINCY) == pytest.approx(0.0)


def test_distance_quincy_to_norwood():
    # Quincy centre to Norwood CC: 9.6 miles as the crow flies.
    assert distance_miles(QUINCY, NORWOOD) == pytest.approx(9.6, abs=0.2)


def test_distance_quincy_to_plymouth():
    # Quincy to the Plymouth coast: 26.4 miles as the crow flies.
    assert distance_miles(QUINCY, PLYMOUTH) == pytest.approx(26.4, abs=0.3)


def test_distance_is_symmetric():
    assert distance_miles(QUINCY, PLYMOUTH) == pytest.approx(
        distance_miles(PLYMOUTH, QUINCY)
    )


# --- Nominatim client --------------------------------------------------------


class FakeGeocoder(Geocoder):
    def __init__(self, results: dict[str, Coordinates]):
        super().__init__("test-agent", min_interval=0)
        self.results = results
        self.queries: list[dict] = []

    def _get(self, params):
        self.queries.append(params)
        hit = self.results.get(params.get("postalcode") or params.get("q"))
        return (
            [
                {
                    "lat": str(hit.lat),
                    "lon": str(hit.lon),
                    "display_name": hit.display_name,
                }
            ]
            if hit
            else []
        )


def test_geocoder_asks_nominatim_for_a_us_postcode():
    geocoder = FakeGeocoder({"02169": QUINCY})
    assert geocoder.by_zip("02169") == QUINCY
    assert geocoder.queries[0]["postalcode"] == "02169"
    assert geocoder.queries[0]["country"] == "us"


def test_geocoder_returns_none_for_an_unknown_place():
    assert FakeGeocoder({}).by_zip("99999") is None


def test_geocoder_ignores_unusable_hits():
    class Bad(Geocoder):
        def __init__(self):
            super().__init__("test", min_interval=0)

        def _get(self, params):
            return [{"lat": "not-a-number", "lon": "-71"}]

    assert Bad().by_zip("02169") is None


# --- store -------------------------------------------------------------------


def test_zip_miss_geocodes_and_stores():
    table = FakeTable()
    store = GeocodeStore("t", table=table)
    geocoder = FakeGeocoder({"02169": QUINCY})

    assert store.zip("02169", geocoder) == QUINCY
    assert "zip#02169" in table.items
    assert table.puts == 1


def test_zip_hit_does_not_geocode_again():
    table = FakeTable()
    geocoder = FakeGeocoder({"02169": QUINCY})

    first = GeocodeStore("t", table=table)
    assert first.zip("02169", geocoder) == QUINCY

    second = GeocodeStore("t", table=table)  # a cold process
    assert second.zip("02169", geocoder) == QUINCY
    assert len(geocoder.queries) == 1, "second lookup should have been a cache hit"


def test_zip_is_memoised_within_a_process():
    table = FakeTable()
    store = GeocodeStore("t", table=table)
    geocoder = FakeGeocoder({"02169": QUINCY})

    store.zip("02169", geocoder)
    store.zip("02169", geocoder)
    assert table.gets == 1


def test_unknown_zip_returns_none_without_a_geocoder():
    store = GeocodeStore("t", table=FakeTable())
    assert store.zip("02169") is None


def test_missing_table_degrades_quietly():
    store = GeocodeStore(None)
    assert store.zip("02169", FakeGeocoder({"02169": QUINCY})) == QUINCY
    assert store.course("5720") is None


def test_course_is_read_from_the_table():
    table = FakeTable()
    store = GeocodeStore("t", table=table)
    store.store_course("5720", NORWOOD)
    assert store.course("5720") == NORWOOD
    assert table.items["course#5720"]["source"] == "nominatim"


def test_dynamodb_failure_is_swallowed():
    class Broken(FakeTable):
        def get_item(self, Key):  # noqa: N803
            raise geo.BotoCoreError()

    store = GeocodeStore("t", table=Broken())
    assert store.course("5720") is None


# --- filtering ---------------------------------------------------------------


def _request(**kwargs) -> SearchRequest:
    return SearchRequest(date=date(2026, 11, 10), players=2, **kwargs)


@pytest.fixture
def fake_courses(monkeypatch):
    """Two providers, one near (Norwood) and one far (Plymouth)."""

    class Near(providers.TeeItUpProvider):
        facility_id = "1"
        display_name = "Near CC"
        x_be_alias = "near"

        def fetch_availability(self, request):
            return [
                {
                    "date": request.date,
                    "start_time": time(8, 0),
                    "course_name": self.display_name,
                    "players": request.players,
                }
            ]

    class Far(Near):
        facility_id = "2"
        display_name = "Far CC"
        x_be_alias = "far"

        def fetch_availability(self, request):
            return [
                {
                    "date": request.date,
                    "start_time": time(9, 0),
                    "course_name": self.display_name,
                    "players": request.players,
                }
            ]

    monkeypatch.setattr(providers, "get_search_providers", lambda: [Near(), Far()])

    table = FakeTable()
    store = GeocodeStore("t", table=table)
    store.store_course("1", NORWOOD)
    store.store_course("2", PLYMOUTH)
    # Without a geocoder the ZIP lookup is a cache miss and no radius applies,
    # so seed the caller's location as if it had been geocoded earlier.
    store.store_zip("02169", QUINCY)
    return store


def test_radius_filters_out_distant_courses(fake_courses):
    options = providers.search_all(_request(radius_miles=15), store=fake_courses)
    assert [o.course_name for o in options] == ["Near CC"]


def test_wide_radius_keeps_everything(fake_courses):
    options = providers.search_all(_request(radius_miles=60), store=fake_courses)
    assert [o.course_name for o in options] == ["Near CC", "Far CC"]


def test_results_carry_real_distances(fake_courses):
    options = providers.search_all(_request(radius_miles=60), store=fake_courses)
    near = next(o for o in options if o.course_name == "Near CC")
    far = next(o for o in options if o.course_name == "Far CC")
    assert near.distance_miles == pytest.approx(9.6, abs=0.2)
    assert far.distance_miles == pytest.approx(26.4, abs=0.3)


def test_course_without_coordinates_is_still_searched(fake_courses):
    # A course that was never seeded must not vanish from a narrow search: it
    # has no distance, so the radius cannot rule it out.
    fake_courses._memory.pop("course#2", None)
    fake_courses.table.items.pop("course#2")
    options = providers.search_all(_request(radius_miles=15), store=fake_courses)
    assert [o.course_name for o in options] == ["Near CC", "Far CC"]
    assert options[1].distance_miles == 0.0


def test_duplicate_aliases_are_collapsed(monkeypatch, fake_courses):
    original = providers.get_search_providers

    def doubled():
        # Ponkapoag is fronted by two aliases pointing at the same facility.
        return original() + [original()[0]]

    monkeypatch.setattr(providers, "get_search_providers", doubled)
    options = providers.search_all(_request(radius_miles=60), store=fake_courses)
    keys = [(o.course_name, o.start_time) for o in options]
    assert len(keys) == len(set(keys))


def test_unknown_zip_searches_everything_without_distances(monkeypatch, fake_courses):
    request = _request(zip_code="99999", radius_miles=1)
    options = providers.search_all(request, store=fake_courses)
    assert [o.course_name for o in options] == ["Near CC", "Far CC"]
    assert all(o.distance_miles == 0.0 for o in options)


def test_search_returns_dated_options(fake_courses):
    options: list[TeeTimeOption] = providers.search_all(
        _request(radius_miles=20), store=fake_courses
    )
    assert options[0].date == date(2026, 11, 10)
    assert options[0].provider == "near"
