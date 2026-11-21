"""Fingerprint Lambda: classify how unfingerprinted courses take bookings.

Reads course rows from the courses table whose provider is still ``unknown``,
fingerprints a bounded batch of them, and writes the verdicts back. The bound
keeps a scheduled run inside Lambda's time limit; the schedule re-runs often
enough that the backlog drains over a few nights.

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


def _unfingerprinted(table, limit: int) -> list[dict]:
    """Collect up to ``limit`` unclassified courses, paging until the batch is full.

    ``Limit`` bounds the items a scan reads, before ``FilterExpression`` is
    applied, so a page can contribute fewer matches than its size — sometimes
    none. Paging therefore continues while items are left and the batch is not
    full, and the page size stays fixed so the number of round-trips does not
    grow as the batch fills.
    """
    courses: list[dict] = []
    start_key = None
    while len(courses) < limit:
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
        courses.extend(page.get("Items", []))
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break
    return courses[:limit]


def handler(event: dict | None = None, context=None) -> dict:
    from .config import get_settings
    from .discovery import CourseStore
    from .fingerprint import fingerprint

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
