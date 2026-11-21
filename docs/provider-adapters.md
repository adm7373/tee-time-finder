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
- **terminal labels** (`golfnow`, `members-only`, `phone-only`) — counted on
  their own line, never as gaps: GolfNow has no queryable tee sheet,
  `members-only` means a club portal, and `phone-only` means online booking is
  switched off at the course. No adapter can serve any of them.

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
        "chronogolf": ChronogolfProvider,
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

### cps-golf — shape verified, transport blocked (not registered)

CPS Golf (`cps.golf`) powers club booking for at least four rows: Meadow at
Peabody (`peabody.cps.golf`), Gannon Municipal (`gannon.cps.golf`), Beverly Golf
& Tennis (`beverlygolf.cps.golf`) and D. W. Field (`dwfields.cps.golf`). Each
club site links its tenant, and the tenant subdomain is what identifies the
booking engine.

The API, read from a live tee sheet (Chattanooga, `2026-11-21`, the only
cps.golf tenant with November inventory reachable for this work):

- `GET https://<tenant>.cps.golf/onlineres/onlineapi/api/v1/onlinereservation/TeeTimes`
- Query: `searchDate=Sat%20Nov%2021%202026`, `holes=0`, `numberOfPlayer=0`,
  `courseIds=1%2C2`, `searchTimeType=0`, `teeSheetSearchView=5`,
  `classCode=R`, `defaultOnlineRate=N`, `isUseCapacityPricing=false`,
  `memberStoreId=1`, `searchType=1`.
- Headers: `client-id: onlineresweb`, plus `x-websiteid`,
  `x-componentid: 1`, `x-productid: 1`, `x-ismobile: false`; older tenants
  also require `x-apiKey` (published in their `onlineresweb/Home/Configuration`).
  Newer tenants require a short-lived bearer token from
  `identityapi/myconnect/token/short`.
- Response: a bare JSON **array** of tee times in course-local time with no
  offset — `startTime: "2026-11-21T14:24:00"`, `courseName` (sometimes padded),
  `courseId`, `holes`, `availableParticipantNo: [1,2,3,4]`, `minPlayer`,
  `maxPlayer`, `shItemPrices`.
- A day with no inventory answers an **object**:
  `{"isSuccess": true, "content": {"messageKey": "NO_TEETIMES", ...}}`.

**Why there is no adapter in `ENGINE_PROVIDERS`.** Every `cps.golf` tenant sits
behind Cloudflare's browser check for non-browser clients: `curl` and `urllib`
receive `HTTP 403` with a "Verifying your browser · Club Prophet" HTML page for
both the site and the API path (checked on `peabody`, `chattanooga` and the
`onlineapi`/`onlineres` hosts). Only a real browser with JavaScript completes the
check. Solving or replaying that challenge from a worker would be bypassing bot
protection, which this project does not do, so `CpsGolfProvider` is implemented
against the verified shape but left unregistered — registering it would report a
blocked site as a silently empty tee sheet.

To enable it later, one of: CPS allowlists a client id or API key for these
tenants, CPS publishes a partner API on a host without the challenge, or the
courses move to an engine that answers non-browser clients. Then add
`"cps-golf": CpsGolfProvider` to `ENGINE_PROVIDERS`, and have the fingerprint
worker store `cps_site` (the tenant) and `cps_course_ids` (the comma-joined ids
from `OnlineCourses`) on the row.

### chronogolf — implemented

- A course's chronogolf page is `https://www.chronogolf.com/club/<slug>`. The
  page's `__NEXT_DATA__` names the club and lists each course as
  `{id, uuid, name, holes}`; the **uuid** is what the API takes, not the
  numeric id (Sassamon Trace is course `8444`, uuid
  `46e18274-b0e5-4138-9df4-ccf32b2fc4e1`).
- API: `GET https://www.chronogolf.com/marketplace/v2/teetimes`
- Query: `start_date=YYYY-MM-DD`, `course_ids=<course uuid>`,
  `start_time=HH%3AMM`, `page=1`. `holes` is optional (it must be a number if
  sent; `holes=all` is a `400`). `start_time` filters server-side, so the
  adapter sends `00:00` and applies the window itself. `players` is accepted
  but ignored.
- No cookie, token or browser check: a plain `urllib` request with an
  `Accept: application/json` header answers `200` from a server. `robots.txt`
  disallows `/private_api/` (the logged-in app) and says nothing about
  `/marketplace/`, which is the public tee sheet this adapter uses.
- Payload, read live on 2026-11-21 (Sassamon Trace and Nehoiden):
  `{"status": "open", "teetimes": [...]}`, where `status` is `open`,
  `sold_out` or `closed`. Each tee time carries course-local `date` and
  `start_time` (`"11:40"`), the utc `starts_at`, `min_player_size` /
  `max_player_size`, `hole`, `course: {id, uuid, name, holes,
  bookable_holes}`, `default_price` (`green_fee`, `affiliation_type`) and
  `frozen`.
- The endpoint pages **24 slots at a time** (page 2 held Nehoiden's 14:00–15:30
  slots, page 3 was empty), so `ChronogolfProvider.fetch_availability` walks
  pages until one comes back empty, stopping after five. A full day can
  therefore be five requests, and the marketplace answers `429` when asked in
  bursts: the adapter paces requests three seconds apart
  (`min_interval_seconds = 3.0`), and a `429` is logged by `_fetch_json`
  rather than disguised as an empty tee sheet.
- Row fields: `cg_course_ids` (the course uuid, comma-joined when a club books
  across several) and `cg_slug` (for the booking link). `from_row` requires the
  uuid: the endpoint answers `status: closed` with no error for an unknown one,
  so a slug-only row would look like a quiet course instead of a missing id.

### foreup — implemented

- Discovered from a course site's booking link, e.g. Robert T. Lynch Municipal
  Golf Course: `booking_domain: foreupsoftware.com`, evidence
  `https://foreupsoftware.com/index.php/booking/19865/2748#/teetimes`.
- Booking page: `/index.php/booking/<course_id>/<schedule_id>`
- JSON API: `/index.php/api/booking/times`
- A request of the form
  `?time=all&date=11-21-2026&holes=all&players=4&schedule_id=2748&booking_class=default`
  answers `200` with a JSON array of tee times. It needs no cookie, session or
  challenge, so the worker can call it directly.
- The fingerprint worker records the booking link it followed
  (`Fingerprint.booking_link`) and, for foreup, stores `foreup_course_id` and
  `foreup_schedule_id` on the row (`foreup_ids`). `ForeUpProvider.from_row`
  therefore reads the row and never fetches the booking page.
- Payload, read from live tee sheets on 2026-11-21 — Babe Zaharias (Tampa,
  `19717/2254`), Sunkist (`21874/9160`) and Robert T. Lynch (`19865/2748`):
  a bare array of objects with `time: "2026-11-21 14:50"` (course-local wall
  clock, no offset), `course_name`, `course_id`, `schedule_id`,
  `available_spots`, `allowed_group_sizes: ["3", "4"]` (strings, and often
  narrower than the seat count), `minimum_players`,
  `maximum_players_per_booking`, `holes`, and the green/cart fee fields.
- `players` filters on the server as well: a two-ball asking Babe Zaharias,
  which only books 3 and 4, gets `[]`. `parse_slots` repeats the check against
  `available_spots` and `allowed_group_sizes` so the adapter does not depend on
  it.
- `booking_class=default` makes no difference (byte-identical responses with
  and without), and `time=all&holes=all` are accepted as before.
- An unknown `schedule_id` answers `false`, not an array: `parse_slots` raises
  on that rather than reporting an empty tee sheet.
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
