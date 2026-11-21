# Adding a booking-engine provider

Search reaches a course through an **engine adapter**: a small class that knows
one booking engine's API. A course is a row in `tee-time-finder-courses`, not a
class, so a newly discovered course becomes searchable as soon as its label has
an adapter.

## 1. Find what is missing

From the repo root, with AWS credentials for the account:

    python -m scripts.unimplemented_providers

It lists every course row whose `provider` label has no entry in
`ENGINE_PROVIDERS` (`app/providers.py`), split into:

- **classified, but no provider adapter** — a real engine (foreup, chronogolf,
  teeitup, ...). An adapter unlocks those courses now.
- **not classified yet** (`unknown`) — the fingerprint worker has not worked out
  where the course books. That is fingerprint work, not adapter work.
- **terminal labels** (`golfnow`, `members-only`) — counted on their own line,
  never as gaps: GolfNow has no queryable tee sheet, and `members-only` means a
  club portal. No adapter can serve either.

Take the label with the most rows unless there is a reason to prefer another.

## 2. Learn the engine's API before writing code

Read that label's rows: `booking_domain`, `website`, `evidence`, `facility_id`.
The fingerprint's `evidence` records the booking URL it followed, and the ids
usually sit in the path (`.../booking/19865/2748`).

Fetch one course's booking page and look for the JSON endpoint. Then make one
live request and inspect the shape. Two traps:

- **Empty is ambiguous in winter.** New England courses often have no inventory
  from November. Confirm the endpoint answers and the payload parses against a
  course with live slots (facility `17943` is a reliable control) before
  concluding the ids are wrong.
- **An alias is not a facility id.** TeeItUp needs both; another engine may want
  a course id, a schedule id, or a tenant slug instead.

## 3. Write one adapter

Subclass `BookingEngineProvider` in `app/providers.py` and implement only:

| | |
|---|---|
| `label` | the row label it answers to |
| `from_row(cls, row)` | pull the ids the API needs; return `None` when the row cannot supply them |
| `availability_url(self, request)` | the URL for one date |
| `parse_slots(self, payload, request)` | `{date, start_time, course_name, players}` dicts, filtered by local date, time window and party size |
| `booking_url(self)`, `request_headers(self)` | optional: the page a person books on, and any engine headers |

Pacing, TLS, JSON fetching, logging and `TeeTimeOption` building come from the
base. Do not add a subclass per course.

Register it in the same file:

    ENGINE_PROVIDERS: dict[str, type[BookingEngineProvider]] = {
        "teeitup": TeeItUpProvider,
        "teeitup-confirmed": TeeItUpProvider,
        "foreup": ForeUpProvider,
    }

## 4. Persist the ids the adapter needs

If the ids come from a booking URL, have the fingerprint worker write them onto
the row (for foreup: `foreup_course_id`, `foreup_schedule_id`) so `from_row`
needs no network call. Row fields are the contract between discovery,
fingerprinting and search: an adapter that fetches at construction time adds a
page load to every search.

## 5. Test without the network

- Fake `_fetch_json`; never hit a course site from a test.
- Cover `from_row` with a complete row and an incomplete one, the slot filters
  (local date, time window, party size), and one end-to-end build through
  `ENGINE_PROVIDERS`.
- `tests/test_providers.py::test_a_new_engine_needs_only_its_own_four_methods`
  is the template: it registers a fake engine and searches it.

## 6. Check

    venv/bin/python -m pytest
    venv/bin/python -m flake8 --max-line-length=100 --extend-ignore=E501,W503,F401 app/providers.py tests/
    python -m scripts.unimplemented_providers    # the label leaves the first list

## Conventions

- Respect each site's terms, robots policy and pacing; set `min_interval_seconds`
  for the engine. Never automate a login or store a session cookie.
- Never invent a facility id, alias, course id or schedule id: take it from the
  row or from the booking page.
- There are no per-course classes: every course is a row, and its label picks
  the adapter that builds it.

## Known engines

### teeitup — implemented

- API: `https://phx-api-be-east-1b.kenna.io/v2/tee-times?date=YYYY-MM-DD&facilityIds=<facility>&returnPromotedRates=true`
- Header `x-be-alias: <alias>`; `TeeItUpProvider` paces requests two seconds apart.
- Slots carry UTC instants and `rates[].allowedPlayers`.

### foreup — from_row done, parse_slots pending

- Discovered from a course site's booking link, e.g. Robert T. Lynch Municipal
  Golf Course: `booking_domain: foreupsoftware.com`, evidence
  `https://foreupsoftware.com/index.php/booking/19865/2748#/teetimes`.
- Booking page: `/index.php/booking/<course_id>/<schedule_id>`
- JSON API: `/index.php/api/booking/times`
- A request of the form
  `?time=all&date=11-27-2026&holes=all&players=2&schedule_id=2748&booking_class=default`
  answered `200` with a JSON array — `[]` for that course in late November.
- The fingerprint worker records the booking link it followed
  (`Fingerprint.booking_link`) and, for foreup, stores `foreup_course_id` and
  `foreup_schedule_id` on the row (`foreup_ids`). `ForeUpProvider.from_row`
  therefore reads the row and never fetches the booking page.
- Still unverified: whether `booking_class` or `api_key` are required, and the
  slot shape. `ForeUpProvider.parse_slots` raises until that is settled, which
  is why the class is not yet in `ENGINE_PROVIDERS`.
- The schedule id is mandatory and cannot be replaced by the course id: for
  course `19865`, `schedule_id=2748` answers `[]` while `schedule_id=19865`
  answers `false`, and no `schedule_id` or a `course_id` parameter is an error.
  Ould Newbury's link (`/booking/19969`) names only the course, and neither the
  page nor an obvious API call resolves the schedule — the page renders it from
  a template. Such a row keeps `foreup_course_id` and builds no provider until
  the schedule is learned; `from_row` requires both ids.
- Rows classified before this landed predate the ids. Robert T. Lynch is
  backfilled (`19865`/`2748`); any other `foreup` row needs the fields written
  on it, or `provider` reset to `unknown` so the fingerprint worker redoes it.
