"""ZIP and course geocoding, cached in DynamoDB.

Course coordinates are seeded once by ``scripts/geocode_courses.py`` and read
from the same table at runtime. ZIP coordinates are resolved lazily: the first
search for a ZIP asks Nominatim and stores the answer, later searches read it
back.

Table layout, one item per location::

    pk = "zip#02169"      {lat, lon, display_name, source, geocoded_at}
    pk = "course#5720"    {lat, lon, display_name, source, geocoded_at}
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import logging
import ssl
import time

import boto3
import certifi
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

#: Mean Earth radius in miles.
EARTH_RADIUS_MILES = 3958.7613

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: Nominatim's usage policy asks for at most one request per second.
NOMINATIM_MIN_INTERVAL_SECONDS = 1.0

_last_nominatim_call = 0.0


@lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    """A verifying TLS context backed by certifi's CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class Coordinates:
    lat: float
    lon: float
    display_name: str = ""
    source: str = "nominatim"


def distance_miles(a: Coordinates, b: Coordinates) -> float:
    """Great-circle distance between two points, in miles."""
    lat1, lon1, lat2, lon2 = map(radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * asin(sqrt(h))


@dataclass(frozen=True)
class CoursePlace:
    """One Nominatim golf-course hit: where it is and where its website lives."""

    name: str
    coords: Coordinates
    website: str = ""
    display_name: str = ""


class Geocoder:
    """Nominatim client that honours the one-request-per-second policy."""

    def __init__(
        self, user_agent: str, min_interval: float = NOMINATIM_MIN_INTERVAL_SECONDS
    ):
        self.user_agent = user_agent
        self.min_interval = min_interval

    def _get(self, params: dict) -> list[dict]:
        global _last_nominatim_call
        elapsed = time.monotonic() - _last_nominatim_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        _last_nominatim_call = time.monotonic()

        request = Request(
            f"{NOMINATIM_URL}?{urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
        )
        with urlopen(request, timeout=15, context=_tls_context()) as response:
            return json.loads(response.read())

    def by_zip(self, zip_code: str) -> Coordinates | None:
        """Centroid of a US ZIP code."""
        hits = self._get(
            {"postalcode": zip_code, "country": "us", "format": "json", "limit": 1}
        )
        return _first(hits)

    def by_query(self, query: str) -> Coordinates | None:
        """Best match for a free-text address or place name."""
        hits = self._get({"q": query, "format": "json", "limit": 1})
        return _first(hits)

    def golf_course_places(
        self, origin: Coordinates, radius_miles: float
    ) -> list["CoursePlace"]:
        """Golf courses around ``origin`` with coordinates and website if known.

        Nominatim has no radius search, so this asks inside a bounding box and
        lets the caller apply the real distance. ``extratags=1`` is requested
        so candidates carry their website when OpenStreetMap has one — the
        fingerprint step needs somewhere to start looking for a booking page.
        """
        lat_span = radius_miles / 69.0
        lon_span = radius_miles / (69.0 * max(cos(radians(origin.lat)), 0.1))
        viewbox = (
            f"{origin.lon - lon_span:.4f},{origin.lat + lat_span:.4f},"
            f"{origin.lon + lon_span:.4f},{origin.lat - lat_span:.4f}"
        )
        hits = self._get(
            {
                "q": "golf_course",
                "format": "json",
                "limit": 50,
                "bounded": 1,
                "viewbox": viewbox,
                "extratags": 1,
            }
        )
        places: list[CoursePlace] = []
        for hit in hits:
            coords = _first([hit])
            if coords is None:
                continue
            # "Norwood Country Club, 400, Providence Highway, ..." -> first part
            name = hit.get("display_name", "").split(",")[0].strip()
            if not name:
                continue
            website = ((hit.get("extratags") or {}).get("website") or "").strip()
            places.append(
                CoursePlace(
                    name=name,
                    coords=coords,
                    website=website,
                    display_name=hit.get("display_name", ""),
                )
            )
        return places

    def golf_courses_near(
        self, origin: Coordinates, radius_miles: float
    ) -> list[tuple[str, Coordinates]]:
        """Golf courses within ``radius_miles``, as (name, coordinates) pairs.

        Thin wrapper over :meth:`golf_course_places` for callers that only need
        a name and a point; discovery and seeding use the richer method.
        """
        return [
            (place.name, place.coords)
            for place in self.golf_course_places(origin, radius_miles)
        ]


def _first(hits: list[dict]) -> Coordinates | None:
    if not hits:
        return None
    hit = hits[0]
    try:
        return Coordinates(
            lat=float(hit["lat"]),
            lon=float(hit["lon"]),
            display_name=hit.get("display_name", ""),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("unusable geocode hit: %r", hit)
        return None


class GeocodeStore:
    """DynamoDB-backed cache of ZIP and course coordinates.

    Reads are best-effort: if DynamoDB is unavailable the search still runs, just
    without a cached lookup, so a table problem degrades to slower searches
    rather than a 500.
    """

    def __init__(self, table_name: str | None, table=None):
        self.table_name = table_name
        self._table = table
        self._memory: dict[str, Coordinates | None] = {}

    @property
    def table(self):
        if self._table is None:
            if not self.table_name:
                return None
            self._table = boto3.resource("dynamodb").Table(self.table_name)
        return self._table

    @staticmethod
    def _zip_key(zip_code: str) -> str:
        return f"zip#{zip_code}"

    @staticmethod
    def _course_key(facility_id: str) -> str:
        return f"course#{facility_id}"

    def _get(self, key: str) -> Coordinates | None:
        if key in self._memory:
            return self._memory[key]
        table = self.table
        if table is None:
            return None
        try:
            item = table.get_item(Key={"pk": key}).get("Item")
        except (BotoCoreError, ClientError) as error:
            logger.warning("geocode lookup failed for %s: %s", key, error)
            return None
        if not item:
            return None
        try:
            coords = Coordinates(
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                display_name=item.get("display_name", ""),
                source=item.get("source", "cache"),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("malformed geocode item for %s: %r", key, item)
            return None
        self._memory[key] = coords
        return coords

    def _put(self, key: str, coords: Coordinates) -> None:
        self._memory[key] = coords
        table = self.table
        if table is None:
            return
        try:
            table.put_item(
                Item={
                    "pk": key,
                    "lat": str(coords.lat),
                    "lon": str(coords.lon),
                    "display_name": coords.display_name,
                    "source": coords.source,
                    "geocoded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except (BotoCoreError, ClientError) as error:
            # A failed write only costs us a repeat geocode next time.
            logger.warning("geocode write failed for %s: %s", key, error)

    def zip(
        self, zip_code: str, geocoder: Geocoder | None = None
    ) -> Coordinates | None:
        """Cached ZIP coordinates, geocoding and storing them on a miss."""
        coords = self._get(self._zip_key(zip_code))
        if coords is not None:
            return coords
        if geocoder is None:
            return None
        coords = geocoder.by_zip(zip_code)
        if coords is None:
            logger.info("no geocode result for ZIP %s", zip_code)
            return None
        self._put(self._zip_key(zip_code), coords)
        return coords

    def store_zip(self, zip_code: str, coords: Coordinates) -> None:
        self._put(self._zip_key(zip_code), coords)

    def course(self, facility_id: str) -> Coordinates | None:
        """Course coordinates, seeded ahead of time by the geocode script."""
        return self._get(self._course_key(facility_id))

    def store_course(self, facility_id: str, coords: Coordinates) -> None:
        self._put(self._course_key(facility_id), coords)
