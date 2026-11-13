# Architecture

Tee Time Finder is a private FastAPI app. One deployment package runs as three Lambdas: the HTTP API, a course-discovery worker, and a booking-page fingerprint worker. Search and discovery are separate. A tee time appears in results only when its course is a provider in `app/providers.py`.

GolfNow is not a search provider and is not a dependency of the API.

## Runtime

`app/main.py` builds the FastAPI app. `handler = Mangum(app)` is the API Lambda entry (`infra/lambda.tf`, handler `app.main.handler`). The same zip is deployed as:

| Function | Handler | Timeout | Trigger |
|---|---|---|---|
| `tee-time-finder` | `app.main.handler` | 30s | HTTP API |
| `tee-time-finder-discover` | `app.discovery.handler` | 10 min | EventBridge `cron(0 10 * * ? *)`, or an admin invoke |
| `tee-time-finder-fingerprint` | `app.fingerprint_handler.handler` | 10 min | EventBridge `cron(30 10 * * ? *)`, or an admin invoke |

The workers are not behind API Gateway. They do slow outbound work and need longer than the API's 30-second budget. Schedules are UTC, about 5–6am America/New_York, with fingerprint half an hour after discovery (`infra/schedules.tf`).

Locally the same app runs under uvicorn. See `docs/development.md`.

## Search

`POST /search` accepts a `SearchRequest` (`app/models.py`):

- ZIP code, default `02169`, overridable on every request
- Date
- Optional earliest and latest tee time
- Player count, default 2
- Radius in miles, default 25, maximum 100

`require_user` runs first. The handler then calls `search_all` (`app/providers.py`).

`search_all` resolves the caller's ZIP through `GeocodeStore` (`app/geo.py`). The first search for a ZIP asks Nominatim and stores the coordinates; later searches read them back. Course coordinates are seeded by `scripts/geocode_courses.py` into the same table (`pk` of `zip#02169` or `course#5720`). A course with no coordinates is still searched. Its distance stays `0`, which the page shows as unknown. A course farther than `radius_miles` is skipped before any booking call.

Each course is a `TeeItUpProvider` subclass that sets `facility_id`, `x_be_alias`, and `display_name`. The base class calls the TeeItUp tee-times API, waits at least two seconds between requests, converts UTC instants to `America/New_York`, and keeps slots on the requested date, inside the time window, that allow the requested player count. Results are `TeeTimeOption` values: provider, course name, date, start time, players, and distance. Duplicate course/date/time rows are dropped, then the list is sorted by start time.

`get_search_providers()` is the search catalog. It is ordered nearest to `02169` first so a small radius stops issuing calls early. Adding a course means a new subclass plus an entry in that list. Classified rows in the courses table are not loaded here.

## Authentication

The page at `GET /` is inline HTML in `app/main.py`. It reads `GET /config` for the default date and the Cognito hosted-UI URL, then posts the authorization code to `POST /auth/token`. The redirect URI comes from `Settings` (`app/config.py`): an explicit `COGNITO_REDIRECT_URI`, or `{base_url}/`. Cognito binds the code to that URI, and Terraform uses the same value for the app client's callback (`infra/lambda.tf`).

API Gateway routes:

| Route | Authorization |
|---|---|
| `GET /`, `GET /health`, `GET /config`, `POST /auth/token` | None |
| `ANY /{proxy+}`, including `POST /search` | JWT authorizer |
| `ANY /admin/{proxy+}` | JWT authorizer, then the `admins` group in the app |

The authorizer checks signature, issuer, and expiry. Its audience list is empty: Cognito access tokens carry `client_id` and no `aud`, so an audience check would reject every access token. `require_user` (`app/auth.py`) checks the app client id, token type, issuer, expiry, and signature again inside the app.

`GET /admin` tells a signed-in user whether they are in the Cognito `admins` group. `POST /admin/{step}` (`discover` or `fingerprint`) invokes that worker with `InvocationType=Event` and returns immediately (`app/admin.py`). The browser cannot choose the worker payload; the allowed bodies live in `STEPS`.

## Course catalog

Discovery records courses. It does not serve tee times.

`app.discovery.handler` does two things (`app/discovery.py`):

- **Sweep.** On the first run of a month, or when the event sets `force_sweep`, ask Nominatim which golf courses sit in the boxes in `app/discovery_config.py` and write a row for each new one. An explicit `zip_code` or `radius_miles` on the event forces a sweep of that box.
- **Backlog.** Probe up to 10 rows whose provider is still unclassified, fewest attempts first, against TeeItUp aliases (`app/teeitup_probe.py`). A row is left alone after five failed attempts. Facility ids already present in `get_search_providers()` are marked already configured.

`app.fingerprint_handler.handler` reads rows still marked `unknown` and classifies how the course takes bookings (`app/fingerprint.py`). A default run handles a small batch so it stays inside the ten-minute timeout. The admin fingerprint step asks for a limit of 10.

Both workers write `tee-time-finder-courses` (`infra/courses.tf`). A row is classified when `provider` is in `CLASSIFIED_PROVIDERS` (`teeitup`, `teeitup-confirmed`, `chronogolf`, `foreup`, and the other labels in that set, including `other`). `known` means classified, not merely present in the table.

A fingerprint may label a course `golfnow` so discovery stops probing it. Search still does not call GolfNow.

## Data and cost

Both DynamoDB tables are on-demand, keyed by `pk`:

- `tee-time-finder-geocode` caches ZIP and course coordinates.
- `tee-time-finder-courses` holds discovered courses, probe attempts, and fingerprint verdicts.

Cognito holds the user pool. The app does not create users in Terraform and does not store account secrets in the repo. Local configuration is an untracked `.env`; deployed configuration is Lambda environment variables from Terraform (`app/config.py`, `infra/lambda.tf`).

`infra/budget.tf` scopes a monthly budget to this project's tag. At the budget amount it emails `ALERT_EMAIL`. At the higher action threshold it attaches a deny policy to the Lambda role covering the two tables, which stops the nightly workers. The API and Cognito sign-in keep working. There is no account-wide spend cap.

## Boundaries

- Keep the app private. Search and admin actions require a Cognito token.
- Default ZIP `02169` stays overridable per search and is not a credential.
- Do not commit usernames, passwords, session cookies, or API keys.
- Respect each site's terms, robots policy, rate limits, and authentication. TeeItUp calls are paced; Nominatim is held to one request per second.
- Search history is not stored. If it is added later, scope it to the owner and do not keep raw provider responses that contain personal data.
