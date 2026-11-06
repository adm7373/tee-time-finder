# TeeItUp Providers (Abstracted)

This module provides a reusable `TeeItUpProvider` base class for courses using
[TeeItUp Golf](https://teeitup.golf) for booking. The abstraction extracts common
API logic so new courses can be added by subclassing and setting 4 class attributes.

## Base Class

### `TeeItUpProvider` (in `app/providers.py`)

Extend this class and set the following attributes to create a new provider:

| Attribute | Description | Example |
|---|---|---|
| `base_url` | Base URL of the booking site | `https://norwood-country-club.book.teeitup.golf` |
| `api_url` | API endpoint for tee times | `https://phx-api-be-east-1b.kenna.io/v2/tee-times` |
| `facility_id` | Facility ID for the course | `5720` |
| `x_be_alias` | `x-be-alias` HTTP header value | `norwood-country-club` |

The `fetch_availability(request)` method is inherited and handles:
- API request construction with facility ID and current date
- JSON parsing with `x-be-alias` header
- SSL certificate fallback for local development
- Time-window filtering
- Duplicate time deduplication
- Rate limiting (2-second default interval)

## Currently Implemented Providers

### `NorwoodCCProvider`
- **Course**: Norwood Country Club (Norwood, MA)
- **Booking page**: <https://norwood-country-club.book.teeitup.golf>
- **Attributes**: `facility_id="5720"`, `x_be_alias="norwood-country-club"`

### `SquirrelRunProvider`
- **Course**: Squirrel Run Golf Course
- **Booking page**: <https://squirrel-run.book.teeitup.golf>
- **Attributes**: `facility_id="3196"`, `x_be_alias="squirrel-run"`

### `PonkapoagCCProvider`
- **Course**: Ponkapoag Country Club
- **Booking page**: <https://ponkapoag-cc.book.teeitup.golf>
- **Attributes**: `facility_id="4068"`, `x_be_alias="ponkapoag-cc"`

### `BraintreeCountryClubProvider`
- **Course**: Braintree Country Club
- **Booking page**: <https://braintree-cc.book.teeitup.golf>
- **Attributes**: `facility_id="5024"`, `x_be_alias="braintree-cc"`

## Example Template Providers

These were example templates demonstrating the abstraction pattern (not active in code):

| Provider Name | facility_id | x_be_alias | Notes |
|---|---|---|---|
| `MountainViewGCCProvider` | `6821` | `mountain-view-gc` | Mountain View Golf Course (template) |
| `ValleyViewCCProvider` | `9147` | `valley-view-cc` | Valley View Country Club (template) |

## Adding a New Provider

To add a new course using TeeItUp for booking:

```python
from app.providers import TeeItUpProvider

class MyCourseProvider(TeeItUpProvider):
    name = "my-course"
    base_url = "https://my-course.book.teeitup.golf"
    api_url = "https://phx-api-be-east-1b.kenna.io/v2/tee-times"
    facility_id = "12345"  # Course-specific
    x_be_alias = "my-course"  # Header-specific

    # Optional: override min_interval_seconds if needed
    # min_interval_seconds = 5.0
```

Then add `MyCourseProvider()` to the `get_search_providers()` function.

## Provider Structure

All providers follow this inheritance hierarchy:

```
SearchProvider
  └── MockSearchProvider (local development)
  └── SiteBookingProvider
       ├── TeeItUpProvider
       │   └── [Course-specific providers]
       └── SiteBookingProvider (direct)
            ├── GraniteLinksProvider
            └── PresidentsGCProvider
```

## Configuration

TeeItUp credentials are stored in AWS Secrets Manager (see `app/config.py`).
The `get_teeitup_credentials()` method fetches them if needed, but the
`TeeItUpProvider` base class uses public read-only endpoints and doesn't
require authentication.
