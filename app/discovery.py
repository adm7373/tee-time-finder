"""Course discovery: find golf courses near a ZIP and record the good ones.

A discovery run answers "what exists near me" and writes each usable course to
the courses table. The next step — figuring out how each course takes bookings
— is the fingerprint Lambda's job; this one only geocodes and filters.

A run takes ``zip_code`` and ``radius_miles`` (or nothing, for the defaults)
and, for each Nominatim golf-course hit inside the box:

- drops what is outside the real radius, and what is already known;
- for the nearest few unknown courses, probes TeeItUp aliases;
- writes a row per course: coordinates, website, and, when a TeeItUp engine
  was found, its alias and facility ID.

``known`` rows (already in the table) and ``already_configured`` rows (a
facility ID the app already searches) are counted, not re-probed. ``error`` is
only for a run-level failure — a bad ZIP, a Nominatim outage — never for one
course turning out to be unusable; unusable courses simply earn a row that says
so.
"""

import datetime
import logging
from dataclasses import dataclass, field

from .discovery_config import SEARCH_BOXES
from .geo import CoursePlace, GeocodeStore, Geocoder, distance_miles
from .teeitup_probe import confirm_course, facility_id_for, slug_candidates

logger = logging.getLogger(__name__)

#: How many unknown courses a single run probes against TeeItUp. Bounding this
#: keeps a scheduled run inside Lambda's time limit; the schedule re-runs often
#: enough that everything unknown is eventually covered.
MAX_PROBES_PER_RUN = 10


@dataclass
class DiscoveryResult:
    run_id: str
    added: int = 0
    tee_it_up: int = 0
    known: int = 0
    already_configured: int = 0
    skipped_outside_radius: int = 0
    error: str = ""
    details: list[str] = field(default_factory=list)


class CourseStore:
    """DynamoDB-backed record of discovered courses.

    One item per course, keyed by a slug of its name::

        pk = "course#norwood-country-club"

    so a re-run of discovery overwrites rather than duplicates. The fields are
    written by two different Lambdas at different times: discovery fills in the
    name, coordinates and any TeeItUp pairing it could confirm, while the
    fingerprint Lambda later fills in (or corrects) the provider verdict.
    """

    def __init__(self, table_name: str | None, table=None):
        import boto3

        self.table_name = table_name
        if table is not None:
            self._table = table
        elif table_name:
            self._table = boto3.resource("dynamodb").Table(table_name)
        else:
            self._table = None

    @staticmethod
    def slug(name: str) -> str:
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "course"

    def get(self, name: str) -> dict | None:
        if self._table is None:
            return None
        try:
            item = self._table.get_item(Key={"pk": f"course#{self.slug(name)}"})
            return item.get("Item")
        except Exception as error:  # best-effort: discovery must survive the table
            logger.warning("course lookup failed for %s: %s", name, error)
            return None

    def put(self, item: dict) -> None:
        if self._table is None:
            return
        try:
            self._table.put_item(Item=item)
        except Exception as error:
            logger.warning("course write failed for %s: %s", item.get("pk"), error)


def discover(
    zip_code: str = "02169",
    radius_miles: float = 25.0,
    *,
    store: CourseStore | None = None,
    geocode_store: GeocodeStore | None = None,
    geocoder: Geocoder | None = None,
    configured_facility_ids: set[str] | None = None,
    date: str | None = None,
    run_id: str | None = None,
) -> DiscoveryResult:
    """Run one discovery pass and record what it found."""
    from .providers import get_search_providers

    result = DiscoveryResult(
        run_id=run_id or datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    store = store or CourseStore(None)
    geocode_store = geocode_store or GeocodeStore(None)
    geocoder = geocoder or Geocoder("tee-time-finder/0.1 (personal tool)")
    if configured_facility_ids is None:
        configured_facility_ids = {
            provider.facility_id for provider in get_search_providers()
        }
    check_date = date or datetime.date.today().isoformat()

    origin = geocode_store.zip(zip_code, geocoder)
    if origin is None:
        result.error = f"could not geocode ZIP {zip_code}"
        return result

    places = geocoder.golf_course_places(origin, radius_miles)
    # Nominatim returns a course as both a node and a way; probe each name once.
    deduped: dict[str, CoursePlace] = {}
    for place in places:
        prior = deduped.get(place.name)
        if prior is None or distance_miles(origin, place.coords) < distance_miles(
            origin, prior.coords
        ):
            deduped[place.name] = place

    probes = 0
    for place in sorted(
        deduped.values(), key=lambda p: distance_miles(origin, p.coords)
    ):
        miles = distance_miles(origin, place.coords)
        if miles > radius_miles:
            result.skipped_outside_radius += 1
            continue
        if store.get(place.name) is not None:
            result.known += 1
            continue

        row: dict = {
            "pk": f"course#{CourseStore.slug(place.name)}",
            "name": place.name,
            "lat": str(place.coords.lat),
            "lon": str(place.coords.lon),
            "display_name": place.coords.display_name,
            "distance_from_zip": round(miles, 1),
            "zip_code": zip_code,
            "website": place.website,
            "provider": "unknown",
            "last_checked": result.run_id,
        }

        if probes < MAX_PROBES_PER_RUN:
            probes += 1
            for alias in slug_candidates(place.name):
                facility_id = facility_id_for(alias)
                if not facility_id:
                    continue
                if facility_id in configured_facility_ids:
                    row.update(
                        {
                            "provider": "teeitup",
                            "booking_domain": f"{alias}.book.teeitup.golf",
                            "facility_id": facility_id,
                        }
                    )
                    result.already_configured += 1
                    break
                if confirm_course(alias, facility_id, check_date):
                    row.update(
                        {
                            "provider": "teeitup-confirmed",
                            "booking_domain": f"{alias}.book.teeitup.golf",
                            "facility_id": facility_id,
                        }
                    )
                    result.tee_it_up += 1
                    result.details.append(
                        f"{place.name} -> {alias} (facility {facility_id})"
                    )
                    break

        store.put(row)
        result.added += 1

    return result


def handler(event: dict | None = None, context=None) -> dict:
    """Lambda entry point: discover courses near a ZIP and store them.

    ``event`` may carry ``zip_code`` and ``radius_miles``. With no arguments the
    run covers :data:`SEARCH_BOXES` around the default ZIP, matching the manual
    habit — one wide sweep per scheduled run.
    """
    from .config import get_settings

    event = event or {}
    settings = get_settings()
    store = CourseStore(settings.courses_table_name)
    geocode_store = GeocodeStore(settings.geocode_table_name)
    geocoder = Geocoder(settings.nominatim_user_agent)

    boxes: list[tuple[str, float]]
    if "zip_code" in event or "radius_miles" in event:
        boxes = [
            ("custom", float(event.get("radius_miles") or settings.default_radius))
        ]
        zip_code = str(event.get("zip_code") or settings.default_search_zip)
    else:
        boxes = list(SEARCH_BOXES)
        zip_code = settings.default_search_zip

    total = DiscoveryResult(
        run_id=datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    for label, radius in boxes:
        result = discover(
            zip_code,
            radius,
            store=store,
            geocode_store=geocode_store,
            geocoder=geocoder,
            run_id=f"{total.run_id}#{label}",
        )
        total.added += result.added
        total.tee_it_up += result.tee_it_up
        total.known += result.known
        total.already_configured += result.already_configured
        total.skipped_outside_radius += result.skipped_outside_radius
        total.details.extend(result.details)
        if result.error and not total.error:
            total.error = result.error

    logger.info(
        "discovery %s: added=%d teeitup=%d known=%d already=%d error=%r",
        total.run_id,
        total.added,
        total.tee_it_up,
        total.known,
        total.already_configured,
        total.error,
    )
    return {
        "run_id": total.run_id,
        "added": total.added,
        "tee_it_up": total.tee_it_up,
        "known": total.known,
        "already_configured": total.already_configured,
        "skipped_outside_radius": total.skipped_outside_radius,
        "error": total.error,
        "details": total.details,
    }
