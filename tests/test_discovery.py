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

    def scan(self, **kwargs):
        items = list(self.items.values())
        start = kwargs.get("ExclusiveStartKey") or {}
        if start:
            keys = list(self.items)
            offset = keys.index(start["pk"]) + 1
            items = items[offset:]
        return {"Items": items}


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
    assert result.details == ["Near CC -> near-cc.book.teeitup.golf (facility 9999)"]


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


# --- the nightly run must not re-sweep -------------------------------------


def test_sweep_only_runs_on_the_first_run_of_a_month():
    from app.discovery import due_for_sweep
    import datetime

    utc = datetime.timezone.utc
    assert due_for_sweep(None, datetime.datetime(2026, 5, 14, tzinfo=utc))
    assert due_for_sweep(
        "2026-04-01T10:00:00+00:00", datetime.datetime(2026, 5, 14, tzinfo=utc)
    )
    # Same month: already swept, so no Nominatim request.
    assert not due_for_sweep(
        "2026-05-02T10:00:00+00:00", datetime.datetime(2026, 5, 14, tzinfo=utc)
    )
    # A corrupt timestamp should not silently disable discovery forever.
    assert due_for_sweep("not-a-date", datetime.datetime(2026, 5, 14, tzinfo=utc))


def test_second_run_in_a_month_makes_no_nominatim_request(monkeypatch):
    """The whole point: a nightly run must not re-ask for the same list."""
    import datetime

    calls = []

    class Counting(FakeGeocoder):
        def golf_course_places(self, origin, radius_miles):
            calls.append(radius_miles)
            return super().golf_course_places(origin, radius_miles)

    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: None)
    geocoder = Counting(QUINCY, [_place()])
    table = FakeTable()
    from app.geo import GeocodeStore

    geo_table = FakeTable()
    geo_table.items["zip#02169"] = {"lat": "42.2", "lon": "-71.0"}
    store = CourseStore("c", table=table)
    geocode_store = GeocodeStore("g", table=geo_table)
    utc = datetime.timezone.utc

    first = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 14, 10, tzinfo=utc),
    )
    second = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=geocode_store,
        geocoder=geocoder,
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 15, 10, tzinfo=utc),
    )

    assert first.swept and first.added == 1
    assert not second.swept and second.added == 0
    assert len(calls) == 1, "the second run should not have asked Nominatim"


def test_unclassified_course_is_probed_again_next_run(monkeypatch):
    """A row that says 'unknown' is a backlog item, not a known course."""
    import datetime

    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: None)
    table = FakeTable()
    table.items["course#old-cc"] = {
        "pk": "course#old-cc",
        "name": "Old CC",
        "provider": "unknown",
        "discovery_attempts": 1,
    }
    store = CourseStore("c", table=table)
    from app.geo import GeocodeStore

    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=GeocodeStore("g", table=FakeTable()),
        geocoder=FakeGeocoder(QUINCY, []),
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 15, 10, tzinfo=datetime.timezone.utc),
    )
    assert result.probed == 1
    assert table.items["course#old-cc"]["discovery_attempts"] == 2


def test_classified_course_is_left_alone(monkeypatch):
    import datetime

    monkeypatch.setattr(
        "app.discovery.facility_id_for",
        lambda alias: (_ for _ in ()).throw(AssertionError("should not probe")),
    )
    table = FakeTable()
    table.items["course#done-cc"] = {
        "pk": "course#done-cc",
        "name": "Done CC",
        "provider": "golfnow",
    }
    store = CourseStore("c", table=table)
    from app.geo import GeocodeStore

    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=GeocodeStore("g", table=FakeTable()),
        geocoder=FakeGeocoder(QUINCY, []),
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 15, 10, tzinfo=datetime.timezone.utc),
    )
    assert result.probed == 0


def test_attempts_are_capped(monkeypatch):
    import datetime

    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: None)
    table = FakeTable()
    table.items["course#hopeless"] = {
        "pk": "course#hopeless",
        "name": "Hopeless CC",
        "provider": "unknown",
        "discovery_attempts": 5,
    }
    store = CourseStore("c", table=table)
    from app.geo import GeocodeStore

    result = discover(
        "02169",
        25.0,
        store=store,
        geocode_store=GeocodeStore("g", table=FakeTable()),
        geocoder=FakeGeocoder(QUINCY, []),
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 15, 10, tzinfo=datetime.timezone.utc),
    )
    assert result.probed == 0
    assert table.items["course#hopeless"]["discovery_attempts"] == 5


def test_backlog_order_reaches_past_the_nearest_ten(monkeypatch):
    """The budget walks the whole set instead of grinding on the same ten."""
    import datetime

    monkeypatch.setattr("app.discovery.facility_id_for", lambda alias: None)
    table = FakeTable()
    for index in range(25):
        table.items[f"course#c{index:02d}"] = {
            "pk": f"course#c{index:02d}",
            "name": f"C{index:02d}",
            "provider": "unknown",
            "discovery_attempts": 0,
        }
    # The first ten are already on their second try.
    for index in range(10):
        table.items[f"course#c{index:02d}"]["discovery_attempts"] = 2

    store = CourseStore("c", table=table)
    from app.geo import GeocodeStore

    discover(
        "02169",
        25.0,
        store=store,
        geocode_store=GeocodeStore("g", table=FakeTable()),
        geocoder=FakeGeocoder(QUINCY, []),
        configured_facility_ids=set(),
        now=datetime.datetime(2026, 5, 15, 10, tzinfo=datetime.timezone.utc),
    )
    # Least-tried first: the untouched ten, not the ones already at two.
    assert all(
        table.items[f"course#c{i:02d}"]["discovery_attempts"] == 1
        for i in range(10, 20)
    )
