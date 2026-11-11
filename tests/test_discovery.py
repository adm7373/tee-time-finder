"""Tests for the discovery and fingerprint Lambdas.

Both Lambdas touch the network and DynamoDB in production, so every external
edge is faked here: the geocoder returns canned places, the TeeItUp probe is
stubbed, and the tables are in-memory dicts. What is pinned is the decision
logic — what gets a row, what counts as what, and what the fingerprint verdicts
look like.
"""

from app import fingerprint as fp
from app.discovery import CourseStore, DiscoveryResult, discover
from app.geo import Coordinates


class FakeTable:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def get_item(self, Key):  # noqa: N803 - mirrors boto3's signature
        item = self.items.get(Key["pk"])
        return {"Item": item} if item else {}

    def put_item(self, Item):  # noqa: N803
        self.items[Item["pk"]] = Item


class FakeGeocoder:
    def __init__(self, origin, places):
        self.origin = origin
        self.places = places

    def by_zip(self, zip_code):
        return self.origin

    def golf_course_places(self, origin, radius_miles):
        return self.places

    def golf_courses_near(self, origin, radius_miles):
        return [(p.name, p.coords) for p in self.places]


QUINCY = Coordinates(lat=42.2461701, lon=-71.0057746, display_name="02169, Quincy")
NEAR = Coordinates(lat=42.26, lon=-71.02, display_name="Near CC, Quincy, MA")

from app.geo import CoursePlace  # noqa: E402


def _place(name="Near CC", website=""):
    return CoursePlace(
        name=name, coords=NEAR, website=website, display_name=f"{name}, Quincy, MA"
    )


def _stores(places):
    geocoder = FakeGeocoder(QUINCY, places)
    table = FakeTable()
    from app.geo import GeocodeStore

    geo_table = FakeTable()
    geo_table.items["zip#02169"] = {"lat": "42.2", "lon": "-71.0"}
    geocode_store = GeocodeStore("g", table=geo_table)
    return geocoder, CourseStore("c", table=table), geocode_store


# --- discovery ---------------------------------------------------------------


def test_unknown_course_gets_a_row_with_coordinates(monkeypatch):
    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: None)
    geocoder, store, geocode_store = _stores([_place()])
    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
    )
    assert result.added == 1
    assert result.error == ""
    row = store.get("Near CC")
    assert row["lat"] == str(NEAR.lat)
    assert row["provider"] == "unknown"


def test_out_of_radius_courses_leave_no_row():
    from app.geo import CoursePlace

    far = CoursePlace(
        name="Far CC",
        coords=Coordinates(lat=42.0, lon=-72.5, display_name="Far"),
        website="",
    )
    geocoder, store, geocode_store = _stores([_place(), far])
    result = discover(
        "02169",
        5.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
    )
    assert result.skipped_outside_radius == 1
    assert result.added == 1
    assert store.get("Far CC") is None


def test_confirmed_teeitup_pairing_is_recorded(monkeypatch):
    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: "9999")
    monkeypatch.setattr(
        "app.discovery.confirm_course", lambda alias, fid, date: "course-id-1"
    )
    geocoder, store, geocode_store = _stores([_place()])
    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
    )
    assert result.tee_it_up == 1
    row = store.get("Near CC")
    assert row["provider"] == "teeitup-confirmed"
    assert row["facility_id"] == "9999"
    assert result.details == ["Near CC -> near-cc (facility 9999)"]


def test_already_configured_facility_is_not_recounted(monkeypatch):
    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: "5720")
    geocoder, store, geocode_store = _stores([_place()])
    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids={"5720"},
    )
    assert result.already_configured == 1
    assert result.tee_it_up == 0
    assert store.get("Near CC")["provider"] == "teeitup"


def test_known_courses_are_skipped_not_rewritten(monkeypatch):
    monkeypatch.setattr(
        "app.discovery.facility_id_for",
        lambda alias: (_ for _ in ()).throw(AssertionError("no probe")),
    )
    geocoder, store, geocode_store = _stores([_place()])
    store.put({"pk": "course#near-cc", "name": "Near CC", "provider": "teeitup"})
    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
    )
    assert result.known == 1
    assert result.added == 0


def test_ungeocodable_zip_is_a_run_error():
    class NoZip(FakeGeocoder):
        def by_zip(self, zip_code):
            return None

    from app.geo import GeocodeStore

    store = CourseStore("c", table=FakeTable())
    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=GeocodeStore(None),
        geocoder=NoZip(None, []),
        configured_facility_ids=set(),
    )
    assert isinstance(result, DiscoveryResult)
    assert "02169" in result.error
    assert result.added == 0


# --- fingerprint -------------------------------------------------------------


def _course(**overrides):
    row = {
        "name": "Near CC",
        "website": "",
        "display_name": "Near CC, Quincy, MA",
        "provider": "unknown",
        "booking_domain": "",
        "facility_id": "",
    }
    row.update(overrides)
    return row


def test_teeitup_redirect_is_detected(monkeypatch):
    monkeypatch.setattr(
        fp,
        "_fetch",
        lambda url: (
            "https://near-cc.book.teeitup.golf/",
            '<html data-gnFacilityIds="[9999]"></html>',
        ),
    )
    monkeypatch.setattr(fp, "_teeitup_facility_id", lambda body: "9999")
    verdict = fp.fingerprint(_course(website="https://nearcc.com"))
    assert verdict.provider == "teeitup"
    assert verdict.booking_domain == "near-cc.book.teeitup.golf"
    assert verdict.facility_id == "9999"


def test_embedded_widget_counts_as_teeitup(monkeypatch):
    monkeypatch.setattr(fp, "_fetch", lambda url: (url, "book now via TeeItUp widget"))
    monkeypatch.setattr(fp, "_teeitup_facility_id", lambda body: None)
    verdict = fp.fingerprint(_course(website="https://nearcc.com"))
    assert verdict.provider == "teeitup"
    assert "widget" in verdict.evidence


def test_golfnow_booking_link_is_named(monkeypatch):
    site = '<a href="https://www.golfnow.com/tee-times/123">Book tee times</a>'

    def fetch(url):
        return (url, site) if "nearcc" in url else (url, "golfnow booking engine")

    monkeypatch.setattr(fp, "_fetch", fetch)
    verdict = fp.fingerprint(_course(website="https://nearcc.com"))
    assert verdict.provider == "golfnow"
    assert "golfnow" in verdict.evidence


def test_dead_site_stays_unknown(monkeypatch):
    monkeypatch.setattr(fp, "_fetch", lambda url: (url, ""))
    verdict = fp.fingerprint(_course(website="https://nearcc.com"))
    assert verdict.provider == "unknown"
    assert verdict.checked_urls, "attempts should be recorded for the next run"


def test_known_teeitup_engine_is_rechecked_not_trusted(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return ("https://near-cc.book.teeitup.golf/", "engine page")

    monkeypatch.setattr(fp, "_fetch", fetch)
    monkeypatch.setattr(fp, "_teeitup_facility_id", lambda body: "9999")
    verdict = fp.fingerprint(
        _course(
            provider="teeitup-confirmed",
            booking_domain="near-cc.book.teeitup.golf",
            facility_id="9999",
        )
    )
    assert verdict.provider == "teeitup"
    assert verdict.facility_id == "9999"
    assert calls == ["https://near-cc.book.teeitup.golf/"]


def test_guesses_prefer_the_osm_website():
    guesses = fp.website_guesses(
        _course(website="https://real-site.com", display_name="Near CC, Quincy, MA")
    )
    assert guesses[0] == "https://real-site.com"
