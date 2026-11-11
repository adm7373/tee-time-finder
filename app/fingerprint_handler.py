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

logger = logging.getLogger(__name__)

#: Courses fingerprinted per run. Each course costs up to a dozen slow page
#: fetches, so this stays small; the schedule provides the repetition.
DEFAULT_LIMIT = 5


def _unfingerprinted(table, limit: int) -> list[dict]:
    courses: list[dict] = []
    start_key = None
    while len(courses) < limit:
        kwargs = {
            "FilterExpression": "#p = :u",
            "ExpressionAttributeNames": {"#p": "provider"},
            "ExpressionAttributeValues": {":u": "unknown"},
            "Limit": limit - len(courses),
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
    return courses


def handler(event: dict | None = None, context=None) -> dict:
    from .config import get_settings
    from .discovery import CourseStore
    from .fingerprint import fingerprint

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
        if verdict.provider == "unknown":
            still_unknown += 1
        else:
            classified += 1
        if not dry_run:
            store.put(course)

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
