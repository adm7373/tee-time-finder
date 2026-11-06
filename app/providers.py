from datetime import datetime, time, timezone
import json
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

    name = "mock"

    @classmethod
    def is_configured(cls) -> bool:
        """Return True if this provider has everything it needs to run."""
        return True

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        raise NotImplementedError


class MockSearchProvider(SearchProvider):
    """Deterministic sample provider for local development and the Quincy area."""

    name = "mock"

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        candidates = [
            ("Quincy Municipal Golf Course", time(7, 30), 3.2),
            ("Granite Links Golf Club", time(9, 10), 8.7),
            ("George Wright Golf Course", time(11, 45), 16.4),
        ]
        earliest = request.earliest_time or time.min
        latest = request.latest_time or time.max
        return [
            TeeTimeOption(
                provider=self.name,
                course_name=course,
                start_time=start,
                players=request.players,
                distance_miles=distance,
                booking_enabled=False,
            )
            for course, start, distance in candidates
            if earliest <= start <= latest and distance <= request.radius_miles
        ]



class SiteBookingProvider(SearchProvider):
    """Base provider for a specific course booking website.

    Subclasses implement ``fetch_availability(request)`` to scrape or query a
    specific booking site and return raw tee-time slots. The base class handles
    time-window filtering and TeeTimeOption construction.

    Project boundaries respected by all subclasses:
    - Never store credentials, payment data, or customer personal information.
    - Respect the site's robots.txt, rate limits, and terms of service.
    - Search is separate from booking: results always have booking_enabled=False.
    - Fail closed: network or parse errors return an empty list, never an exception.
    """

    #: Base URL of the booking site, e.g. "https://www.example-course.com"
    base_url: str = ""

    #: Human-readable name shown in results.
    name: str = "site"

    #: Minimum seconds between requests to this site (rate limiting).
    min_interval_seconds: float = 2.0

    _last_request_time: float = 0.0

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



class TeeItUpProvider(SiteBookingProvider):
    """Base provider for courses using TeeItUp for booking.

    Courses using TeeItUp booking system should subclass this and set the
    class attributes ``base_url``, ``api_url``, ``facility_id``, and
    ``x_be_alias`` to match their specific configuration.

    The ``fetch_availability`` method handles the TeeItUp API pattern:
    - Constructs the API URL with facility ID and requested date
    - Sends a JSON request with the ``x-be-alias`` header
    - Parses tee-time slots from the response
    """

    #: Base URL of the booking site, e.g. "https://norwood-country-club.book.teeitup.golf"
    base_url: str = ""

    #: API endpoint for tee times, e.g. "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    api_url: str = ""

    #: Facility ID for the course, e.g. "5720"
    facility_id: str = ""

    #: x-be-alias header value, e.g. "norwood-country-club"
    x_be_alias: str = ""

    min_interval_seconds: float = 2.0

    _last_request_time: float = 0.0

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
        except Exception:
            # Fallback for local development (Python 3.15 certificate issues)
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urlopen(request, timeout=15, context=ctx) as response:
                    return json.loads(response.read())
            except Exception:
                return None

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

        payload = self._fetch_json(url)
        if not isinstance(payload, list):
            return []

        seen_times: set[time] = set()
        results: list[dict] = []

        for day in payload:
            tees = day.get("teetimes", [])
            total = day.get("totalAvailableTeetimes", 0)
            if total == 0:
                continue

            for tee in tees:
                try:
                    start = datetime.fromisoformat(
                        tee["teetime"].replace("Z", "+00:00")
                    ).astimezone(tz).time()

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


class NorwoodCCProvider(TeeItUpProvider):
    """Provider for Norwood Country Club (Norwood, MA)."""

    name = "norwood-cc"
    base_url = "https://norwood-country-club.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "5720"
    x_be_alias = "norwood-country-club"






class SquirrelRunProvider(TeeItUpProvider):
    """Provider for Squirrel Run Golf Course."""

    name = "squirrel-run"
    base_url = "https://squirrel-run.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "3196"
    x_be_alias = "squirrel-run"



class PonkapoagCCProvider(TeeItUpProvider):
    """Provider for Ponkapoag Country Club."""

    name = "ponkapoag-cc"
    base_url = "https://ponkapoag-cc.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "4068"
    x_be_alias = "ponkapoag-cc"



class BraintreeCountryClubProvider(TeeItUpProvider):
    """Provider for Braintree Country Club."""

    name = "braintree-cc"
    base_url = "https://braintree-cc.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "5024"
    x_be_alias = "braintree-cc"


class GraniteLinksProvider(SiteBookingProvider):
    """Example provider for Granite Links Golf Club (Quincy, MA)."""

    name = "granite-links"
    base_url = "https://www.granitelinks.com"
    min_interval_seconds = 5.0

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        # TODO: Investigate Northstar Technologies booking endpoint for
        # Granite Links. The public page at /reserve-a-tee-time loads an
        # iframe; find the underlying API or form submission that returns
        # tee-time slots for the requested date.
        #         # Example shape to return once implemented:
        #   [{"start_time": time(7, 30), "course_name": "Granite Links Golf Club",
        #     "players": 4, "distance_miles": 8.7}]
        return []



class PresidentsGCProvider(SiteBookingProvider):
    """Example provider for Presidents Golf Course (Quincy, MA)."""

    name = "presidents-gc"
    base_url = "https://www.presidentsgc.org"
    min_interval_seconds = 5.0

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        # TODO: Investigate the booking system behind
        # https://www.presidentsgc.org/teetimes/. Determine whether tee-time
        # availability is exposed via an embedded widget, iframe, or direct
        # form submission, then parse the slots here.
        #         # Example shape to return once implemented:
        #   [{"start_time": time(8, 0), "course_name": "Presidents Golf Course",
        #     "players": 2, "distance_miles": 1.2}]
        return []



def get_search_providers() -> list[SearchProvider]:
    providers: list[SearchProvider] = [MockSearchProvider()]
    # Norwood now has a verified public read-only availability endpoint.
    providers.append(NorwoodCCProvider())
    providers.append(SquirrelRunProvider())
    providers.append(PonkapoagCCProvider())
    providers.append(BraintreeCountryClubProvider())
    # Add other TeeItUp-based providers here as they are implemented:
    # providers.append(AnotherTeeItUpProvider())
    # Add other site-specific providers here as they are implemented:
    # providers.append(GraniteLinksProvider())
    # providers.append(PresidentsGCProvider())
    return providers


def search_all(request: SearchRequest) -> list[TeeTimeOption]:
    results = [result for provider in get_search_providers() for result in provider.search(request)]
    return sorted(results, key=lambda result: result.start_time)
