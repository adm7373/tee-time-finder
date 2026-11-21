"""Tests for the TeeItUp availability parser.

The fixture mirrors the real API response shape: a list of day objects whose
``teetimes[].teetime`` values are UTC instants (``...Z``) and whose
``rates[].allowedPlayers`` gates which party sizes can book each slot.
"""

from datetime import date, time

import pytest

from app.models import SearchRequest
from app.providers import (
    ENGINE_PROVIDERS,
    BookingEngineProvider,
    ForeUpProvider,
    TeeItUpProvider,
    get_providers_from_store,
    get_search_providers,
    provider_coverage,
    search_all,
)

# 12:00Z and 13:09Z on 2026-11-11 are 07:00 and 08:09 in New York (EST).
PAYLOAD = [
    {
        "dayInfo": {
            "dawn": "2026-11-11T11:01:00.000Z",
            "sunrise": "2026-11-11T11:31:00.000Z",
            "sunset": "2026-11-11T21:25:00.000Z",
            "dusk": "2026-11-11T21:58:00.000Z",
        },
        "courseId": "54f14d710c8ad60378b03ca1",
        "totalAvailableTeetimes": 3,
        "teetimes": [
            {
                "teetime": "2026-11-11T12:00:00.000Z",
                "rates": [{"name": "18 Riding", "allowedPlayers": [1, 2, 3, 4]}],
            },
            {
                "teetime": "2026-11-11T13:09:00.000Z",
                "rates": [{"name": "18 Riding", "allowedPlayers": [1, 2]}],
            },
            {
                # 22:21Z is 17:21 local, and 02:00Z on the 12th is 21:00 local
                # on the 11th - both belong to the requested day.
                "teetime": "2026-11-11T22:21:00.000Z",
                "rates": [{"name": "9 Walking", "allowedPlayers": [1, 2, 3, 4]}],
            },
            {
                # 23:00 local - still the requested day.
                "teetime": "2026-11-12T04:00:00.000Z",
                "rates": [{"name": "18 Riding", "allowedPlayers": [1, 2, 3, 4]}],
            },
            {
                # 01:00 on 2026-11-12 in New York: the next local day.
                "teetime": "2026-11-12T06:00:00.000Z",
                "rates": [{"name": "18 Riding", "allowedPlayers": [1, 2, 3, 4]}],
            },
            {
                # Malformed slot must be skipped, not raise.
                "teetime": "not-a-timestamp",
                "rates": [{"name": "18 Riding", "allowedPlayers": [1, 2, 3, 4]}],
            },
        ],
    }
]


@pytest.fixture
def provider(monkeypatch):
    p = TeeItUpProvider(
        facility_id="5720",
        x_be_alias="norwood-country-club",
        display_name="Norwood Country Club",
    )
    monkeypatch.setattr(p, "_fetch_json", lambda url: PAYLOAD)
    return p


def search(provider, **kwargs):
    request = SearchRequest(date=date(2026, 11, 11), **kwargs)
    return provider.fetch_availability(request)


def test_queries_the_requested_date(provider, monkeypatch):
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return PAYLOAD

    monkeypatch.setattr(provider, "_fetch_json", fake_fetch)
    search(provider)
    assert "date=2026-11-11" in seen["url"]
    assert "facilityIds=5720" in seen["url"]


def test_utc_slots_are_converted_to_local_time(provider):
    results = search(provider, players=2)
    assert [r["start_time"] for r in results] == [
        time(7, 0),
        time(8, 9),
        time(17, 21),
        time(23, 0),
    ]
    assert {r["date"] for r in results} == {date(2026, 11, 11)}


def test_slots_from_the_next_local_day_are_dropped(provider):
    results = search(provider, players=2)
    assert time(1, 0) not in [r["start_time"] for r in results]
    assert len(results) == 4


def test_time_window_filters_on_local_time(provider):
    results = search(provider, players=2, earliest_time=time(7, 30))
    assert [r["start_time"] for r in results] == [
        time(8, 9),
        time(17, 21),
        time(23, 0),
    ]

    results = search(provider, players=2, latest_time=time(9, 0))
    assert [r["start_time"] for r in results] == [time(7, 0), time(8, 9)]

    results = search(
        provider,
        players=2,
        earliest_time=time(8, 0),
        latest_time=time(9, 0),
    )
    assert [r["start_time"] for r in results] == [time(8, 9)]


def test_player_count_filters_by_allowed_players(provider):
    # The 08:09 slot only allows 1 or 2 players.
    four = search(provider, players=4)
    assert [r["start_time"] for r in four] == [
        time(7, 0),
        time(17, 21),
        time(23, 0),
    ]
    assert all(r["players"] == 4 for r in four)


def test_no_results_when_api_returns_nothing(provider, monkeypatch):
    monkeypatch.setattr(provider, "_fetch_json", lambda url: [])
    assert search(provider) == []

    monkeypatch.setattr(provider, "_fetch_json", lambda url: None)
    assert search(provider) == []


def test_search_returns_options_with_date(provider):
    options = provider.search(SearchRequest(date=date(2026, 11, 11), players=2))
    assert options[0].date == date(2026, 11, 11)
    assert options[0].start_time == time(7, 0)
    assert options[0].provider == "norwood-country-club"


# --- the engine boundary: rows in, providers out -----------------------------


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def scan(self):
        return self.rows


def test_from_row_builds_a_provider_from_a_discovered_row():
    row = {
        "pk": "course#bradford-country-club",
        "name": "Bradford Country Club",
        "display_name": "Bradford Country Club, Haverhill, MA",
        "provider": "teeitup-confirmed",
        "facility_id": "9568",
        "x_be_alias": "bradford-country-club",
        "booking_domain": "bradford-country-club.book.teeitup.golf",
        "lat": "42.7510945",
        "lon": "-71.0673279",
    }
    provider = TeeItUpProvider.from_row(row)
    assert provider.facility_id == "9568"
    assert provider.name == "bradford-country-club"
    assert provider.base_url == "https://bradford-country-club.book.teeitup.golf"
    assert provider.coordinates is not None
    assert provider.display_name.startswith("Bradford Country Club")


def test_from_row_recovers_the_alias_from_the_booking_domain():
    provider = TeeItUpProvider.from_row(
        {
            "pk": "course#x",
            "name": "X",
            "provider": "teeitup-confirmed",
            "facility_id": "1",
            "booking_domain": "x-golf-club.book.teeitup.golf",
        }
    )
    assert provider is not None
    assert provider.x_be_alias == "x-golf-club"
    assert provider.coordinates is None


@pytest.mark.parametrize(
    "row",
    [
        {"pk": "course#a", "provider": "teeitup-confirmed", "x_be_alias": "a"},
        {"pk": "course#b", "provider": "teeitup-confirmed", "facility_id": "9"},
    ],
)
def test_from_row_returns_none_without_the_ids_the_api_needs(row):
    assert TeeItUpProvider.from_row(row) is None


def test_labels_without_an_engine_are_counted_not_dropped():
    rows = [
        {"pk": "course#chrono", "provider": "chronogolf"},
        {"pk": "course#unknown", "provider": "unknown"},
        {
            "pk": "course#ok",
            "provider": "teeitup-confirmed",
            "facility_id": "1",
            "x_be_alias": "ok",
        },
        {"pk": "meta#last_sweep"},
    ]
    assert provider_coverage(rows) == {"chronogolf": 1, "unknown": 1}


def test_two_rows_for_one_facility_become_one_provider():
    rows = [
        {
            "pk": "course#a",
            "name": "A",
            "provider": "teeitup",
            "facility_id": "5720",
            "x_be_alias": "norwood-country-club",
        },
        {
            "pk": "course#b",
            "name": "B",
            "provider": "teeitup-confirmed",
            "facility_id": "5720",
            "x_be_alias": "norwood-cc",
        },
    ]
    providers = get_providers_from_store(FakeStore(rows))
    assert [p.facility_id for p in providers] == ["5720"]


def test_a_new_engine_needs_only_its_own_four_methods(monkeypatch):
    """The base carries pacing, HTTP, headers and option building."""

    class FakeEngine(BookingEngineProvider):
        label = "fake"

        def __init__(self, facility_id: str):
            super().__init__(name="fake-engine", display_name="Fake Municipal")
            self.facility_id = facility_id

        @classmethod
        def from_row(cls, row):
            return cls(row["facility_id"]) if row.get("facility_id") else None

        def availability_url(self, request):
            return f"https://fake.test/{self.facility_id}/{request.date.isoformat()}"

        def parse_slots(self, payload, request):
            return [
                {
                    "date": request.date,
                    "start_time": time(9, 0),
                    "course_name": self.display_name,
                    "players": request.players,
                }
            ]

    monkeypatch.setitem(ENGINE_PROVIDERS, "fake", FakeEngine)
    store = FakeStore(
        [{"pk": "course#fake", "name": "Fake", "provider": "fake", "facility_id": "42"}]
    )

    providers = get_providers_from_store(store)
    assert [p.label for p in providers] == ["fake"]

    provider = providers[0]
    urls: list[str] = []
    monkeypatch.setattr(
        provider, "_fetch_json", lambda url: urls.append(url) or {"slots": [1]}
    )
    options = provider.search(
        SearchRequest(date=date(2026, 11, 11), players=2), distance_miles=3.5
    )

    assert urls == ["https://fake.test/42/2026-11-11"]
    assert [
        (o.course_name, o.start_time, o.provider, o.distance_miles) for o in options
    ] == [("Fake Municipal", time(9, 0), "fake-engine", 3.5)]


def test_a_row_the_adapter_cannot_handle_fails_loudly(monkeypatch):
    """A bug in from_row is not the same thing as "no dynamic providers"."""

    class Broken(BookingEngineProvider):
        label = "broken"

        @classmethod
        def from_row(cls, row):
            raise TypeError("from_row cannot handle this row")

    monkeypatch.setitem(ENGINE_PROVIDERS, "broken", Broken)

    with pytest.raises(TypeError):
        get_providers_from_store(FakeStore([{"pk": "course#b", "provider": "broken"}]))


def test_search_all_propagates_a_catalog_failure(monkeypatch):
    """No silent fall back to the static list: the request fails and logs."""

    class FakeGeo:
        def zip(self, zip_code, geocoder=None):
            return None

        def course(self, facility_id):
            return None

    def boom(course_store=None):
        raise TypeError("catalog build failed")

    monkeypatch.setattr("app.providers.get_search_providers", boom)

    with pytest.raises(TypeError):
        search_all(
            SearchRequest(date=date(2026, 11, 11), players=2),
            store=FakeGeo(),
            course_store=object(),
        )


def test_terminal_labels_are_not_coverage_gaps():
    """golfnow and members-only are not engines; no adapter would unlock them."""
    rows = [
        {"pk": "course#members", "provider": "members-only"},
        {"pk": "course#marketplace", "provider": "golfnow"},
        {"pk": "course#cps", "provider": "cps-golf"},
        {"pk": "course#unknown", "provider": "unknown"},
    ]
    assert provider_coverage(rows) == {"cps-golf": 1, "unknown": 1}


def test_no_course_store_means_no_catalog():
    """There is no built-in course list to fall back on any more."""
    assert get_search_providers() == []


def test_foreup_from_row_reads_the_ids_the_fingerprint_stored():
    """A complete row is enough: from_row must not need the booking page."""
    provider = ForeUpProvider.from_row(
        {
            "pk": "course#robert-t-lynch-municipal-golf-course",
            "name": "Robert T. Lynch Municipal Golf Course",
            "display_name": "Robert T. Lynch Municipal, Brookline, MA",
            "provider": "foreup",
            "foreup_course_id": "19865",
            "foreup_schedule_id": "2748",
            "lat": "42.3179",
            "lon": "-71.1326",
        }
    )

    assert provider.course_id == "19865"
    assert provider.schedule_id == "2748"
    assert provider.course_key() == "19865/2748"
    assert provider.coordinates.lat == 42.3179
    assert provider.booking_url().endswith("/booking/19865/2748")

    url = provider.availability_url(SearchRequest(date=date(2026, 5, 14), players=2))
    assert "date=05-14-2026" in url
    assert "schedule_id=2748" in url


def test_foreup_from_row_needs_both_ids():
    """One id cannot ask for tee times: the API rejects a course id as schedule."""
    only_course = {
        "pk": "course#ould-newbury-golf-club",
        "provider": "foreup",
        "foreup_course_id": "19969",
    }
    only_schedule = {
        "pk": "course#half",
        "provider": "foreup",
        "foreup_schedule_id": "2748",
    }
    assert ForeUpProvider.from_row(only_course) is None
    assert ForeUpProvider.from_row(only_schedule) is None


# Trimmed from live foreup tee sheets on 2026-11-21 (Babe Zaharias 19717/2254
# and Sunkist 21874/9160). ``time`` is course-local wall clock and
# ``allowed_group_sizes`` arrives as strings.
FOREUP_TEE_TIMES = [
    {
        "time": "2026-11-21 06:50",
        "start_front": 202610210650,
        "course_id": 19717,
        "course_name": "Babe Zaharias Golf Course",
        "schedule_id": 2254,
        "teesheet_id": 2254,
        "available_spots": 4,
        "available_spots_18": 4,
        "maximum_players_per_booking": 4,
        "minimum_players": 1,
        "allowed_group_sizes": ["3", "4"],
        "holes": 18,
        "green_fee": 25,
    },
    {
        "time": "2026-11-21 07:40",
        "course_id": 19717,
        "course_name": "Babe Zaharias Golf Course",
        "schedule_id": 2254,
        "available_spots": 4,
        "allowed_group_sizes": ["3", "4"],
        "holes": 18,
    },
    {
        "time": "2026-11-21 06:52",
        "course_id": 21874,
        "course_name": "Sunkist Country Club",
        "schedule_id": 9160,
        "available_spots": 2,
        "allowed_group_sizes": ["1", "2", "3", "4"],
        "holes": "9/18",
    },
    {
        "time": "2026-11-22 08:00",
        "course_id": 21874,
        "course_name": "Sunkist Country Club",
        "schedule_id": 9160,
        "available_spots": 4,
        "allowed_group_sizes": ["1", "2", "3", "4"],
        "holes": "9/18",
    },
]


def _foreup(**kwargs):
    return ForeUpProvider(
        course_id="19717",
        schedule_id="2254",
        display_name="Babe Zaharias Golf Course",
        **kwargs,
    )


def test_foreup_parses_a_live_tee_sheet_payload():
    """The payload read from a live foreup tee sheet becomes slots."""
    slots = _foreup().parse_slots(
        FOREUP_TEE_TIMES, SearchRequest(date=date(2026, 11, 21), players=2)
    )

    assert [
        (slot["start_time"], slot["course_name"], slot["players"]) for slot in slots
    ] == [(time(6, 52), "Sunkist Country Club", 2)]


def test_foreup_honours_the_group_sizes_and_seats_a_slot_has():
    """Babe Zaharias books only 3 and 4 and its 06:50 slot seats 4; Sunkist takes 2."""
    provider = _foreup()
    request = SearchRequest(date=date(2026, 11, 21), players=4)

    assert [
        slot["start_time"] for slot in provider.parse_slots(FOREUP_TEE_TIMES, request)
    ] == [time(6, 50), time(7, 40)]

    # A two-ball cannot take a slot that only admits 3 or 4, and the Sunkist
    # 06:52 slot has two seats while its 11/22 slot is another day.
    two = SearchRequest(date=date(2026, 11, 21), players=2)
    assert [
        slot["course_name"] for slot in provider.parse_slots(FOREUP_TEE_TIMES, two)
    ] == ["Sunkist Country Club"]


def test_foreup_filters_by_window_and_date():
    request = SearchRequest(
        date=date(2026, 11, 21), players=4, earliest_time=time(7, 0)
    )

    assert [
        slot["start_time"] for slot in _foreup().parse_slots(FOREUP_TEE_TIMES, request)
    ] == [time(7, 40)]


def test_foreup_raises_on_a_payload_it_does_not_know():
    provider = _foreup()
    request = SearchRequest(date=date(2026, 11, 21), players=4)

    # The API answers ``false`` for an unknown schedule id.
    with pytest.raises(ValueError, match="not a tee-time list"):
        provider.parse_slots(False, request)
    with pytest.raises(ValueError, match="no usable time"):
        provider.parse_slots([{"course_name": "Babe Zaharias"}], request)


def test_foreup_is_registered_and_builds_options_end_to_end(monkeypatch):
    assert ENGINE_PROVIDERS["foreup"] is ForeUpProvider

    provider = ForeUpProvider.from_row(
        {
            "pk": "course#robert-t-lynch-municipal-golf-course",
            "provider": "foreup",
            "name": "Robert T. Lynch Municipal Golf Course",
            "foreup_course_id": "19865",
            "foreup_schedule_id": "2748",
        }
    )
    monkeypatch.setattr(provider, "_fetch_json", lambda url: FOREUP_TEE_TIMES)

    options = provider.search(
        SearchRequest(date=date(2026, 11, 21), players=4), distance_miles=2.5
    )

    assert [option.provider for option in options] == ["foreup-19865"] * 2
    assert options[0].booking_url == (
        "https://foreupsoftware.com/index.php/booking/19865/2748"
    )
    assert options[0].distance_miles == 2.5
