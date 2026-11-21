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
    ChronogolfProvider,
    CpsGolfProvider,
    ForeUpProvider,
    TeeItUpProvider,
    get_providers_from_store,
    get_search_providers,
    provider_coverage,
    search_all,
)

# Trimmed from a live cps.golf tee sheet (chattanooga, 2026-11-21): the API
# answers a bare JSON array, startTime is course-local with no offset, and each
# tee time carries the party sizes it can take.
CPS_TEE_TIMES = [
    {
        "teeSheetId": 436451,
        "startTime": "2026-11-21T14:24:00",
        "courseId": 2,
        "courseDate": "2026-11-21T00:00:00",
        "holes": 9,
        "courseName": "Brown Acres",
        "participants": 4,
        "minPlayer": 1,
        "maxPlayer": 4,
        "availableParticipantNo": [1, 2, 3, 4],
    },
    {
        "teeSheetId": 438283,
        "startTime": "2026-11-21T14:40:00",
        "courseId": 1,
        "courseDate": "2026-11-21T00:00:00",
        "holes": 9,
        # The live payload really does pad some course names.
        "courseName": "Brainerd ",
        "participants": 1,
        "minPlayer": 1,
        "maxPlayer": 1,
        "availableParticipantNo": [1],
    },
    {
        "teeSheetId": 438290,
        "startTime": "2026-11-22T09:00:00",
        "courseId": 1,
        "courseDate": "2026-11-22T00:00:00",
        "courseName": "Brainerd",
        "availableParticipantNo": [1, 2],
    },
]

CPS_NO_TEE_TIMES = {
    "transactionId": "f875f1f3-4734-431a-ac8f-cbab0a0b222b",
    "isSuccess": True,
    "content": {
        "messageKey": "NO_TEETIMES",
        "messageTemplate": "No tee times available",
        "messageType": "Attention",
    },
}

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
        {"pk": "course#caddie", "provider": "clubcaddie"},
        {"pk": "course#phone", "provider": "phone-only"},
        {"pk": "course#unknown", "provider": "unknown"},
        {
            "pk": "course#ok",
            "provider": "teeitup-confirmed",
            "facility_id": "1",
            "x_be_alias": "ok",
        },
        {"pk": "meta#last_sweep"},
    ]
    assert provider_coverage(rows) == {"clubcaddie": 1, "unknown": 1}


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


def _cps(**kwargs):
    """A provider for the tenant the live payload was read from."""
    return CpsGolfProvider(
        site="chattanooga", course_ids="1,2", display_name="Chattanooga", **kwargs
    )


def test_cps_parses_the_verified_tee_time_payload():
    """The payload read from a live tee sheet becomes slots, not a guess."""
    slots = _cps().parse_slots(
        CPS_TEE_TIMES, SearchRequest(date=date(2026, 11, 21), players=2)
    )

    assert [
        (slot["start_time"], slot["course_name"], slot["players"]) for slot in slots
    ] == [(time(14, 24), "Brown Acres", 2)]


def test_cps_filters_by_date_window_and_party_size():
    provider = _cps()
    later = SearchRequest(
        date=date(2026, 11, 21), players=1, earliest_time=time(14, 30)
    )
    # 14:24 is before the window; the 11/22 tee time is another day.
    assert [s["start_time"] for s in provider.parse_slots(CPS_TEE_TIMES, later)] == [
        time(14, 40)
    ]

    earlier = SearchRequest(
        date=date(2026, 11, 21), players=1, latest_time=time(14, 30)
    )
    assert [s["course_name"] for s in provider.parse_slots(CPS_TEE_TIMES, earlier)] == [
        "Brown Acres"
    ]

    # Brainerd takes one player only; Brown Acres takes up to four.
    three = SearchRequest(date=date(2026, 11, 21), players=3)
    assert [s["start_time"] for s in provider.parse_slots(CPS_TEE_TIMES, three)] == [
        time(14, 24)
    ]


def test_cps_treats_the_no_tee_times_message_as_empty():
    """A closed day answers an object, not an array; that is empty, not an error."""
    slots = _cps().parse_slots(
        CPS_NO_TEE_TIMES, SearchRequest(date=date(2026, 11, 21), players=2)
    )

    assert slots == []


def test_cps_raises_on_a_payload_it_does_not_know():
    provider = _cps()
    request = SearchRequest(date=date(2026, 11, 21))

    with pytest.raises(ValueError, match="not tee times"):
        provider.parse_slots(
            {"isSuccess": True, "content": {"messageKey": "SOMETHING_NEW"}}, request
        )
    with pytest.raises(ValueError, match="not a tee-time list"):
        provider.parse_slots("false", request)
    with pytest.raises(ValueError, match="no usable startTime"):
        provider.parse_slots([{"courseName": "Brown Acres"}], request)


def test_cps_from_row_reads_the_tenant_and_course_ids():
    provider = CpsGolfProvider.from_row(
        {
            "pk": "course#meadow-at-peabody-golf-course",
            "provider": "cps-golf",
            "name": "Meadow at Peabody Golf Course",
            "display_name": "Meadow at Peabody Golf Course, Peabody, MA",
            "cps_site": "peabody",
            "cps_course_ids": "1",
            "lat": "42.5290",
            "lon": "-70.9450",
        }
    )

    assert provider is not None
    assert provider.course_key() == "peabody/1"
    assert provider.coordinates.lat == 42.529
    assert (
        provider.booking_url() == "https://peabody.cps.golf/onlineresweb/search-teetime"
    )

    url = provider.availability_url(SearchRequest(date=date(2026, 11, 21), players=2))
    assert url.startswith(
        "https://peabody.cps.golf/onlineres/onlineapi/api/v1/onlinereservation/TeeTimes?"
    )
    assert "searchDate=Sat+Nov+21+2026" in url
    assert "courseIds=1" in url


def test_cps_from_row_can_take_the_tenant_from_the_booking_link():
    provider = CpsGolfProvider.from_row(
        {
            "pk": "course#beverly-golf-tennis-club",
            "provider": "cps-golf",
            "booking_link": "https://beverlygolf.cps.golf/onlineresweb/search-teetime",
            "cps_course_ids": "1",
        }
    )

    assert provider is not None
    assert provider.site == "beverlygolf"


def test_cps_from_row_needs_both_the_tenant_and_course_ids():
    """The endpoint books across named courses; an unnamed request is no request."""
    assert (
        CpsGolfProvider.from_row({"provider": "cps-golf", "cps_site": "peabody"})
        is None
    )
    assert (
        CpsGolfProvider.from_row({"provider": "cps-golf", "cps_course_ids": "1"})
        is None
    )


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


# Trimmed from live chronogolf marketplace pages (Sassamon Trace and Nehoiden,
# 2026-11-21). ``date``/``start_time`` are course-local; ``starts_at`` is UTC.
CHRONOGOLF_PAGE = {
    "status": "open",
    "teetimes": [
        {
            "id": 534576124,
            "uuid": "cd2ef7c9-9970-4ebf-97ab-271288797617",
            "course": {
                "id": 8444,
                "bookable_holes": [9],
                "holes": 9,
                "name": "Sassamon Trace",
                "uuid": "46e18274-b0e5-4138-9df4-ccf32b2fc4e1",
            },
            "hole": 1,
            "has_cart": False,
            "min_player_size": 1,
            "max_player_size": 1,
            "starts_at": "2026-11-21T16:40:00Z",
            "start_time": "11:40",
            "date": "2026-11-21",
            "event_id": None,
            "has_deal": True,
            "default_price": {
                "green_fee": 32.0,
                "half_cart": 12.23,
                "one_person_cart": None,
                "player_type_id": 30410,
                "subtotal": 32.0,
                "bookable_holes": 9,
                "affiliation_type": "Public",
            },
            "departure": None,
            "format": "normal",
            "frozen": False,
        },
        {
            "id": 534576201,
            "uuid": "7c0b5d1d-6f0f-4f3a-9d2e-2a0c8b3a5f11",
            "course": {
                "id": 8388,
                "bookable_holes": [9, 18],
                "holes": 9,
                "name": "Nehoiden",
                "uuid": "a7deeef4-5562-4d4c-b71a-48698badfdd6",
            },
            "hole": 1,
            "min_player_size": 1,
            "max_player_size": 4,
            "starts_at": "2026-11-21T17:10:00Z",
            "start_time": "12:10",
            "date": "2026-11-21",
            "default_price": {"green_fee": 45.0, "affiliation_type": "Guest"},
            "frozen": False,
        },
        {
            "id": 534576290,
            "uuid": "b5a1e3f4-0a2b-4c6d-8e9f-1a2b3c4d5e6f",
            "course": {
                "id": 8388,
                "bookable_holes": [9, 18],
                "holes": 9,
                "name": "Nehoiden",
                "uuid": "a7deeef4-5562-4d4c-b71a-48698badfdd6",
            },
            "hole": 1,
            "min_player_size": 1,
            "max_player_size": 4,
            "starts_at": "2026-11-22T13:00:00Z",
            "start_time": "08:00",
            "date": "2026-11-22",
            "default_price": {"green_fee": 45.0, "affiliation_type": "Guest"},
            "frozen": False,
        },
    ],
}


def _chronogolf(**kwargs):
    return ChronogolfProvider(
        course_ids="46e18274-b0e5-4138-9df4-ccf32b2fc4e1",
        club_slug="sassamon-trace",
        display_name="Sassamon Trace Golf Course",
        **kwargs,
    )


def test_chronogolf_from_row_reads_the_course_uuid_the_row_stored():
    provider = ChronogolfProvider.from_row(
        {
            "pk": "course#sassamon-trace-golf-course",
            "provider": "chronogolf",
            "name": "Sassamon Trace Golf Course",
            "cg_course_ids": "46e18274-b0e5-4138-9df4-ccf32b2fc4e1",
            "cg_slug": "sassamon-trace",
        }
    )

    assert provider.course_ids == "46e18274-b0e5-4138-9df4-ccf32b2fc4e1"
    assert provider.course_key() == "46e18274-b0e5-4138-9df4-ccf32b2fc4e1"
    assert provider.booking_url() == "https://www.chronogolf.com/club/sassamon-trace"


def test_chronogolf_from_row_needs_the_course_uuid():
    """The endpoint answers ``closed`` for an unknown uuid, so the id is required."""
    assert ChronogolfProvider.from_row({"provider": "chronogolf"}) is None

    # A slug alone still builds no provider: it cannot ask for tee times.
    only_slug = ChronogolfProvider.from_row(
        {"provider": "chronogolf", "cg_slug": "sassamon-trace"}
    )
    assert only_slug is None


def test_chronogolf_reads_the_slug_from_a_stored_booking_link():
    provider = ChronogolfProvider.from_row(
        {
            "provider": "chronogolf",
            "cg_course_ids": "46e18274-b0e5-4138-9df4-ccf32b2fc4e1",
            "booking_link": "https://www.chronogolf.com/club/nehoiden-golf-club",
        }
    )

    assert provider.club_slug == "nehoiden-golf-club"


def test_chronogolf_parses_a_live_tee_sheet_page():
    slots = _chronogolf().parse_slots(
        CHRONOGOLF_PAGE, SearchRequest(date=date(2026, 11, 21), players=1)
    )

    assert [(slot["start_time"], slot["course_name"]) for slot in slots] == [
        (time(11, 40), "Sassamon Trace"),
        (time(12, 10), "Nehoiden"),
    ]


def test_chronogolf_honours_the_player_range_each_slot_has():
    # Sassamon's 11:40 slot seats one; Nehoiden's 12:10 seats up to four.
    slots = _chronogolf().parse_slots(
        CHRONOGOLF_PAGE, SearchRequest(date=date(2026, 11, 21), players=2)
    )

    assert [slot["course_name"] for slot in slots] == ["Nehoiden"]


def test_chronogolf_filters_by_window_and_date():
    request = SearchRequest(
        date=date(2026, 11, 21), players=1, earliest_time=time(12, 0)
    )

    assert [
        slot["start_time"]
        for slot in _chronogolf().parse_slots(CHRONOGOLF_PAGE, request)
    ] == [time(12, 10)]

    # The 11/22 slot belongs to another day.
    next_day = SearchRequest(date=date(2026, 11, 22), players=1)
    assert [
        slot["start_time"]
        for slot in _chronogolf().parse_slots(CHRONOGOLF_PAGE, next_day)
    ] == [time(8, 0)]


def test_chronogolf_raises_on_a_page_it_does_not_know():
    provider = _chronogolf()
    request = SearchRequest(date=date(2026, 11, 21), players=1)

    with pytest.raises(ValueError, match="not a tee-time page"):
        provider.parse_slots([], request)
    with pytest.raises(ValueError, match="no teetimes list"):
        provider.parse_slots({"status": "closed"}, request)
    with pytest.raises(ValueError, match="no usable date/start_time"):
        provider.parse_slots({"status": "open", "teetimes": [{"id": 1}]}, request)


def test_chronogolf_walks_pages_until_a_page_is_empty(monkeypatch):
    """The endpoint answers 24 slots at a time, so a full day spans pages."""
    second_page = {
        "status": "open",
        "teetimes": [
            dict(
                CHRONOGOLF_PAGE["teetimes"][1],
                id=534576300,
                start_time="14:00",
                starts_at="2026-11-21T19:00:00Z",
            )
        ],
    }
    pages = [CHRONOGOLF_PAGE, second_page, {"status": "open", "teetimes": []}]
    urls: list[str] = []

    def fake_fetch(url):
        urls.append(url)
        return pages[len(urls) - 1]

    provider = _chronogolf()
    monkeypatch.setattr(provider, "_fetch_json", fake_fetch)

    slots = provider.fetch_availability(
        SearchRequest(date=date(2026, 11, 21), players=1)
    )

    assert [slot["start_time"] for slot in slots] == [
        time(11, 40),
        time(12, 10),
        time(14, 0),
    ]
    assert [url.split("page=")[1] for url in urls] == ["1", "2", "3"]


def test_chronogolf_is_registered_and_builds_options_end_to_end(monkeypatch):
    assert ENGINE_PROVIDERS["chronogolf"] is ChronogolfProvider

    provider = ChronogolfProvider.from_row(
        {
            "pk": "course#sassamon-trace-golf-course",
            "provider": "chronogolf",
            "name": "Sassamon Trace Golf Course",
            "cg_course_ids": "46e18274-b0e5-4138-9df4-ccf32b2fc4e1",
            "cg_slug": "sassamon-trace",
        }
    )
    monkeypatch.setattr(provider, "_fetch_json", lambda url: CHRONOGOLF_PAGE)

    options = provider.search(
        SearchRequest(date=date(2026, 11, 21), players=1), distance_miles=2.5
    )

    assert [option.provider for option in options] == ["sassamon-trace"] * 2
    assert options[0].booking_url == "https://www.chronogolf.com/club/sassamon-trace"


def test_chronogolf_reads_the_unpadded_hour_live_sheets_send():
    """Sassamon Trace's 11/23 sheet sends ``start_time: "9:20"``."""
    payload = {
        "status": "open",
        "teetimes": [
            {
                "course": {"name": "Sassamon Trace"},
                "start_time": "9:20",
                "date": "2026-11-23",
                "min_player_size": 1,
                "max_player_size": 2,
            }
        ],
    }

    slots = _chronogolf().parse_slots(
        payload, SearchRequest(date=date(2026, 11, 23), players=2)
    )

    assert [slot["start_time"] for slot in slots] == [time(9, 20)]


OLDE_SCOTLAND_UUID = "7c60cf72-5f0a-45cc-bfcb-2075f12c45ab"


def test_teeitup_uuid_alias_is_named_after_the_course():
    """Olde Scotland's tenant is a uuid: it must not surface as the provider name."""
    provider = TeeItUpProvider(
        facility_id="15419",
        x_be_alias=OLDE_SCOTLAND_UUID,
        display_name="Olde Scotland Links",
    )

    assert provider.name == "Olde Scotland Links"
    # The API is still called with the uuid, exactly as before.
    assert provider.request_headers()["x-be-alias"] == OLDE_SCOTLAND_UUID
    assert provider.base_url == f"https://{OLDE_SCOTLAND_UUID}.book.teeitup.golf"
    assert "facilityIds=15419" in provider.availability_url(
        SearchRequest(date=date(2026, 11, 21), players=2)
    )


def test_teeitup_uuid_alias_without_a_course_name_gets_a_short_id():
    provider = TeeItUpProvider(facility_id="15419", x_be_alias=OLDE_SCOTLAND_UUID)

    assert provider.name == "teeitup-7c60cf72"


def test_teeitup_slug_aliases_keep_their_slug_as_the_provider_name():
    provider = TeeItUpProvider(
        facility_id="3920",
        x_be_alias="indian-ridge-golf-course",
        display_name="Indian Ridge Golf Club",
    )

    assert provider.name == "indian-ridge-golf-course"


def test_teeitup_row_with_a_uuid_alias_builds_readable_options(monkeypatch):
    provider = TeeItUpProvider.from_row(
        {
            "pk": "course#olde-scotland-links",
            "provider": "teeitup",
            "name": "Olde Scotland Links",
            "facility_id": "15419",
            "x_be_alias": OLDE_SCOTLAND_UUID,
        }
    )
    monkeypatch.setattr(
        provider,
        "_fetch_json",
        lambda url: [
            {
                "teetimes": [
                    {
                        "teetime": "2026-11-21T16:40:00Z",
                        "rates": [{"allowedPlayers": [1, 2]}],
                    }
                ]
            }
        ],
    )

    options = provider.search(SearchRequest(date=date(2026, 11, 21), players=2), 1.0)

    assert [option.provider for option in options] == ["Olde Scotland Links"]


def test_teeitup_uses_the_course_name_not_the_geocoded_address(monkeypatch):
    """Rows carry a geocoded display_name; results and names must use ``name``."""
    provider = TeeItUpProvider.from_row(
        {
            "pk": "course#olde-scotland-links",
            "provider": "teeitup",
            "name": "Olde Scotland Links",
            "display_name": "Olde Scotland Links, 679, Spruce Street, Scotland Estates, "
            "Bridgewater, Plymouth County, Massachusetts, 02324, United States",
            "facility_id": "15419",
            "x_be_alias": OLDE_SCOTLAND_UUID,
        }
    )
    monkeypatch.setattr(
        provider,
        "_fetch_json",
        lambda url: [
            {
                "teetimes": [
                    {
                        "teetime": "2026-11-21T16:40:00Z",
                        "rates": [{"allowedPlayers": [2]}],
                    }
                ]
            }
        ],
    )

    options = provider.search(SearchRequest(date=date(2026, 11, 21), players=2), 0.0)

    assert provider.name == "Olde Scotland Links"
    assert [option.course_name for option in options] == ["Olde Scotland Links"]
    assert [option.provider for option in options] == ["Olde Scotland Links"]


def test_hidden_rows_build_no_provider_and_are_not_a_coverage_gap():
    rows = [
        {
            "pk": "course#hidden",
            "provider": "teeitup-confirmed",
            "facility_id": "1",
            "x_be_alias": "hidden-club",
            "hidden": True,
        },
        {
            "pk": "course#visible",
            "provider": "teeitup-confirmed",
            "facility_id": "2",
            "x_be_alias": "visible-club",
        },
        {"pk": "course#pitch", "provider": "unknown", "hidden": True},
    ]

    class Store:
        def scan(self):
            return rows

    providers = get_providers_from_store(Store())

    assert [provider.name for provider in providers] == ["visible-club"]
    assert provider_coverage(rows) == {}
