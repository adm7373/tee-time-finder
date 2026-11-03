# Tee Time Finder

A private, personal tool for searching local golf tee times by ZIP code, date, and time, with optional booking support through participating course booking sites.

## Project boundaries

- Avoid GolfNow.
- Keep the application private and access-controlled.
- Never commit usernames, passwords, session cookies, API keys, or other account information.
- Use a secret manager or local environment configuration for credentials.
- Respect each booking site's terms, robots policies, rate limits, and authentication requirements.
- Treat booking as an explicit user-confirmed action; searching and presenting options should be separate from placing a reservation.
- The default search ZIP code is `02169`; it must remain overridable per search and must not be treated as a credential.
- The initial release is search-only. Booking-related types and provider boundaries should be designed now, but booking must remain disabled until explicitly implemented and confirmed.

## Development

Local secrets belong in an untracked `.env` file or an approved secret manager. See `.env.example` when configuration is added.

## Initial search contract

Every search should accept:

- ZIP code, defaulting to `02169`
- Date
- Earliest and latest acceptable tee time
- Optional player count and maximum travel distance

Search results should use a provider-neutral format so that course sites can be added independently and GolfNow is never required.

## Booking readiness

Search providers should expose availability only. A future booking flow should use a separate booking capability that:

1. Re-checks availability immediately before booking.
2. Requires an explicit confirmation from the user.
3. Keeps credentials and browser sessions in a secret manager or isolated runtime.
4. Returns a provider booking reference without storing passwords or payment data.
5. Records auditable status transitions such as `available`, `booking_requested`, `confirmed`, and `failed`.

## Deployment direction

The initial design should favor low-cost AWS services and a small private surface area. Candidate components include a private web application, a lightweight scheduled/search backend, and AWS Secrets Manager or Parameter Store for sensitive configuration. The final architecture will depend on the number and type of booking sites involved.

