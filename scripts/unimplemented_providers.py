"""Report course labels that discovery recorded but no provider implements.

Discovery and the fingerprint worker write a ``provider`` label on every course
row. Search only works for labels registered in ``ENGINE_PROVIDERS``; every
other row is a course the app knows about and cannot search. This prints that
gap, split into rows whose engine is worth writing an adapter for and rows that
are not classified yet.

    python -m scripts.unimplemented_providers
    python -m scripts.unimplemented_providers --table tee-time-finder-courses
"""

import argparse

from app.config import get_settings
from app.discovery import CLASSIFIED_PROVIDERS, CourseStore
from app.providers import ENGINE_PROVIDERS, provider_coverage


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table",
        default=None,
        help="Courses table; defaults to COURSES_TABLE_NAME from the environment.",
    )
    args = parser.parse_args(argv)

    table = args.table or get_settings().courses_table_name
    if not table:
        print("No courses table configured. Pass --table or set COURSES_TABLE_NAME.")
        return 1

    rows = [
        row
        for row in CourseStore(table).scan()
        if str(row.get("pk", "")).startswith("course#")
    ]
    implemented = ", ".join(sorted(ENGINE_PROVIDERS))
    print(f"{len(rows)} course rows; engine adapters: {implemented}")

    missing = provider_coverage(rows)
    if not missing:
        print("every course row is covered by a provider")
        return 0

    classified = {k: v for k, v in missing.items() if k in CLASSIFIED_PROVIDERS}
    unclassified = {k: v for k, v in missing.items() if k not in CLASSIFIED_PROVIDERS}

    if classified:
        print("\nclassified, but no provider adapter (an adapter would unlock these):")
        for label, count in sorted(classified.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4}  {label}")

    if unclassified:
        total = sum(unclassified.values())
        print(
            f"\nnot classified yet ({total} rows): fingerprint work, not adapter work:"
        )
        for label, count in sorted(unclassified.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4}  {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
