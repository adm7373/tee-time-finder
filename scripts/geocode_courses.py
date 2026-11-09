"""Seed course coordinates into the geocode table.

Run once after `terraform apply`, and again whenever a course is added::

    python -m scripts.geocode_courses --dry-run     # show what would be stored
    python -m scripts.geocode_courses               # write to DynamoDB

Coordinates come from MANUAL_COORDINATES where a course is not in OpenStreetMap,
otherwise from Nominatim, respecting its one-request-per-second policy. Anything
still unresolved is reported rather than guessed; --set overrides either source
and marks the row as manually placed::

    python -m scripts.geocode_courses --set 4328=41.9321,-70.7227

Requires AWS credentials that can PutItem on the geocode table, and
GEOCODE_TABLE_NAME (or --table) naming it.
"""

import argparse
import sys

from app.config import get_settings
from app.geo import Coordinates, GeocodeStore, Geocoder
from app.providers import get_search_providers

#: Courses OpenStreetMap does not have, placed by street address instead. Both
#: are in Plymouth, MA, 1.6 miles apart; neither address is tagged as a golf
#: course in OSM, so these are the street coordinates rather than a course node.
MANUAL_COORDINATES = {
    # 32 Elderberry Dr, Plymouth, MA 02360
    "4328": Coordinates(lat=41.932114, lon=-70.722723, source="manual"),
    # 265 S Meadow Rd, Plymouth, MA 02360
    "4329": Coordinates(lat=41.910884, lon=-70.7359079, source="manual"),
}

#: Searches tried in order for each course. Nominatim misses some small courses
#: by name alone, so each entry ends with the town it is known to be in.
COURSE_QUERIES = {
    "5720": ["Norwood Country Club, Norwood, MA"],
    "4328": ["Squirrel Run Golf Course, Plymouth, MA", "Squirrel Run, Plymouth, MA"],
    "4329": ["Village Links Golf Club, Plymouth, MA", "Village Links, Plymouth, MA"],
    "17927": ["Ponkapoag Golf Course, Canton, MA"],
    "16026": ["Braintree Municipal Golf Course, Braintree, MA"],
    "17943": ["Presidents Golf Course, Quincy, MA"],
}


def _parse_point(value: str) -> Coordinates:
    try:
        lat, lon = (float(part) for part in value.split(",", 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected LAT,LON, got {value!r}")
    return Coordinates(lat=lat, lon=lon, source="manual")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default=settings.geocode_table_name)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FACILITY_ID=LAT,LON",
        help="place a course by hand; may be repeated",
    )
    args = parser.parse_args(argv)

    if not args.table and not args.dry_run:
        parser.error("no table configured: set GEOCODE_TABLE_NAME or pass --table")

    overrides: dict[str, Coordinates] = {}
    for item in args.set:
        try:
            facility_id, point = item.split("=", 1)
        except ValueError:
            parser.error(f"expected FACILITY_ID=LAT,LON, got {item!r}")
        overrides[facility_id] = _parse_point(point)

    store = GeocodeStore(args.table)
    geocoder = Geocoder(settings.nominatim_user_agent)

    missing: list[str] = []
    for provider in get_search_providers():
        facility_id = provider.facility_id
        label = provider.display_name or provider.name

        coords = overrides.get(facility_id) or MANUAL_COORDINATES.get(facility_id)
        if coords is None:
            for query in COURSE_QUERIES.get(facility_id, [label]):
                coords = geocoder.by_query(query)
                if coords is not None:
                    break

        if coords is None:
            missing.append(f"{facility_id} ({label})")
            print(f"MISS   {facility_id:>6}  {label}")
            continue

        place = coords.display_name or f"{coords.lat},{coords.lon}"
        print(f"OK     {facility_id:>6}  {label}  [{coords.source}]  {place[:70]}")
        if not args.dry_run:
            store.store_course(facility_id, coords)

    if missing:
        print(
            "\nCould not place:\n  "
            + "\n  ".join(missing)
            + "\nRe-run with --set FACILITY_ID=LAT,LON for each.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
