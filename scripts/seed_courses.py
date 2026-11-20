"""Seed existing TeeItUp courses into DynamoDB.

Writes the 18 pre-configured courses into the courses table (for search and
discovery) and into the geocode table (for coordinates caching):

    python -m scripts.seed_courses --dry-run
    python -m scripts.seed_courses
"""

import argparse

from app.config import get_settings
from app.discovery import CourseStore
from app.geo import GeocodeStore
from app.seed_data import EXISTING_COURSES, build_course_row, seed_courses


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--courses-table",
        default=settings.courses_table_name,
        help="DynamoDB courses table name",
    )
    parser.add_argument(
        "--geocode-table",
        default=settings.geocode_table_name,
        help="DynamoDB geocode table name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print courses without writing to DynamoDB",
    )
    args = parser.parse_args(argv)

    if not args.dry_run and not args.courses_table:
        parser.error(
            "no courses table configured: set COURSES_TABLE_NAME or pass --courses-table"
        )

    print(f"Preparing to seed {len(EXISTING_COURSES)} TeeItUp courses...")

    if args.dry_run:
        for data in EXISTING_COURSES:
            row = build_course_row(data)
            print(
                f"DRY-RUN  {row['facility_id']:>6}  {row['name']:<32}  "
                f"{row['x_be_alias']:<32}  {row['distance_from_zip']} mi"
            )
        print(f"\nDry run complete ({len(EXISTING_COURSES)} courses).")
        return 0

    course_store = CourseStore(args.courses_table)
    geocode_store = GeocodeStore(args.geocode_table) if args.geocode_table else None

    seeded = seed_courses(course_store, geocode_store)
    for row in seeded:
        print(
            f"SEEDED   {row['facility_id']:>6}  {row['name']:<32}  "
            f"{row['x_be_alias']:<32}  {row['distance_from_zip']} mi"
        )

    print(
        f"\nSuccessfully seeded {len(seeded)} courses into "
        f"{args.courses_table}"
        + (f" and {args.geocode_table}" if args.geocode_table else "")
        + "."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
