"""Course discovery: find golf courses near a ZIP and record the good ones.

A run does one of two things, or both:

**Sweep** — ask Nominatim what golf courses exist in a box and write a row for
any that are not already known. The OSM course list changes on the order of
months, so the schedule sweeps on the first run of the month and no other
night. Re-querying the same three boxes nightly would cost three requests to
learn nothing, which is the one pattern Nominatim's usage policy is clearest
about.

**Backlog** — work through rows already in the table that are still marked
``unknown``, probing TeeItUp aliases. This is what a nightly run mostly does.
Rows are ordered by fewest attempts first, so the per-run probe budget walks
the whole set instead of grinding on the same nearest ten, and every row
records how many times it has been tried so a course that never resolves is
retried on a widening backoff rather than every night forever.

The distinction that matters: ``known`` means *classified*, not *has a row*. A
row with ``provider: "unknown"`` is a course we know exists but have not worked
out, and it stays eligible until it is either classified or has exhausted its
attempts.
"""

import datetime
import logging
from dataclasses import dataclass, field

from .discovery_config import SEARCH_BOXES
from .geo import CoursePlace, GeocodeStore, Geocoder, distance_miles
from .teeitup_probe import confirm_course, facility_id_for, slug_candidates

logger = logging.getLogger(__name__)

#: How many unknown courses a single run probes against TeeItUp. Bounding this
#: keeps a scheduled run inside Lambda's time limit; the backlog order means
#: the next run picks up where this one stopped.
MAX_PROBES_PER_RUN = 10

#: Probe attempts before a course is left alone. A course that has failed this
#: many times is more likely absent from TeeItUp than unlucky, and the
#: fingerprint Lambda (which tries real websites) is the better net for it.
MAX_ATTEMPTS = 5

#: Providers that mean "we know what this is". Anything else is still eligible.
CLASSIFIED_PROVIDERS = frozenset(
    {
        "teeitup",
        "teeitup-confirmed",
        "golfnow",
        "chronogolf",
        "foreup",
        "clubcaddie",
        "golfback",
        "teesnap",
        "gallus",
        "supreme-golf",
        "other",
    }
)


def attempts_for(row: dict | None) -> int:
    """How many discovery probes a row has had, tolerating old/handmade rows."""
    if not row:
        return 0
    try:
        return int(row.get("discovery_attempts") or 0)
    except (TypeError, ValueError):
        return 0


def is_classified(row: dict | None) -> bool:
    return bool(row) and row.get("provider") in CLASSIFIED_PROVIDERS


def is_exhausted(row: dict | None) -> bool:
    return attempts_for(row) >= MAX_ATTEMPTS


def needs_probe(row: dict | None) -> bool:
    """A row worth probing: not classified, and attempts left."""
    return not is_classified(row) and not is_exhausted(row)


def due_for_sweep(last_swept: str | None, now: datetime.datetime) -> bool:
    """True on the first run of a month, or when we have never swept.

    Monthly is generous for a list that changes a few times a year, and it keeps
    the nightly job honest: it only asks Nominatim when there is a reason to
    expect an answer that differs.
    """
    if not last_swept:
        return True
    try:
        swept = datetime.datetime.fromisoformat(last_swept)
    except ValueError:
        return True
    if swept.tzinfo is None:
        swept = swept.replace(tzinfo=datetime.timezone.utc)
    return (now.year, now.month) != (swept.year, swept.month)


@dataclass
class DiscoveryResult:
    run_id: str
    swept: bool = False
    added: int = 0
    probed: int = 0
    tee_it_up: int = 0
    known: int = 0
    exhausted: int = 0
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

    def scan(self) -> list[dict]:
        """Every course row, or none if the table is missing or unhappy."""
        if self._table is None:
            return []
        rows: list[dict] = []
        start_key = None
        while True:
            kwargs: dict = {}
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            try:
                page = self._table.scan(**kwargs)
            except Exception as error:
                logger.warning("course scan failed: %s", error)
                return rows
            rows.extend(page.get("Items", []))
            start_key = page.get("LastEvaluatedKey")
            if not start_key:
                return rows

    def meta(self, key: str) -> dict | None:
        """A bookkeeping row that is not a course, e.g. the last sweep time."""
        if self._table is None:
            return None
        try:
            item = self._table.get_item(Key={"pk": f"meta#{key}"})
            return item.get("Item")
        except Exception as error:
            logger.warning("meta lookup failed for %s: %s", key, error)
            return None

    def put_meta(self, key: str, value: str) -> None:
        self.put({"pk": f"meta#{key}", "value": value})


def _probe(row: dict, configured_facility_ids: set[str], check_date: str) -> str:
    """Try TeeItUp aliases for one course, updating ``row`` in place.

    Returns what happened: ``teeitup`` (new pairing confirmed), ``already``
    (a facility the app already searches), or ``unknown``.
    """
    for alias in slug_candidates(row["name"]):
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
            return "already"
        if confirm_course(alias, facility_id, check_date):
            row.update(
                {
                    "provider": "teeitup-confirmed",
                    "booking_domain": f"{alias}.book.teeitup.golf",
                    "facility_id": facility_id,
                }
            )
            return "teeitup"
    return "unknown"


def _sweep(
    result: DiscoveryResult,
    zip_code: str,
    radius_miles: float,
    store: CourseStore,
    geocode_store: GeocodeStore,
    geocoder: Geocoder,
    configured_facility_ids: set[str],
) -> None:
    """Ask Nominatim what exists in the box and record anything new."""
    origin = geocode_store.zip(zip_code, geocoder)
    if origin is None:
        result.error = f"could not geocode ZIP {zip_code}"
        return

    places = geocoder.golf_course_places(origin, radius_miles)
    # Nominatim returns a course as both a node and a way; keep one per name.
    deduped: dict[str, CoursePlace] = {}
    for place in places:
        prior = deduped.get(place.name)
        if prior is None or distance_miles(origin, place.coords) < distance_miles(
            origin, prior.coords
        ):
            deduped[place.name] = place

    for place in deduped.values():
        miles = distance_miles(origin, place.coords)
        if miles > radius_miles:
            result.skipped_outside_radius += 1
            continue
        existing = store.get(place.name)
        if existing is not None:
            # Already recorded. Its classification is the backlog pass's
            # business, not this one's; a sweep only adds what is missing.
            if is_classified(existing):
                result.known += 1
            continue

        row = {
            "pk": f"course#{CourseStore.slug(place.name)}",
            "name": place.name,
            "lat": str(place.coords.lat),
            "lon": str(place.coords.lon),
            "display_name": place.coords.display_name,
            "distance_from_zip": round(miles, 1),
            "zip_code": zip_code,
            "website": place.website,
            "provider": "unknown",
            "discovery_attempts": 0,
            "last_checked": result.run_id,
        }
        # A newly found course is the likeliest to resolve, so probe it now
        # rather than waiting for the backlog pass to reach it.
        if result.probed < MAX_PROBES_PER_RUN:
            result.probed += 1
            row["discovery_attempts"] = 1
            outcome = _probe(row, configured_facility_ids, _today())
            if outcome == "teeitup":
                result.tee_it_up += 1
                result.details.append(
                    f"{row['name']} -> {row['booking_domain']} "
                    f"(facility {row['facility_id']})"
                )
            elif outcome == "already":
                result.already_configured += 1

        store.put(row)
        result.added += 1


def _today() -> str:
    return datetime.date.today().isoformat()


def _backlog(
    result: DiscoveryResult,
    store: CourseStore,
    configured_facility_ids: set[str],
) -> None:
    """Work through recorded courses that are still unclassified.

    Ordered by fewest attempts first, so the probe budget covers the whole set
    over successive runs instead of returning to the same nearest ten. Each
    row's attempt count is incremented and written back even when nothing
    resolved, which is what gives a hopeless course a finite number of tries.
    """
    rows = [row for row in store.scan() if row.get("pk", "").startswith("course#")]
    candidates = [row for row in rows if needs_probe(row)]
    candidates.sort(key=lambda row: (attempts_for(row), row.get("name", "")))

    for row in candidates:
        if result.probed >= MAX_PROBES_PER_RUN:
            break
        result.probed += 1
        row["discovery_attempts"] = attempts_for(row) + 1
        row["last_checked"] = result.run_id
        outcome = _probe(row, configured_facility_ids, _today())
        if outcome == "teeitup":
            result.tee_it_up += 1
            result.details.append(
                f"{row['name']} -> {row['booking_domain']} "
                f"(facility {row['facility_id']})"
            )
        elif outcome == "already":
            result.already_configured += 1
        if is_exhausted(row) and not is_classified(row):
            result.exhausted += 1
        store.put(row)


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
    force_sweep: bool = False,
    now: datetime.datetime | None = None,
) -> DiscoveryResult:
    """Run one discovery pass: sweep if due, then work the backlog."""
    from .providers import get_search_providers

    now = now or datetime.datetime.now(datetime.timezone.utc)
    result = DiscoveryResult(run_id=run_id or now.isoformat())
    store = store or CourseStore(None)
    geocode_store = geocode_store or GeocodeStore(None)
    geocoder = geocoder or Geocoder("tee-time-finder/0.1 (personal tool)")
    if configured_facility_ids is None:
        configured_facility_ids = {
            provider.facility_id for provider in get_search_providers()
        }

    last_swept = (store.meta("last_sweep") or {}).get("value")
    if force_sweep or due_for_sweep(last_swept, now):
        _sweep(
            result,
            zip_code,
            radius_miles,
            store,
            geocode_store,
            geocoder,
            configured_facility_ids,
        )
        if not result.error:
            result.swept = True
            store.put_meta("last_sweep", now.isoformat())

    _backlog(result, store, configured_facility_ids)
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
    force_sweep = bool(event.get("force_sweep"))
    if "zip_code" in event or "radius_miles" in event:
        boxes = [
            ("custom", float(event.get("radius_miles") or settings.default_radius))
        ]
        zip_code = str(event.get("zip_code") or settings.default_search_zip)
        # An explicit box is a request to look there, so sweep it regardless of
        # when the last scheduled sweep ran.
        force_sweep = True
    else:
        boxes = list(SEARCH_BOXES)
        zip_code = settings.default_search_zip

    now = datetime.datetime.now(datetime.timezone.utc)
    total = DiscoveryResult(run_id=now.isoformat())
    for label, radius in boxes:
        result = discover(
            zip_code,
            radius,
            store=store,
            geocode_store=geocode_store,
            geocoder=geocoder,
            run_id=f"{total.run_id}#{label}",
            force_sweep=force_sweep,
            now=now,
        )
        total.swept = total.swept or result.swept
        total.added += result.added
        total.probed += result.probed
        total.tee_it_up += result.tee_it_up
        total.known += result.known
        total.exhausted += result.exhausted
        total.already_configured += result.already_configured
        total.skipped_outside_radius += result.skipped_outside_radius
        total.details.extend(result.details)
        if result.error and not total.error:
            total.error = result.error

    logger.info(
        "discovery %s: swept=%s added=%d probed=%d teeitup=%d known=%d "
        "exhausted=%d error=%r",
        total.run_id,
        total.swept,
        total.added,
        total.probed,
        total.tee_it_up,
        total.known,
        total.exhausted,
        total.error,
    )
    return {
        "run_id": total.run_id,
        "swept": total.swept,
        "added": total.added,
        "probed": total.probed,
        "tee_it_up": total.tee_it_up,
        "known": total.known,
        "exhausted": total.exhausted,
        "already_configured": total.already_configured,
        "skipped_outside_radius": total.skipped_outside_radius,
        "error": total.error,
        "details": total.details,
    }
