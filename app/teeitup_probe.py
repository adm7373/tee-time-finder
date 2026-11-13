"""Shared discovery primitives: alias guessing and TeeItUp page probing.

Both the ``find_courses`` CLI and the discovery Lambda answer the same three
questions about a course — what is the booking alias, what is its facility ID,
and is the pairing real? — so the logic lives here, once, with no boto3 or
Lambda dependency. The CLI wraps it with prints; the Lambda wraps it with
DynamoDB writes.
"""

import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

import certifi

#: TeeItUp serves every booking engine from <alias>.book.teeitup.golf.
BOOKING_URL = "https://{alias}.book.teeitup.golf/"

#: An unknown alias answers with this page instead of a booking engine.
MISSING_MARKER = "Booking Engine Not Found"

API_URL = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"

#: Be a good citizen: one request per second to each host, as Nominatim asks and
#: TeeItUp tolerates.
REQUEST_INTERVAL_SECONDS = 1.0

_last_request = 0.0


@lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def paced_get(url: str, accept: str = "application/json") -> tuple[int, str]:
    """GET a URL, pacing requests. Returns (status, body); status 0 on error."""
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < REQUEST_INTERVAL_SECONDS:
        time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request = time.monotonic()

    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "tee-time-finder/0.1 (personal tool)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=_tls_context()) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, TimeoutError):
        return 0, ""


def slug_candidates(name: str) -> list[str]:
    """Booking aliases worth trying for a course name.

    TeeItUp aliases are usually the slugified name, but not always
    ("devine-golf-course" for William J. Devine, "cedarhill-golf" for Cedar
    Hill), so try the shapes seen in practice before giving up.
    """
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    words = base.split()
    joined = "-".join(words)
    candidates = [joined]

    # Trailing words that are often omitted from the alias.
    for suffix in ("-golf-course", "-golf-club", "-country-club", "-golf-links"):
        if joined.endswith(suffix):
            candidates.append(joined[: -len(suffix)])

    # "william j devine golf course" -> "devine-golf-course"
    if len(words) > 3:
        candidates.append("-".join(words[-3:]))
    # "cedar hill golf course" -> "cedarhill-golf" (seen in practice)
    if len(words) >= 2:
        candidates.append("".join(words[:2]) + "-golf")

    seen: set[str] = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def class_name_for(name: str) -> str:
    """A provider class name that reads well: 'Rockland GC' -> RocklandGCProvider."""
    words = re.sub(r"[^A-Za-z0-9 ]", "", name).split()
    return "".join(words) + "Provider"


def facility_id_for(alias: str) -> str | None:
    """Read gnFacilityIds out of the booking page, or None if it does not exist."""
    status, body = paced_get(BOOKING_URL.format(alias=alias), accept="text/html")
    if status != 200 or MISSING_MARKER in body:
        return None
    # The SPA ships its config as escaped RSC payload chunks.
    pushes = "".join(
        re.findall(r'self\.__next_f\.push\(\[1,"(.{0,60000}?)"\]\)', body, re.S)
    )
    match = re.search(
        r'gnFacilityIds\\?"?:\s*\\?"?\[([\d,\s]*)\]', pushes.replace('\\"', '"')
    )
    if not match:
        return None
    ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
    return ids[0] if ids else None


def confirm_course(alias: str, facility_id: str, date: str) -> str | None:
    """Return the courseId if this alias/facility pair resolves, else None."""
    query = urllib.parse.urlencode(
        {"date": date, "facilityIds": facility_id, "returnPromotedRates": "true"}
    )
    status, body = paced_get(f"{API_URL}?{query}")
    if status != 200 or not body:
        return None
    try:
        import json

        payload = json.loads(body)
    except ValueError:
        return None
    if not payload:
        return None
    return payload[0].get("courseId")
