"""Seed definitions and loader for existing TeeItUp courses.

Moves the details of configured courses into DynamoDB (both the courses table
and the geocode table) so they are discoverable and searchable dynamically.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .geo import Coordinates, distance_miles

if TYPE_CHECKING:
    from .discovery import CourseStore
    from .geo import GeocodeStore

DEFAULT_SEARCH_COORDINATES = Coordinates(
    lat=42.2461701, lon=-71.0057746, display_name="02169, Quincy, MA"
)

# Canonical details for all 18 existing TeeItUp courses.
EXISTING_COURSES: list[dict] = [
    {
        "facility_id": "17943",
        "x_be_alias": "presidents-golf-course",
        "name": "Presidents Golf Course",
        "lat": 42.2699707,
        "lon": -71.0424722,
        "display_name": "Presidents Golf Course, 357, Squantum Street, East Milton, Quincy, Norfolk County, Massachusetts, 02171, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "16026",
        "x_be_alias": "braintree-municipal-golf-course",
        "name": "Braintree Municipal Golf Course",
        "lat": 42.1925351,
        "lon": -71.014143,
        "display_name": "Braintree Municipal Golf Course, 101, Jefferson Street, South Braintree, Braintree, Norfolk County, Massachusetts, 02184, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "17032",
        "x_be_alias": "devine-golf-course",
        "name": "William J. Devine Golf Course",
        "lat": 42.3006446,
        "lon": -71.0929277,
        "display_name": "William J. Devine Golf Course, 1, Circuit Drive, Roxbury, Boston, Suffolk County, Massachusetts, 02121, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "17927",
        "x_be_alias": "ponkapoag-golf-course",
        "name": "Ponkapoag Golf Course",
        "lat": 42.1860978,
        "lon": -71.1136921,
        "display_name": "Ponkapoag Golf Course, Ponkapoag, Canton, Norfolk County, Massachusetts, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "11063",
        "x_be_alias": "rockland-golf-course",
        "name": "Rockland Golf Course",
        "lat": 42.1234669,
        "lon": -70.92893,
        "display_name": "Rockland Golf Course, 276, Plain Street, Rockland, Plymouth County, Massachusetts, 02370, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "5720",
        "x_be_alias": "norwood-country-club",
        "name": "Norwood Country Club",
        "lat": 42.1986317,
        "lon": -71.1820973,
        "display_name": "Norwood Country Club, 400, Providence Highway, Ellis, Islington, Norwood, Norfolk County, Massachusetts, 02062, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "12116",
        "x_be_alias": "cedarhill-golf",
        "name": "Cedar Hill Golf Course",
        "lat": 42.1061845,
        "lon": -71.0762816,
        "display_name": "Cedar Hill Golf Course, 1137, Park Street, South Stoughton, Stoughton, Norfolk County, Massachusetts, 02072, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "5719",
        "x_be_alias": "newton-commonwealth-golf-course",
        "name": "Newton Commonwealth Golf Course",
        "lat": 42.3433797,
        "lon": -71.1738982,
        "display_name": "Newton Commonwealth Golf Course, 212, Kenrick Street, Newton Corner, Newton, Middlesex County, Massachusetts, 02458, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "9146",
        "x_be_alias": "green-harbor-golf-club",
        "name": "Green Harbor Golf Club",
        "lat": 42.0863715,
        "lon": -70.6887824,
        "display_name": "Green Harbor Golf Club, 624, Webster Street, Marshfield, Plymouth County, Massachusetts, 02050, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "4557",
        "x_be_alias": "wentworth-hills-country-club",
        "name": "Wentworth Hills Country Club",
        "lat": 42.0155202,
        "lon": -71.3789492,
        "display_name": "Wentworth Hills Country Club, 27, Bow Street, Plainville, Norfolk County, Massachusetts, 02762, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "4328",
        "x_be_alias": "squirrel-run-golf-club",
        "name": "Squirrel Run Golf Course",
        "lat": 41.932114,
        "lon": -70.722723,
        "display_name": "Squirrel Run Golf Course, 32 Elderberry Dr, Plymouth, MA 02360",
        "source": "manual",
    },
    {
        "facility_id": "6696",
        "x_be_alias": "lakeville-country-club",
        "name": "Lakeville Country Club",
        "lat": 41.8666646,
        "lon": -70.9452899,
        "display_name": "Lakeville Country Club, 44, Clear Pond Road, Upper Four Corners, Lakeville, Plymouth County, Massachusetts, 02347, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "4329",
        "x_be_alias": "village-links-golf-club",
        "name": "Village Links Golf Club",
        "lat": 41.910884,
        "lon": -70.7359079,
        "display_name": "Village Links Golf Club, 265 S Meadow Rd, Plymouth, MA 02360",
        "source": "manual",
    },
    {
        "facility_id": "15487",
        "x_be_alias": "stone-e-lea",
        "name": "Stone-E-Lea Golf Course",
        "lat": 41.9155987,
        "lon": -71.3278534,
        "display_name": "Stone-E-Lea Golf Course, 1411, County Street, Dodgeville, Hebronville, Attleboro, Bristol County, Massachusetts, 02703, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "10968",
        "x_be_alias": "plymouth-country-club",
        "name": "Plymouth Country Club",
        "lat": 41.9338808,
        "lon": -70.6078753,
        "display_name": "Plymouth Country Club, 221, Warren Avenue, Beach Park, Plymouth, Plymouth County, Massachusetts, 02360, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "4839",
        "x_be_alias": "crosswinds-golf-club",
        "name": "Crosswinds Golf Club",
        "lat": 41.8956095,
        "lon": -70.6252097,
        "display_name": "Crosswinds Golf Club, 424, Long Pond Road, South Pond, Plymouth, Plymouth County, Massachusetts, 02360, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "6345",
        "x_be_alias": "waverly-oaks-golf-club",
        "name": "Waverly Oaks Golf Club",
        "lat": 41.8903802,
        "lon": -70.6184584,
        "display_name": "Waverly Oaks Golf Club, 444, Long Pond Road, South Pond, Plymouth, Plymouth County, Massachusetts, 02360, United States",
        "source": "nominatim",
    },
    {
        "facility_id": "4981",
        "x_be_alias": "pinehills-golf-club",
        "name": "Pinehills Golf Club",
        "lat": 41.8900218,
        "lon": -70.5918643,
        "display_name": "Pinehills Golf Club, 54, Clubhouse Drive, Plymouth, Plymouth County, Massachusetts, 02360, United States",
        "source": "nominatim",
    },
]


def build_course_row(course_data: dict, origin: Coordinates = DEFAULT_SEARCH_COORDINATES) -> dict:
    """Format a course dictionary into the DynamoDB course record format."""
    from .discovery import CourseStore

    coords = Coordinates(
        lat=float(course_data["lat"]),
        lon=float(course_data["lon"]),
        display_name=course_data.get("display_name", ""),
        source=course_data.get("source", "seed"),
    )
    dist = round(distance_miles(origin, coords), 1)
    name = course_data["name"]
    alias = course_data["x_be_alias"]

    return {
        "pk": f"course#{CourseStore.slug(name)}",
        "name": name,
        "display_name": course_data.get("display_name") or name,
        "facility_id": course_data["facility_id"],
        "x_be_alias": alias,
        "booking_domain": f"{alias}.book.teeitup.golf",
        "provider": "teeitup",
        "lat": str(course_data["lat"]),
        "lon": str(course_data["lon"]),
        "distance_from_zip": dist,
        "zip_code": "02169",
        "discovery_attempts": 0,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


def seed_courses(
    course_store: "CourseStore",
    geocode_store: "GeocodeStore | None" = None,
    origin: Coordinates = DEFAULT_SEARCH_COORDINATES,
) -> list[dict]:
    """Write all 18 existing courses to CourseStore and GeocodeStore."""
    rows: list[dict] = []
    for data in EXISTING_COURSES:
        row = build_course_row(data, origin)
        course_store.put(row)
        rows.append(row)

        if geocode_store is not None:
            geocode_store.store_course(
                data["facility_id"],
                Coordinates(
                    lat=float(data["lat"]),
                    lon=float(data["lon"]),
                    display_name=data.get("display_name", ""),
                    source=data.get("source", "seed"),
                ),
            )
    return rows
