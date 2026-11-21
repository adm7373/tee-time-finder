from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, time, datetime
from functools import lru_cache
import time as _time
import logging
import certifi
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
import ssl
import json

from .geo import Coordinates, GeocodeStore, Geocoder, distance_miles as miles_between
from .models import SearchRequest, TeeTimeOption

if TYPE_CHECKING:
    from .discovery import CourseStore


logger = logging.getLogger(__name__)


class SearchProvider:
    """Provider boundary used by real course integrations later.

    Each provider is isolated from the UI and from other providers. A provider
    that is not configured (e.g. missing API key) should report itself as not
    configured so it can be skipped without failing the whole search.
    """

    #: Stable provider identifier, surfaced on every ``TeeTimeOption``.
    name = "base"

    @classmethod
    def is_configured(cls) -> bool:
        """Return True if this provider has everything it needs to run."""
        return True

    #: Human-readable course name; falls back to ``name`` when unset.
    display_name = ""

    def __init__(
        self,
        name: str | None = None,
        display_name: str | None = None,
        coordinates: Coordinates | None = None,
        **kwargs,
    ):
        if name is not None:
            self.name = name
        if display_name is not None:
            self.display_name = display_name
        self.coordinates = coordinates

    def search(
        self, request: SearchRequest, distance_miles: float = 0.0
    ) -> list[TeeTimeOption]:
        raise NotImplementedError


#: All configured courses are in New England; the TeeItUp API returns UTC
#: instants, which must be converted before comparing against a local window.
COURSE_TIMEZONE = "America/New_York"


@lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    """A verifying TLS context backed by certifi's CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())


def _row_coordinates(row: dict) -> Coordinates | None:
    """Coordinates discovery saved on a row, or None when it did not."""
    if not (row.get("lat") and row.get("lon")):
        return None
    try:
        return Coordinates(
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            display_name=row.get("display_name", ""),
            source="dynamodb",
        )
    except (TypeError, ValueError):
        return None


class BookingEngineProvider(SearchProvider):
    """Base for a booking engine that many courses share.

    An engine supplies only what differs: ``from_row`` (how a course row becomes
    an instance), ``availability_url`` (the request for one date) and
    ``parse_slots`` (how the response becomes slot dicts). Pacing, TLS, headers
    and the mapping to ``TeeTimeOption`` live here, so adding an engine is one
    small class plus an entry in :data:`ENGINE_PROVIDERS`, and adding a course
    is a row in the courses table -- never another subclass.

    ``label`` is the value discovery writes to a row's ``provider`` field.
    """

    #: Row label this engine answers to, e.g. ``"teeitup"``.
    label: str = ""

    #: Booking API endpoint. Engines may build it per course instead.
    api_url: str = ""

    #: Minimum seconds between requests from one instance.
    min_interval_seconds: float = 2.0

    _last_request_time: float = 0.0

    @classmethod
    def from_row(cls, row: dict) -> "BookingEngineProvider | None":
        """Build an instance from a course row, or None when the row lacks ids."""
        raise NotImplementedError

    def request_headers(self) -> dict[str, str]:
        """Headers each availability request carries."""
        return {"Accept": "application/json", "User-Agent": "tee-time-finder/0.1"}

    def availability_url(self, request: SearchRequest) -> str:
        raise NotImplementedError

    def parse_slots(self, payload: object, request: SearchRequest) -> list[dict]:
        """Slots as ``{date, start_time, course_name, players}`` dicts."""
        raise NotImplementedError

    def booking_url(self) -> str | None:
        """Where a person would book, or None when the engine has no page."""
        return None

    def course_key(self) -> str:
        """Key that collapses two rows fronting the same course."""
        return getattr(self, "facility_id", "") or self.name

    def _fetch_json(self, url: str) -> object:
        """Fetch JSON from a URL with the engine's headers.

        TLS certificates are always verified, using certifi's CA bundle rather
        than whatever the interpreter happens to ship: a Lambda runtime and a
        local Python build can otherwise disagree about trusted roots.
        """

        now = _time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval_seconds:
            _time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_time = _time.time()

        request = Request(url, headers=self.request_headers())
        try:
            with urlopen(request, timeout=15, context=_tls_context()) as response:
                return json.loads(response.read())
        except Exception as error:
            logger.error(
                "%s [%s] _fetch_json failed: %s", type(self).__name__, self.name, error
            )
            return None

    def _log(self, message: str) -> None:
        logger.debug("%s [%s]: %s", type(self).__name__, self.name, message)

    def _log_result(self, method: str, success: bool, count: int) -> None:
        logger.info(
            "%s [%s] %s: %d results (%s)",
            type(self).__name__,
            self.name,
            method,
            count,
            "success" if success else "no results",
        )

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        """Ask the engine for one date and keep the slots that can be booked."""
        url = self.availability_url(request)
        self._log(f"fetch_availability {url}")
        payload = self._fetch_json(url)
        if not payload:
            # The request failed (logged in _fetch_json) or returned nothing.
            return []
        slots = self.parse_slots(payload, request)
        self._log_result("fetch_availability", bool(slots), len(slots))
        return slots

    def search(
        self, request: SearchRequest, distance_miles: float = 0.0
    ) -> list[TeeTimeOption]:
        """Search for tee-time options.

        ``distance_miles`` is supplied by :func:`search_all`, which knows where
        the caller's ZIP is relative to this course.
        """
        availability = self.fetch_availability(request)
        booking_url = self.booking_url()
        return [
            TeeTimeOption(
                provider=self.name,
                course_name=item["course_name"],
                date=item["date"],
                start_time=item["start_time"],
                players=item["players"],
                distance_miles=round(distance_miles, 1),
                booking_url=booking_url,
            )
            for item in availability
        ]


class TeeItUpProvider(BookingEngineProvider):
    """TeeItUp for booking: a course is a facility id plus a site alias.

    Courses are rows: discovery records the facility id and alias on the course
    row and :meth:`from_row` builds the provider from it.
    """

    label = "teeitup"

    #: API endpoint for tee times
    api_url: str = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"

    #: Facility ID for the course, e.g. "5720"
    facility_id: str = ""

    #: x-be-alias header value, e.g. "norwood-country-club"
    x_be_alias: str = ""

    def __init__(
        self,
        facility_id: str | None = None,
        x_be_alias: str | None = None,
        display_name: str | None = None,
        coordinates: Coordinates | None = None,
        **kwargs,
    ):
        """Initialize TeeItUpProvider and compute base_url."""
        if facility_id is not None:
            self.facility_id = facility_id
        if x_be_alias is not None:
            self.x_be_alias = x_be_alias
        super().__init__(
            name=self.x_be_alias,
            display_name=display_name if display_name is not None else self.display_name,
            coordinates=coordinates,
            **kwargs,
        )
        # Compute base_url from x_be_alias
        if self.x_be_alias:
            self.base_url = f"https://{self.x_be_alias}.book.teeitup.golf"
        else:
            self.base_url = ""
        # Subclasses declare their alias as a class attribute; expose it as the
        # provider name so results are attributable to a course.
        self.name = self.x_be_alias or type(self).__name__

    @classmethod
    def from_row(cls, row: dict) -> "TeeItUpProvider | None":
        """Build a provider from a row discovery or the fingerprint wrote.

        The API needs a facility id and an alias. Confirmed rows carry both; a
        row that only recorded the booking domain still yields the alias.
        """
        facility_id = row.get("facility_id")
        alias = row.get("x_be_alias") or ""
        if not alias and ".book.teeitup.golf" in (row.get("booking_domain") or ""):
            alias = row["booking_domain"].split(".book.teeitup.golf")[0]
        if not facility_id or not alias:
            return None

        coordinates = _row_coordinates(row)

        return cls(
            facility_id=str(facility_id),
            x_be_alias=alias,
            display_name=row.get("display_name") or row.get("name") or alias,
            coordinates=coordinates,
        )

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "x-be-alias": self.x_be_alias,
            "User-Agent": "tee-time-finder/0.1",
        }

    def availability_url(self, request: SearchRequest) -> str:
        query = urlencode(
            {
                "date": request.date.isoformat(),
                "facilityIds": self.facility_id,
                "returnPromotedRates": "true",
            }
        )
        return f"{self.api_url}?{query}"

    def booking_url(self) -> str | None:
        return f"{self.base_url}/" if getattr(self, "base_url", None) else None

    def parse_slots(self, payload: object, request: SearchRequest) -> list[dict]:
        """Slots on the requested local date, in the window, for the party size."""
        tz = ZoneInfo(COURSE_TIMEZONE)
        earliest = request.earliest_time or time.min
        latest = request.latest_time or time.max
        # The window is expressed in local wall-clock time; compare against the
        # slot's local wall-clock time, not the UTC instant the API returns.
        window_start = datetime.combine(request.date, earliest, tzinfo=tz)
        window_end = datetime.combine(request.date, latest, tzinfo=tz)

        seen_times: set[time] = set()
        results: list[dict] = []

        for day in payload:
            for tee in day.get("teetimes") or []:
                try:
                    start = datetime.fromisoformat(
                        tee["teetime"].replace("Z", "+00:00")
                    ).astimezone(tz)
                except (KeyError, TypeError, ValueError):
                    self._log(f"skipping unparseable teetime: {tee.get('teetime')!r}")
                    continue

                # Guard against the API returning slots from an adjacent day.
                if start.date() != request.date:
                    continue
                if not (window_start <= start <= window_end):
                    continue
                if start.time() in seen_times:
                    continue

                rates = tee.get("rates") or []
                if not any(
                    request.players in (rate.get("allowedPlayers") or [])
                    for rate in rates
                ):
                    continue

                seen_times.add(start.time())
                results.append(
                    {
                        "date": request.date,
                        "start_time": start.time(),
                        "course_name": self.display_name or self.x_be_alias,
                        "players": request.players,
                    }
                )

        return results


class ForeUpProvider(BookingEngineProvider):
    """foreup for booking: a course is a course id plus a schedule id.

    Both ids sit in the booking link the fingerprint worker records
    (``/index.php/booking/<course_id>/<schedule_id>``), so :meth:`from_row`
    reads them off the row and never touches the network.

    ``parse_slots`` is not written yet: the availability payload has not been
    seen for a course with live inventory, so this class stays out of
    :data:`ENGINE_PROVIDERS` until it can be. ``docs/provider-adapters.md``
    records what is known about the endpoint.
    """

    label = "foreup"

    #: API endpoint behind the booking page
    api_url: str = "https://foreupsoftware.com/index.php/api/booking/times"

    #: Course and schedule id from the course's booking link
    course_id: str = ""
    schedule_id: str = ""

    def __init__(
        self,
        course_id: str | None = None,
        schedule_id: str | None = None,
        display_name: str | None = None,
        coordinates: Coordinates | None = None,
        **kwargs,
    ):
        """Initialize ForeUpProvider from the two ids its API needs."""
        if course_id is not None:
            self.course_id = course_id
        if schedule_id is not None:
            self.schedule_id = schedule_id
        super().__init__(
            name=f"foreup-{self.course_id}" if self.course_id else self.label,
            display_name=display_name or "",
            coordinates=coordinates,
            **kwargs,
        )

    @classmethod
    def from_row(cls, row: dict) -> "ForeUpProvider | None":
        """Build a provider from the ids the fingerprint stored on the row."""
        course_id = str(row.get("foreup_course_id") or "")
        schedule_id = str(row.get("foreup_schedule_id") or "")
        if not (course_id and schedule_id):
            return None
        return cls(
            course_id=course_id,
            schedule_id=schedule_id,
            display_name=row.get("display_name") or row.get("name") or "foreup",
            coordinates=_row_coordinates(row),
        )

    def course_key(self) -> str:
        """The pair of ids identifies the course; the label is shared."""
        return f"{self.course_id}/{self.schedule_id}"

    def booking_url(self) -> str | None:
        if not (self.course_id and self.schedule_id):
            return None
        return (
            "https://foreupsoftware.com/index.php/booking/"
            f"{self.course_id}/{self.schedule_id}"
        )

    def availability_url(self, request: SearchRequest) -> str:
        query = urlencode(
            {
                "time": "all",
                "date": request.date.strftime("%m-%d-%Y"),
                "holes": "all",
                "players": request.players,
                "schedule_id": self.schedule_id,
                "booking_class": "default",
            }
        )
        return f"{self.api_url}?{query}"

    def parse_slots(self, payload: object, request: SearchRequest) -> list[dict]:
        raise NotImplementedError(
            "foreup availability has not been seen for a course with live "
            "inventory; see docs/provider-adapters.md"
        )


#: Row label -> engine adapter. Discovery and the fingerprint worker write these
#: labels on course rows; a row whose label is not here is a course the app
#: knows about but cannot search yet.
ENGINE_PROVIDERS: dict[str, type[BookingEngineProvider]] = {
    "teeitup": TeeItUpProvider,
    "teeitup-confirmed": TeeItUpProvider,
}


def provider_coverage(rows: list[dict]) -> dict[str, int]:
    """Course rows whose label has no engine adapter, counted by label.

    This is the number that says whether writing another adapter would unlock
    anything, so it is shared by the catalog load and
    ``scripts/unimplemented_providers.py``.
    """
    missing: dict[str, int] = {}
    for row in rows:
        if not str(row.get("pk", "")).startswith("course#"):
            continue
        label = row.get("provider") or "<missing>"
        if label in ENGINE_PROVIDERS:
            continue
        missing[label] = missing.get(label, 0) + 1
    return missing


def get_providers_from_store(store: "CourseStore") -> list[SearchProvider]:
    """Build one provider per classified course row.

    The row's label picks the engine, so a course discovered last night is
    searchable without a code change; the engine's ``from_row`` supplies the
    ids it needs. Rows an engine cannot build from, and labels with no engine,
    are counted and logged rather than dropped in silence.
    """
    rows = [row for row in store.scan() if str(row.get("pk", "")).startswith("course#")]
    providers: list[SearchProvider] = []
    seen: set[str] = set()
    for row in rows:
        label = row.get("provider") or ""
        engine = ENGINE_PROVIDERS.get(label)
        if engine is None:
            continue
        provider = engine.from_row(row)
        if provider is None:
            logger.info(
                "catalog: %s is labelled %s but the row is missing ids %s needs",
                row.get("name", "?"),
                label,
                engine.label,
            )
            continue
        key = provider.course_key()
        if key in seen:
            continue
        seen.add(key)
        providers.append(provider)

    missing = provider_coverage(rows)
    if missing:
        logger.info(
            "catalog: %d courses have no provider implementation: %s",
            sum(missing.values()),
            missing,
        )
    return providers


def get_search_providers(
    course_store: "CourseStore | None" = None,
) -> list[SearchProvider]:
    """Search catalog built from the courses table.

    Every course, canonical ones included, is a row: there is no built-in list
    to fall back on, so no store (or an empty one) means nothing to search. A
    hidden fallback would keep answering with courses nothing maintains.
    """
    if course_store is None:
        return []
    return get_providers_from_store(course_store)


def search_all(
    request: SearchRequest,
    store: GeocodeStore | None = None,
    geocoder: Geocoder | None = None,
    course_store: "CourseStore | None" = None,
) -> list[TeeTimeOption]:
    """Search every configured course, filtering by the requested radius.

    The caller's ZIP is resolved through ``store`` (a cached lookup, geocoding
    on a miss). Courses without known coordinates are still searched, so a
    row without coordinates hides the distance rather than the course.
    """
    store = store or GeocodeStore(None)
    origin = store.zip(request.zip_code, geocoder)

    results: list[TeeTimeOption] = []
    seen: set[tuple[str, date, time]] = set()
    # No fallback here. A catalog that cannot be built -- including an adapter
    # raising on a row -- is a bug to see and fix; swallowing it would answer
    # from an empty catalog and hide every discovered course with no signal.
    candidate_providers = get_search_providers(course_store)

    eligible_providers: list[tuple[SearchProvider, float]] = []
    for provider in candidate_providers:
        distance = 0.0
        if origin is not None:
            course = getattr(provider, "coordinates", None) or store.course(
                provider.facility_id
            )
            if course is not None:
                distance = miles_between(origin, course)
                if distance > request.radius_miles:
                    continue
        eligible_providers.append((provider, distance))

    if not eligible_providers:
        return []

    def _fetch(p: SearchProvider, dist: float) -> list[TeeTimeOption]:
        try:
            return p.search(request, distance_miles=dist)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Search failed for provider %s: %s", getattr(p, "name", p), error
            )
            return []

    results: list[TeeTimeOption] = []
    seen: set[tuple[str, date, time]] = set()

    if len(eligible_providers) == 1:
        provider, distance = eligible_providers[0]
        for option in _fetch(provider, distance):
            key = (option.course_name, option.date, option.start_time)
            if key not in seen:
                seen.add(key)
                results.append(option)
    else:
        max_workers = min(len(eligible_providers), 10)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_provider = {
                executor.submit(_fetch, p, d): p
                for p, d in eligible_providers
            }
            for future in as_completed(future_to_provider):
                for option in future.result():
                    key = (option.course_name, option.date, option.start_time)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(option)

    return sorted(results, key=lambda result: result.start_time)
