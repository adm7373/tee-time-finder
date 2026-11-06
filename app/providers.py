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
                    "Accept": "text/html,application/json",
                },
            )
            with urlopen(req, timeout=15) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, OSError, ValueError, UnicodeDecodeError):
            return None

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        """Return raw tee-time slot dicts from this booking site.

        Each dict should contain at least ``start_time`` (a ``datetime.time``),
        and optionally ``players`` and ``price``. Subclasses implement this.
        """
        raise NotImplementedError

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        if not self.base_url:
            return []

        results: list[TeeTimeOption] = []
        earliest = request.earliest_time or time.min
        latest = request.latest_time or time.max

        try:
            slots = self.fetch_availability(request)
        except Exception:
            return []

        if not isinstance(slots, list):
            return []

        for slot in slots:
            if not isinstance(slot, dict):
                continue
            start = slot.get("start_time")
            if not isinstance(start, time):
                continue
            if not (earliest <= start <= latest):
                continue

            players = slot.get("players", request.players)
            try:
                players = int(players)
            except (ValueError, TypeError):
                players = request.players

            distance = slot.get("distance_miles", 0.0)
            try:
                distance = float(distance)
            except (ValueError, TypeError):
                distance = 0.0

            results.append(
                TeeTimeOption(
                    provider=self.name,
                    course_name=str(slot.get("course_name", self.name)),
                    start_time=start,
                    players=max(1, min(8, players)),
                    distance_miles=max(0.0, distance),
                    booking_enabled=False,
                )
            )

        return results


class GraniteLinksProvider(SiteBookingProvider):
    """Example provider for Granite Links Golf Club (Quincy, MA).

    Booking page: https://www.granitelinks.com/reserve-a-tee-time
    Powered by Northstar Technologies (iframe-based booking).

    This is an implementation template. ``fetch_availability`` needs to be
    filled in once the Northstar booking endpoint and response format are
    understood. Until then it returns an empty list.
    """

    name = "granite-links"
    base_url = "https://www.granitelinks.com"
    min_interval_seconds = 5.0

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        # TODO: Investigate Northstar Technologies booking endpoint for
        # Granite Links. The public page at /reserve-a-tee-time loads an
        # iframe; find the underlying API or form submission that returns
        # tee-time slots for the requested date.
        #
        # Example shape to return once implemented:
        #   [{"start_time": time(7, 30), "course_name": "Granite Links Golf Club",
        #     "players": 4, "distance_miles": 8.7}]
        return []


class PresidentsGCProvider(SiteBookingProvider):
    """Example provider for Presidents Golf Course (Quincy, MA).

    Tee times page: https://www.presidentsgc.org/teetimes/
    Tee times can be booked 7 days in advance.

    This is an implementation template. ``fetch_availability`` needs to be
    filled in once the booking system used by Presidents GC is understood.
    Until then it returns an empty list.
    """

    name = "presidents-gc"
    base_url = "https://www.presidentsgc.org"
    min_interval_seconds = 5.0

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        # TODO: Investigate the booking system behind
        # https://www.presidentsgc.org/teetimes/. Determine whether tee-time
        # availability is exposed via an embedded widget, iframe, or direct
        # form submission, then parse the slots here.
        #
        # Example shape to return once implemented:
        #   [{"start_time": time(8, 0), "course_name": "Presidents Golf Course",
        #     "players": 2, "distance_miles": 1.2}]
        return []


class NorwoodCCProvider(SiteBookingProvider):
    """Provider for Norwood Country Club (Norwood, MA).

    Booking page: https://norwood-country-club.book.teeitup.golf/
    Platform: TeeItUp Golf (Next.js frontend backed by phx-api-be-east-1b.kenna.io).

    The availability endpoint is public and search-only. Booking remains
    disabled; the response's rate IDs are intentionally not exposed yet.
    """

    name = "norwood-cc"
    base_url = "https://norwood-country-club.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "5720"
    min_interval_seconds = 5.0

    def fetch_availability(self, request: SearchRequest) -> list[dict]:
        from datetime import timedelta

        results = []
        today = request.date

        current = today
        checked = 0
        max_days = 1

        while checked < max_days:
            query = urlencode({
                "date": current.isoformat(),
                "facilityIds": self.facility_id,
                "returnPromotedRates": "true",
            })
            payload = self._fetch_json(f"{self.api_url}?{query}")
            if not isinstance(payload, list):
                current += timedelta(days=1)
                checked += 1
                continue

            seen_times = set()
            for day in payload:
                tees = day.get("teetimes", [])
                total = day.get("totalAvailableTeetimes", 0)
                if total == 0:
                    continue

                for tee in tees:
                    try:
                        start = datetime.fromisoformat(
                            tee["teetime"].replace("Z", "+00:00")
                        ).astimezone(ZoneInfo("America/New_York")).time()

                        earliest = request.earliest_time or time.min
                        latest = request.latest_time or time.max
                        if earliest <= start <= latest:
                            for rate in tee.get("rates", []):
                                if request.players in rate.get("allowedPlayers", []):
                                    if start not in seen_times:
                                        seen_times.add(start)
                                        results.append({
                                            "start_time": start,
                                            "course_name": "Norwood Country Club",
                                            "players": request.players,
                                            "distance_miles": 4.0,
                                        })
                                    break
                    except (KeyError, TypeError, ValueError):
                        continue

            current += timedelta(days=1)
            checked += 1

        return results

    def _fetch_json(self, url: str) -> object:
        import ssl

        request = Request(url, headers={
            "Accept": "application/json",
            "x-be-alias": "norwood-country-club",
            "User-Agent": "tee-time-finder/0.1",
        })
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


def get_search_providers() -> list[SearchProvider]:
    providers: list[SearchProvider] = [MockSearchProvider()]
    # Norwood now has a verified public read-only availability endpoint.
    providers.append(NorwoodCCProvider())
    # Add other site-specific providers here as they are implemented:
    # providers.append(GraniteLinksProvider())
    # providers.append(PresidentsGCProvider())
    return providers


def search_all(request: SearchRequest) -> list[TeeTimeOption]:
    results = [result for provider in get_search_providers() for result in provider.search(request)]
    return sorted(results, key=lambda result: result.start_time)

