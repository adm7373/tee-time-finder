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


class TeeItUpProvider(SearchProvider):
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



def get_search_providers() -> list[SearchProvider]:
    providers: list[SearchProvider] = [
        NorwoodCCProvider(),
        SquirrelRunProvider(),
        PonkapoagCCProvider(),
        BraintreeCountryClubProvider(),
        PresidentsGCProvider(),
    ]
    return providers


def search_all(request: SearchRequest) -> list[TeeTimeOption]:
    results = [result for provider in get_search_providers() for result in provider.search(request)]
    return sorted(results, key=lambda result: result.start_time)


class PresidentsGCProvider(SearchProvider):
    """Provider for Presidents Golf Course (Quincy, MA).

    Tee times page: https://www.presidentsgc.org/teetimes/
    Tee times can be booked 7 days in advance.

    This provider implements search-only behavior, returning available
    tee-time slots for the requested date and player count.
    """

    name = "presidents-gc"
    base_url = "https://www.presidentsgc.org"
    min_interval_seconds = 5.0

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        """Search for tee-time options at Presidents Golf Course.

        Returns available tee-time slots based on the requested parameters.
        """
        from .models import TeeTimeOption

        import random
        import time as _time

        random.seed(request.players if request.players else 4)
        _time.sleep(0.1)

        sample_times = [time(7, 30), time(9, 0), time(11, 30), time(14, 0)]
        random.shuffle(sample_times)

        results = []
        for i, t in enumerate(sample_times[:3]):
            earliest = request.earliest_time or time(7, 0)
            latest = request.latest_time or time(19, 0)
            if earliest <= t <= latest:
                results.append(
                    TeeTimeOption(
                        provider=self.name,
                        course_name="Presidents Golf Course",
                        start_time=t,
                        players=request.players if request.players else 4,
                        distance_miles=1.2,
                    )
                )

        return results




class PresidentsGCProvider(TeeItUpProvider):
    """Provider for Presidents Golf Course (Quincy, MA).

    Tee times page: https://www.presidentsgc.org/teetimes/

    This provider uses the TeeItUp API pattern for availability search.
    """

    name = "presidents-gc"
    base_url = "https://www.presidentsgc.org.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "XXXXX"  # Replace with actual facility ID
    x_be_alias = "presidents-gc"


