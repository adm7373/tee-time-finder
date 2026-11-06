from datetime import datetime, time, timezone
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import SearchRequest, TeeTimeOption


class SearchProvider:
    """Provider boundary used by real course integrations later.

    Each provider is isolated from the UI and from other providers. A provider
    that is not configured (e.g. missing API key) should report itself as not
    configured so it can be skipped without failing the whole search.
    """

    @classmethod
    def is_configured(cls) -> bool:
        """Return True if this provider has everything it needs to run."""
        return True

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        raise NotImplementedError


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

    #: API endpoint for tee times, e.g. "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
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
        # Compute base_url from x_be_alias: "https://{x_be_alias}.book.teeitup.golf"
        if self.x_be_alias:
            self.base_url = f"https://{self.x_be_alias}.book.teeitup.golf"
        else:
            self.base_url = ""

    def _fetch(self, url: str) -> str | None:
        """Fetch a URL and return its text content, or None on failure."""
        import time as _time

        now = _time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval_seconds:
            _time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_time = _time.time()

        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "tee-time-finder/0.1 (personal tool; contact via repo)",
                },
            )
            with urlopen(req, timeout=15) as response:
                return response.read().decode("utf-8")
        except Exception:
            return None

    def _fetch_json(self, url: str) -> object:
        """Fetch JSON from a URL with TeeItUp headers.

        Includes the ``x-be-alias`` header and a standard User-Agent.
        Handles SSL certificate issues gracefully for local development.
        """
        import ssl
        import json
        import time as _time

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
            ctx = ssl.create_default_context()
            with urlopen(request, timeout=15, context=ctx) as response:
                return json.loads(response.read())
        except Exception as e:
            logging.getLogger(__name__).error(
                f"TeeItUpProvider [{self.x_be_alias}] _fetch_json failed: {e}"
            )
            # Fallback for local development (Python 3.15 certificate issues)
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urlopen(request, timeout=15, context=ctx) as response:
                    return json.loads(response.read())
            except Exception:
                return None

    def _log(self, message: str):
        """Log a message at debug level."""
        _logging = logging.getLogger(__name__)
        _logging.debug(f"TeeItUpProvider [{self.x_be_alias}]: {message}")

    def _log_result(self, method: str, success: bool, count: int):
        """Log fetch/search results."""
        _logging = logging.getLogger(__name__)
        status = "success" if success else "no results"
        _logging.info(f"TeeItUpProvider [{self.x_be_alias}] {method}: {count} results ({status})")

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        """Fetch availability from the TeeItUp API for this course.

        Constructs the API request with the facility ID and current date,
        then returns parsed tee-time slots.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        current = datetime.now(tz).date()
        query = urlencode(
            {
                "date": current.isoformat(),
                "facilityIds": self.facility_id,
                "returnPromotedRates": "true",
            }
        )
        url = f"{self.api_url}?{query}"

        self._log(f"fetch_availability called with url: {url}")
        payload = self._fetch_json(url)
        if payload is None:
            # API request failed (exception was logged in _fetch_json)
            return []
        elif len(payload) == 0:
            # API returned zero results
            return []
        else:
            # API returned data with tee-times
            pass  # continue processing below

        seen_times: set[time] = set()
        results: list[dict] = []

        for day in payload:
            tees = day.get("teetimes", [])
            total = day.get("totalAvailableTeetimes", 0)
            if total == 0:
                continue

            for tee in tees:
                try:
                    start = (
                        datetime.fromisoformat(tee["teetime"].replace("Z", "+00:00"))
                        .astimezone(tz)
                        .time()
                    )

                    earliest = request.earliest_time or time.min
                    latest = request.latest_time or time.max
                    if earliest <= start <= latest:
                        for rate in tee.get("rates", []):
                            if request.players in rate.get("allowedPlayers", []):
                                if start not in seen_times:
                                    seen_times.add(start)
                                    results.append(
                                        {
                                            "start_time": start,
                                            "course_name": self.name,
                                            "players": request.players,
                                            "distance_miles": 4.0,
                                        }
                                    )
                                break
                except (KeyError, TypeError, ValueError):
                    continue

        return results

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        """Search for tee-time options.

        For TeeItUp providers, search returns tee-time options fetched
        from the availability API. This maintains search-focused behavior.
        """
        from .models import TeeTimeOption

        availability = self.fetch_availability(request)
        options = []

        for item in availability:
            options.append(
                TeeTimeOption(
                    provider=self.name,
                    course_name=item["course_name"],
                    start_time=item["start_time"],
                    players=item["players"],
                    distance_miles=item["distance_miles"],
                    # booking_enabled removed - search-focused
                )
            )

        return options


class NorwoodCCProvider(TeeItUpProvider):
    """Provider for Norwood Country Club (Norwood, MA)."""

    facility_id = "5720"
    x_be_alias = "norwood-country-club"


class SquirrelRunProvider(TeeItUpProvider):
    """Provider for Squirrel Run Golf Course."""

    facility_id = "4328"
    x_be_alias = "squirrel-run-golf-club"


class VillageLinksProvider(TeeItUpProvider):
    """Provider for Village Links"""

    facility_id = "4329"
    x_be_alias = "village-links-golf-club"


class PonkapoagCCProvider(TeeItUpProvider):
    """Provider for Ponkapoag Country Club."""

    facility_id = "17927"
    x_be_alias = "ponkapoag-golf-course"


class PonkapoagCC2Provider(TeeItUpProvider):
    """Provider for Ponkapoag Country Club."""

    facility_id = "17927"
    x_be_alias = "ponkapoag-golf-course-2"


class BraintreeCountryClubProvider(TeeItUpProvider):
    """Provider for Braintree Country Club."""

    facility_id = "16026"
    x_be_alias = "braintree-municipal-golf-course"


class PresidentsGCProvider(TeeItUpProvider):
    """Provider for Presidents Golf Course (Quincy, MA)."""

    facility_id = "17943"
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


def search_all(request: SearchRequest) -> list[TeeTimeOption]:
    results = [
        result
        for provider in get_search_providers()
        for result in provider.search(request)
    ]
    return sorted(results, key=lambda result: result.start_time)
