"""Tests for the TeeItUp availability parser.

The fixture mirrors the real API response shape: a list of day objects whose
``teetimes[].teetime`` values are UTC instants (``...Z``) and whose
``rates[].allowedPlayers`` gates which party sizes can book each slot.
"""

from datetime import date, time

import pytest

from app.models import SearchRequest
from app.providers import NorwoodCCProvider

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
    p = NorwoodCCProvider()
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
