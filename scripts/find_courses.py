"""Thin CLI over the shared discovery logic.

The alias guessing, TeeItUp probing and geocoding live in ``app.teeitup_probe``
and ``app.geo`` so the discovery Lambda reuses them. This script is the
same sweep with prints instead of DynamoDB writes — useful for checking what a
run would do before enabling the schedule, or for one-off searches from a
laptop.
"""

import argparse
import sys

from app.discovery import CourseStore, discover
from app.geo import CoursePlace, GeocodeStore, Geocoder


class _PrintStore(CourseStore):
    def __init__(self):
        super().__init__(None)
        self.rows: dict[str, dict] = {}

    def get(self, name: str):
        return self.rows.get(name)

    def put(self, item: dict):
        self.rows[item["name"]] = item

    def scan(self):
        return list(self.rows.values())

    def meta(self, key: str):
        # No sweep history in a dry run, so every invocation looks like a first
        # run and the sweep-vs-backlog behaviour is visible.
        return None

    def put_meta(self, key: str, value: str):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--near", default="02169")
    parser.add_argument("--radius", type=float, default=25)
    parser.add_argument("--aliases", default=None)
    parser.add_argument("--emit", action="store_true")
    args = parser.parse_args(argv)

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

    # Every facility the app already searches, plus any --aliases hints: a
    # course pointing at one of these is reported as already configured rather
    # than as a discovery. Without this the report claimed all 35 were new.
    from app.providers import get_search_providers
    from app.teeitup_probe import facility_id_for

    configured = {p.facility_id for p in get_search_providers()}
    configured.update(
        facility_id
        for alias in known_aliases.values()
        if (facility_id := facility_id_for(alias))
    )

    print(f"Searching within {args.radius:g} miles of {args.near}")
    store = _PrintStore()
    result = discover(
        args.near,
        args.radius,
        store=store,
        geocode_store=GeocodeStore(None, table=None),
        geocoder=geocoder,
        configured_facility_ids=configured,
    )
    if args.emit:
        from app.teeitup_probe import facility_id_for as _f  # noqa: F401

        for name, row in sorted(store.rows.items()):
            if row.get("provider") == "teeitup-confirmed":
                print(f'# {name}: facility {row["facility_id"]}')
        return 0

    new = [n for n, r in store.rows.items() if r["provider"] == "teeitup-confirmed"]
    configured = [n for n, r in store.rows.items() if r["provider"] == "teeitup"]
    print(f"\n{len(new)} new courses on TeeItUp:\n")
    for name in sorted(new):
        row = store.rows[name]
        print(f"  {row['distance_from_zip']:5.1f} mi  {name:36} {row['facility_id']}")
    if configured:
        print(f"\n{len(configured)} already configured (a facility the app searches):")
        for name in sorted(configured):
            row = store.rows[name]
            print(f"  {row['distance_from_zip']:5.1f} mi  {name:36} {row['facility_id']}")
    missing = [n for n, r in store.rows.items() if r["provider"] == "unknown"]
    if missing:
        print(f"\n{len(missing)} not on TeeItUp (no booking engine found):")
        for name in sorted(missing, key=lambda n: store.rows[n]["distance_from_zip"]):
            print(f"  {store.rows[name]['distance_from_zip']:5.1f} mi  {name}")
    if result.error:
        print(f"\nerror: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
