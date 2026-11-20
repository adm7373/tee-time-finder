"""Tests for dynamic TeeItUp course handling, DynamoDB seeding, and search integration."""

from datetime import date
import pytest

from app.discovery import CourseStore, discover
from app import fingerprint as fp
from app.fingerprint_handler import handler as fingerprint_handler
from app.geo import Coordinates, GeocodeStore
from app.models import SearchRequest
from app.providers import (
    TeeItUpProvider,
    get_providers_from_store,
    search_all,
)
from app.seed_data import EXISTING_COURSES, seed_courses
from app.teeitup_probe import confirm_course


class FakeTable:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def get_item(self, Key):
        item = self.items.get(Key["pk"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item["pk"]] = Item

    def scan(self, **kwargs):
        items = list(self.items.values())
        return {"Items": items}


class FakeGeocoder:
    def __init__(self, origin, places):
        self.origin = origin
        self.places = places

    def by_zip(self, zip_code):
        return self.origin

    def golf_course_places(self, origin, radius_miles):
        return self.places


QUINCY = Coordinates(lat=42.2461701, lon=-71.0057746, display_name="02169, Quincy, MA")
NORWOOD_COORDS = Coordinates(
    lat=42.1986317, lon=-71.1820973, display_name="Norwood Country Club, Norwood, MA"
)


# --- (a) Lambdas finding needed details for TeeItUp courses ------------------


def test_confirm_course_sends_x_be_alias_header(monkeypatch):
    captured = {}

    def fake_paced_get(url, accept="application/json", headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return 200, '[{"courseId": "test-cid-123"}]'

    monkeypatch.setattr("app.teeitup_probe.paced_get", fake_paced_get)

    res = confirm_course("norwood-country-club", "5720", "2026-11-12")
    assert res == "test-cid-123"
    assert "facilityIds=5720" in captured["url"]
    assert captured["headers"] == {"x-be-alias": "norwood-country-club"}


def test_discovery_probe_records_x_be_alias_and_geocodes(monkeypatch):
    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: "5720")
    monkeypatch.setattr(
        "app.discovery.confirm_course", lambda alias, fid, d: "test-cid-1"
    )

    from app.geo import CoursePlace

    place = CoursePlace(
        name="Norwood Country Club",
        coords=NORWOOD_COORDS,
        website="",
        display_name="Norwood CC, Norwood, MA",
    )
    geocoder = FakeGeocoder(QUINCY, [place])
    table = FakeTable()
    course_store = CourseStore("c", table=table)
    geo_table = FakeTable()
    geocode_store = GeocodeStore("g", table=geo_table)

    result = discover(
        "02169",
        25.0,
        store=course_store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
    )

    assert result.tee_it_up == 1
    row = course_store.get("Norwood Country Club")
    assert row is not None
    assert row["provider"] == "teeitup-confirmed"
    assert row["facility_id"] == "5720"
    assert row["x_be_alias"] == "norwood-country-club"
    assert row["booking_domain"] == "norwood-country-club.book.teeitup.golf"
    assert row["course_id"] == "test-cid-1"

    # Coordinates cached into geocode store
    cached_coords = geocode_store.course("5720")
    assert cached_coords is not None
    assert cached_coords.lat == pytest.approx(NORWOOD_COORDS.lat)
    assert cached_coords.lon == pytest.approx(NORWOOD_COORDS.lon)


def test_fingerprint_finds_details_from_redirect(monkeypatch):
    monkeypatch.setattr(
        fp,
        "_fetch",
        lambda url: (
            "https://rockland-golf-course.book.teeitup.golf/",
            '<html data-gnFacilityIds="[11063]"></html>',
        ),
    )
    monkeypatch.setattr(fp, "_teeitup_facility_id", lambda body: "11063")

    course = {
        "name": "Rockland Golf Course",
        "website": "https://rocklandgolf.com",
        "display_name": "Rockland Golf Course, Rockland, MA",
        "provider": "unknown",
    }
    verdict = fp.fingerprint(course)
    assert verdict.provider == "teeitup"
    assert verdict.facility_id == "11063"
    assert verdict.x_be_alias == "rockland-golf-course"
    assert verdict.booking_domain == "rockland-golf-course.book.teeitup.golf"


def test_fingerprint_finds_details_from_embedded_widget(monkeypatch):
    page_html = """
    <html><body>
    <div>Book online below:</div>
    <iframe src="https://pinehills-golf-club.book.teeitup.golf/widget"></iframe>
    </body></html>
    """
    monkeypatch.setattr(fp, "_fetch", lambda url: (url, page_html))
    monkeypatch.setattr(fp, "facility_id_for", lambda alias: "4981")

    course = {
        "name": "Pinehills Golf Club",
        "website": "https://pinehillsgolf.com",
        "display_name": "Pinehills Golf Club, Plymouth, MA",
        "provider": "unknown",
    }
    verdict = fp.fingerprint(course)
    assert verdict.provider == "teeitup"
    assert verdict.x_be_alias == "pinehills-golf-club"
    assert verdict.facility_id == "4981"
    assert verdict.booking_domain == "pinehills-golf-club.book.teeitup.golf"


def test_fingerprint_handler_updates_dynamodb_with_alias_and_facility(monkeypatch):
    table = FakeTable()
    table.items["course#presidents-golf-course"] = {
        "pk": "course#presidents-golf-course",
        "name": "Presidents Golf Course",
        "lat": "42.2699707",
        "lon": "-71.0424722",
        "provider": "unknown",
        "website": "https://presidentsgc.com",
    }
    course_store = CourseStore("courses", table=table)

    monkeypatch.setattr(
        fp,
        "_fetch",
        lambda url: (
            "https://presidents-golf-course.book.teeitup.golf/",
            "<html>Next.js self.__next_f.push([1,\"gnFacilityIds:[17943]\"])</html>",
        ),
    )
    monkeypatch.setattr(fp, "_teeitup_facility_id", lambda body: "17943")

    from app.config import Settings

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: Settings(
            _env_file=None,
            courses_table_name="courses",
            geocode_table_name="geocode",
        ),
    )
    monkeypatch.setattr(
        "app.discovery.CourseStore", lambda name: course_store
    )

    geo_table = FakeTable()
    monkeypatch.setattr("app.geo.GeocodeStore", lambda name, table=None: GeocodeStore(name, table=geo_table))

    res = fingerprint_handler(event={"limit": 5})
    assert res["classified"] == 1

    updated = table.items["course#presidents-golf-course"]
    assert updated["provider"] == "teeitup"
    assert updated["facility_id"] == "17943"
    assert updated["x_be_alias"] == "presidents-golf-course"
    assert updated["booking_domain"] == "presidents-golf-course.book.teeitup.golf"


# --- (b) Moving existing TeeItUp courses into DynamoDB -----------------------


def test_existing_courses_count_and_fields():
    assert len(EXISTING_COURSES) == 18
    for course in EXISTING_COURSES:
        assert course["facility_id"]
        assert course["x_be_alias"]
        assert course["name"]
        assert "lat" in course and "lon" in course


def test_seed_courses_populates_both_stores():
    course_table = FakeTable()
    course_store = CourseStore("courses", table=course_table)
    geo_table = FakeTable()
    geocode_store = GeocodeStore("geocode", table=geo_table)

    seeded = seed_courses(course_store, geocode_store)
    assert len(seeded) == 18
    assert len(course_table.items) == 18
    assert len(geo_table.items) == 18

    norwood = course_table.items["course#norwood-country-club"]
    assert norwood["facility_id"] == "5720"
    assert norwood["x_be_alias"] == "norwood-country-club"
    assert norwood["booking_domain"] == "norwood-country-club.book.teeitup.golf"
    assert norwood["provider"] == "teeitup"
    assert norwood["distance_from_zip"] == 9.6

    geo_norwood = geocode_store.course("5720")
    assert geo_norwood is not None
    assert geo_norwood.lat == pytest.approx(42.1986317)


# --- Search functionality connected to DynamoDB -----------------------------


def test_dynamic_teeitup_provider_search(monkeypatch):
    provider = TeeItUpProvider(
        facility_id="5720",
        x_be_alias="norwood-country-club",
        display_name="Norwood Country Club",
        coordinates=NORWOOD_COORDS,
    )
    assert provider.facility_id == "5720"
    assert provider.x_be_alias == "norwood-country-club"
    assert provider.name == "norwood-country-club"
    assert provider.base_url == "https://norwood-country-club.book.teeitup.golf"

    # Mock availability
    sample_payload = [
        {
            "teetimes": [
                {
                    "teetime": "2026-11-12T13:00:00.000Z",
                    "rates": [{"allowedPlayers": [1, 2, 3, 4]}],
                }
            ]
        }
    ]
    monkeypatch.setattr(provider, "_fetch_json", lambda url: sample_payload)

    req = SearchRequest(date=date(2026, 11, 12), players=2)
    results = provider.search(req, distance_miles=9.6)
    assert len(results) == 1
    assert results[0].course_name == "Norwood Country Club"
    assert results[0].provider == "norwood-country-club"
    assert results[0].distance_miles == 9.6


def test_get_providers_from_store_creates_configured_providers():
    table = FakeTable()
    course_store = CourseStore("courses", table=table)
    seed_courses(course_store)

    providers = get_providers_from_store(course_store)
    assert len(providers) == 18
    aliases = {p.x_be_alias for p in providers}
    assert "norwood-country-club" in aliases
    assert "presidents-golf-course" in aliases
    assert "pinehills-golf-club" in aliases


def test_search_all_uses_course_store(monkeypatch):
    table = FakeTable()
    course_store = CourseStore("courses", table=table)
    table.items["course#norwood-country-club"] = {
        "pk": "course#norwood-country-club",
        "name": "Norwood Country Club",
        "display_name": "Norwood Country Club",
        "facility_id": "5720",
        "x_be_alias": "norwood-country-club",
        "provider": "teeitup",
        "lat": "42.1986317",
        "lon": "-71.1820973",
    }

    # Near Norwood (within 10 miles of 02169)
    sample_slot = [
        {
            "teetimes": [
                {
                    "teetime": "2026-11-12T14:00:00.000Z",
                    "rates": [{"allowedPlayers": [2]}],
                }
            ]
        }
    ]
    monkeypatch.setattr(
        "app.providers.TeeItUpProvider._fetch_json", lambda self, url: sample_slot
    )

    geo_table = FakeTable()
    geocode_store = GeocodeStore("g", table=geo_table)
    geocode_store.store_zip("02169", QUINCY)

    req = SearchRequest(date=date(2026, 11, 12), players=2, radius_miles=15.0)
    options = search_all(req, store=geocode_store, course_store=course_store)

    assert len(options) == 1
    assert options[0].course_name == "Norwood Country Club"
    assert options[0].distance_miles == pytest.approx(9.6, abs=0.2)

    # Outside radius: radius=5 miles should filter out Norwood (~9.6 mi)
    req_small = SearchRequest(date=date(2026, 11, 12), players=2, radius_miles=5.0)
    options_small = search_all(
        req_small, store=geocode_store, course_store=course_store
    )
    assert len(options_small) == 0
