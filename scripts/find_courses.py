"""Discover TeeItUp courses near a ZIP and emit provider code.

Adding a course by hand means answering three questions: what is the booking
alias, what is its facility ID, and where is it? This script answers all three:

    python -m scripts.find_courses --near 02169 --radius 25
    python -m scripts.find_courses --near 02169 --radius 25 --aliases aliases.txt

Nominatim supplies the golf courses and their coordinates. The booking alias is
guessed from the course name, then the booking page's own payload is read for
``gnFacilityIds`` — the only place TeeItUp publishes a facility ID. A candidate
is only reported once the availability API confirms that ID resolves to a
distinct course.

Courses not on TeeItUp are reported as MISSING rather than dropped, because
"the nearest course to me is not bookable here" is worth knowing.
"""

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

import certifi

from app.geo import Coordinates, Geocoder, distance_miles
from app.providers import get_search_providers

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


def _get(url: str, accept: str = "application/json") -> tuple[int, str]:
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
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"  network error: {error}", file=sys.stderr)
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

    # Drop leading "the", and trailing words that are often omitted.
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


def facility_id_for(alias: str) -> str | None:
    """Read gnFacilityIds out of the booking page, or None if it does not exist."""
    status, body = _get(BOOKING_URL.format(alias=alias), accept="text/html")
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


def confirm(alias: str, facility_id: str, date: str) -> str | None:
    """Return the courseId if this alias/facility pair resolves, else None."""
    query = urllib.parse.urlencode(
        {"date": date, "facilityIds": facility_id, "returnPromotedRates": "true"}
    )
    status, body = _get(f"{API_URL}?{query}")
    if status != 200 or not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not payload:
        return None
    return payload[0].get("courseId")


@dataclass
class Found:
    name: str
    alias: str
    facility_id: str
    course_id: str
    coords: Coordinates
    distance: float


def class_name(name: str) -> str:
    """A provider class name that reads well: 'Rockland GC' -> RocklandGCProvider."""
    words = re.sub(r"[^A-Za-z0-9 ]", "", name).split()
    return "".join(words) + "Provider"


def emit(found: Found) -> str:
    return (
        f"class {class_name(found.name)}(TeeItUpProvider):\n"
        f'    """Provider for {found.name}."""\n'
        f"\n"
        f'    facility_id = "{found.facility_id}"\n'
        f'    display_name = "{found.name}"\n'
        f'    x_be_alias = "{found.alias}"\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--near", default="02169", help="ZIP code to search around")
    parser.add_argument("--radius", type=float, default=25, help="miles")
    parser.add_argument(
        "--date",
        default=None,
        help="date to confirm availability against (default: today)",
    )
    parser.add_argument(
        "--aliases",
        help="file of 'Course Name = alias' lines for courses whose slug we cannot guess",
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        help="print provider classes and geocode entries instead of a report",
    )
    args = parser.parse_args(argv)

    import datetime

    date = args.date or datetime.date.today().isoformat()

    known_aliases: dict[str, str] = {}
    if args.aliases:
        with open(args.aliases, encoding="utf-8") as handle:
            for line in handle:
                if "=" in line and not line.lstrip().startswith("#"):
                    name, alias = line.split("=", 1)
                    known_aliases[name.strip().lower()] = alias.strip()

    geocoder = Geocoder("tee-time-finder/0.1 (personal tool)")
    origin = geocoder.by_zip(args.near)
    if origin is None:
        print(f"Could not geocode ZIP {args.near}", file=sys.stderr)
        return 1

    print(
        f"Searching within {args.radius:g} miles of {args.near} ({origin.display_name[:50]})"
    )
    courses = geocoder.golf_courses_near(origin, radius_miles=args.radius)
    if not courses:
        print("Nominatim returned no golf courses in that area", file=sys.stderr)
        return 1

    # Nominatim returns a course as both a node and a way; keep the nearest of
    # each name so we do not probe the same alias twice.
    deduped: dict[str, tuple[str, Coordinates]] = {}
    for name, coords in courses:
        if name not in deduped or distance_miles(origin, coords) < distance_miles(
            origin, deduped[name][1]
        ):
            deduped[name] = (name, coords)
    courses = list(deduped.values())

    existing_ids = {p.facility_id for p in get_search_providers()}
    found: list[Found] = []
    missing: list[tuple[str, float]] = []
    already: list[str] = []

    for name, coords in courses:
        distance = distance_miles(origin, coords)
        hint = known_aliases.get(name.lower())
        aliases = [hint] if hint else slug_candidates(name)

        hit: Found | None = None
        for alias in aliases:
            facility_id = facility_id_for(alias)
            if not facility_id:
                continue
            if facility_id in existing_ids:
                already.append(f"{name} ({alias} = {facility_id})")
                break
            course_id = confirm(alias, facility_id, date)
            if course_id:
                hit = Found(name, alias, facility_id, course_id, coords, distance)
                break
        if hit:
            found.append(hit)
        elif not any(name == a.split(" (")[0] for a in already):
            missing.append((name, distance))

    found.sort(key=lambda f: f.distance)

    if args.emit:
        for item in found:
            print(emit(item))
            print()
        print("# geocode_courses.py")
        for item in found:
            print(f'    "{item.facility_id}": ["{item.name}, MA"],')
        return 0

    print(f"\n{len(found)} new courses on TeeItUp:\n")
    for item in found:
        print(
            f"  {item.distance:5.1f} mi  {item.name:36} "
            f"{item.alias:36} {item.facility_id}"
        )
    if already:
        print(f"\n{len(already)} already configured:")
        for line in already:
            print(f"  {line}")
    if missing:
        print(f"\n{len(missing)} not on TeeItUp (no booking engine found):")
        for name, distance in sorted(missing, key=lambda m: m[1]):
            print(f"  {distance:5.1f} mi  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
