from datetime import date, time, datetime
from functools import lru_cache
import time as _time
import logging
import certifi
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import ssl
import json

from .geo import GeocodeStore, Geocoder, distance_miles as miles_between
from .models import SearchRequest, TeeTimeOption


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


class TeeItUpProvider(SearchProvider):
    """Base provider for courses using TeeItUp for booking.

    Courses using TeeItUp booking system should subclass this and set the
    class attributes ``api_url``, ``facility_id``, and ``x_be_alias`` to match
    their specific configuration. The ``base_url`` is computed automatically
    from ``x_be_alias`` as ``https://{x_be_alias}.book.teeitup.golf``.

    The ``fetch_availability`` method handles the TeeItUp API pattern:
    - Constructs the API URL with facility ID and current date
    - Sends a JSON request with the ``x-be-alias`` header
    - Parses tee-time slots from the response
    """

    #: API endpoint for tee times
    api_url: str = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"

    #: Facility ID for the course, e.g. "5720"
    facility_id: str = ""

    #: x-be-alias header value, e.g. "norwood-country-club"
    x_be_alias: str = ""

    min_interval_seconds: float = 2.0

    _last_request_time: float = 0.0

    def __init__(self, **kwargs):
        """Initialize TeeItUpProvider and compute base_url."""
        super().__init__(**kwargs)
        # Compute base_url from x_be_alias
        if self.x_be_alias:
            self.base_url = f"https://{self.x_be_alias}.book.teeitup.golf"
        else:
            self.base_url = ""
        # Subclasses declare their alias as a class attribute; expose it as the
        # provider name so results are attributable to a course.
        self.name = self.x_be_alias or type(self).__name__

    def _fetch_json(self, url: str) -> object:
        """Fetch JSON from a URL with TeeItUp headers.

        TLS certificates are always verified, using certifi's CA bundle rather
        than whatever the interpreter happens to ship: a Lambda runtime and a
        local Python build can otherwise disagree about trusted roots.
        """

        now = _time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval_seconds:
            _time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_time = _time.time()

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "x-be-alias": self.x_be_alias,
                "User-Agent": "tee-time-finder/0.1",
            },
        )
        try:
            with urlopen(request, timeout=15, context=_tls_context()) as response:
                return json.loads(response.read())
        except Exception as e:
            logging.getLogger(__name__).error(
                f"TeeItUpProvider [{self.x_be_alias}] _fetch_json failed: {e}"
            )
            return None

    def _log(self, message: str):
        """Log a message at debug level."""
        _logging = logging.getLogger(__name__)
        _logging.debug(f"TeeItUpProvider [{self.x_be_alias}]: {message}")

    def _log_result(self, method: str, success: bool, count: int):
        """Log fetch/search results."""
        _logging = logging.getLogger(__name__)
        status = "success" if success else "no results"
        _logging.info(
            f"TeeItUpProvider [{self.x_be_alias}] {method}: {count} results ({status})"  # noqa: E501
        )

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        """Fetch availability from the TeeItUp API for this course.

        Queries the requested date (in course-local time), then returns the
        slots that fall on that date, inside the requested time window, and
        that can seat ``request.players`` players.
        """

        tz = ZoneInfo(COURSE_TIMEZONE)
        query = urlencode(
            {
                "date": request.date.isoformat(),
                "facilityIds": self.facility_id,
                "returnPromotedRates": "true",
            }
        )
        url = f"{self.api_url}?{query}"

        self._log(f"fetch_availability called with url: {url}")
        payload = self._fetch_json(url)
        if not payload:
            # Request failed (logged in _fetch_json) or the API returned nothing.
            return []

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

        self._log_result("fetch_availability", bool(results), len(results))
        return results

    def search(
        self, request: SearchRequest, distance_miles: float = 0.0
    ) -> list[TeeTimeOption]:
        """Search for tee-time options.

        ``distance_miles`` is supplied by :func:`search_all`, which knows where
        the caller's ZIP is relative to this course.
        """

        availability = self.fetch_availability(request)
        return [
            TeeTimeOption(
                provider=self.name,
                course_name=item["course_name"],
                date=item["date"],
                start_time=item["start_time"],
                players=item["players"],
                distance_miles=round(distance_miles, 1),
            )
            for item in availability
        ]


class NorwoodCCProvider(TeeItUpProvider):
    """Provider for Norwood Country Club (Norwood, MA)."""

    facility_id = "5720"
    display_name = "Norwood Country Club"
    x_be_alias = "norwood-country-club"


class SquirrelRunProvider(TeeItUpProvider):
    """Provider for Squirrel Run Golf Course."""

    facility_id = "4328"
    display_name = "Squirrel Run Golf Course"
    x_be_alias = "squirrel-run-golf-club"


class VillageLinksProvider(TeeItUpProvider):
    """Provider for Village Links"""

    facility_id = "4329"
    display_name = "Village Links Golf Club"
    x_be_alias = "village-links-golf-club"


class PonkapoagCCProvider(TeeItUpProvider):
    """Provider for Ponkapoag Country Club."""

    facility_id = "17927"
    display_name = "Ponkapoag Golf Course"
    x_be_alias = "ponkapoag-golf-course"


class PonkapoagCC2Provider(TeeItUpProvider):
    """Provider for Ponkapoag Country Club."""

    facility_id = "17927"
    display_name = "Ponkapoag Golf Course"
    x_be_alias = "ponkapoag-golf-course-2"


class BraintreeCountryClubProvider(TeeItUpProvider):
    """Provider for Braintree Country Club."""

    facility_id = "16026"
    display_name = "Braintree Municipal Golf Course"
    x_be_alias = "braintree-municipal-golf-course"


class PresidentsGCProvider(TeeItUpProvider):
    """Provider for Presidents Golf Course (Quincy, MA)."""

    facility_id = "17943"
    display_name = "Presidents Golf Course"
    x_be_alias = "presidents-golf-course"


def get_search_providers() -> list[SearchProvider]:
    providers: list[SearchProvider] = [
        NorwoodCCProvider(),
        SquirrelRunProvider(),
        VillageLinksProvider(),
        PonkapoagCCProvider(),
        PonkapoagCC2Provider(),
        BraintreeCountryClubProvider(),
        PresidentsGCProvider(),
    ]
    return providers


def search_all(
    request: SearchRequest,
    store: GeocodeStore | None = None,
    geocoder: Geocoder | None = None,
) -> list[TeeTimeOption]:
    """Search every configured course, filtering by the requested radius.

    The caller's ZIP is resolved through ``store`` (a cached lookup, geocoding
    on a miss). Courses without known coordinates are still searched, so a
    missing seed row hides the distance rather than the course.
    """
    store = store or GeocodeStore(None)
    origin = store.zip(request.zip_code, geocoder)

    results: list[TeeTimeOption] = []
    seen: set[tuple[str, date, time]] = set()
    for provider in get_search_providers():
        distance = 0.0
        if origin is not None:
            course = store.course(provider.facility_id)
            if course is not None:
                distance = miles_between(origin, course)
                if distance > request.radius_miles:
                    continue
        for option in provider.search(request, distance_miles=distance):
            # Two aliases can front the same facility (Ponkapoag), which would
            # otherwise list every tee time twice.
            key = (option.course_name, option.date, option.start_time)
            if key in seen:
                continue
            seen.add(key)
            results.append(option)

    return sorted(results, key=lambda result: result.start_time)
