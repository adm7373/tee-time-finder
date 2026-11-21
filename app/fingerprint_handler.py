"""Fingerprint Lambda: classify how unfingerprinted courses take bookings.

Reads course rows from the courses table whose provider is still ``unknown``,
fingerprints a bounded batch of the least-recently-checked ones, and writes the
verdicts back. The bound keeps a scheduled run inside Lambda's time limit, and
checking oldest first means a row nobody can classify does not starve the rest
of the backlog.

The event may carry ``limit`` (default 5) and ``dry_run`` (classify without
writing). The response counts what happened: classified, still unknown, and any
run-level error. A course that stays unknown is not a failure — it just keeps
its guesses for the next run.
"""

import datetime
import logging

from .logging_setup import configure_logging

logger = logging.getLogger(__name__)

#: Courses fingerprinted per run. Each course costs up to a dozen slow page
#: fetches, so this stays small; the schedule provides the repetition.
DEFAULT_LIMIT = 5

#: Items DynamoDB may evaluate in one scan page. This is deliberately not the
#: batch size: ``Limit`` counts items read, before ``FilterExpression`` runs, so
#: a page sized to the batch can come back with few or no matches. A fixed page
#: also keeps the number of round-trips proportional to the table rather than to
#: the number of courses still missing.
SCAN_PAGE_SIZE = 100


def _last_checked(row: dict) -> str:
    """When a course row was last looked at; rows never checked sort first."""
    return str(row.get("last_checked") or "")


def _unfingerprinted(table, limit: int) -> list[dict]:
    """The unclassified courses checked longest ago, up to ``limit``.

    Selection is by ``last_checked``, not table order. Every processed row is
    stamped with the run's timestamp, so a course whose site never answers
    sorts behind everything that has not had a turn yet. Taking rows in table
    order instead let one permanently broken row at the head of the table be
    re-probed on every run, and the backlog behind it never advanced.

    That means reading every unclassified row rather than stopping as soon as
    the batch is full; ``Limit`` bounds items read before ``FilterExpression``
    runs, so pages are fixed-size and paging continues to the end of the table.

    Hidden rows are dropped here: an admin hid the course, so it must stop
    taking checks from the courses that are still candidates.
    """
    courses: list[dict] = []
    start_key = None
    while True:
        kwargs = {
            "FilterExpression": "#p = :u",
            "ExpressionAttributeNames": {"#p": "provider"},
            "ExpressionAttributeValues": {":u": "unknown"},
            "Limit": SCAN_PAGE_SIZE,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        try:
            page = table.scan(**kwargs)
        except Exception as error:
            logger.warning("courses scan failed: %s", error)
            break
        courses.extend(
            row for row in page.get("Items", []) if not row.get("hidden")
        )
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break
    courses.sort(key=_last_checked)
    return courses[:limit]


def handler(event: dict | None = None, context=None) -> dict:
    from .config import get_settings
    from .discovery import CourseStore
    from .fingerprint import fingerprint, foreup_ids

    configure_logging()
    event = event or {}
    settings = get_settings()
    limit = int(event.get("limit") or DEFAULT_LIMIT)
    dry_run = bool(event.get("dry_run"))

    store = CourseStore(settings.courses_table_name)
    if store._table is None:
        return {
            "classified": 0,
            "unknown": 0,
            "checked": [],
            "error": "no courses table configured",
        }

    courses = _unfingerprinted(store._table, limit)
    run_id = datetime.datetime.now(datetime.timezone.utc).isoformat()
    classified = 0
    still_unknown = 0
    checked: list[str] = []

    for course in courses:
        name = course.get("name", "?")
        verdict = fingerprint(course)
        checked.append(name)
        course.update(
            {
                "provider": verdict.provider,
                "booking_domain": verdict.booking_domain,
                "evidence": verdict.evidence,
                "last_checked": run_id,
            }
        )
        if verdict.facility_id:
            course["facility_id"] = verdict.facility_id
        if verdict.x_be_alias:
            course["x_be_alias"] = verdict.x_be_alias
        if verdict.provider == "foreup":
            # The ids the adapter needs are in the booking link. Storing them
            # on the row means from_row never has to fetch the page again.
            course.update(foreup_ids(verdict.booking_link))
        if verdict.provider == "unknown":
            still_unknown += 1
        else:
            classified += 1
        # A verdict line per course, with the evidence behind it: the summary
        # count alone says nothing about which course changed or why.
        verdict_line = f"{name} -> {verdict.provider}"
        if verdict.booking_domain:
            verdict_line += f" at {verdict.booking_domain}"
        if verdict.facility_id:
            verdict_line += f" (facility {verdict.facility_id})"
        logger.info(
            "fingerprint %s: %s | %s",
            run_id,
            verdict_line,
            verdict.evidence or "no evidence",
        )
        if not dry_run:
            store.put(course)
            if (
                verdict.provider == "teeitup"
                and verdict.facility_id
                and course.get("lat")
                and course.get("lon")
                and settings.geocode_table_name
            ):
                try:
                    from .geo import Coordinates, GeocodeStore

                    geo_store = GeocodeStore(settings.geocode_table_name)
                    geo_store.store_course(
                        verdict.facility_id,
                        Coordinates(
                            lat=float(course["lat"]),
                            lon=float(course["lon"]),
                            display_name=course.get("display_name", ""),
                            source="fingerprint",
                        ),
                    )
                except Exception as geo_error:
                    logger.warning("failed to cache geocode: %s", geo_error)

    logger.info(
        "fingerprint %s: classified=%d unknown=%d", run_id, classified, still_unknown
    )
    return {
        "classified": classified,
        "unknown": still_unknown,
        "checked": checked,
        "dry_run": dry_run,
        "error": "",
    }
