# Initial architecture notes

## Search boundary

The application accepts a `SearchRequest` containing a ZIP code, date, time window, and optional player count. The ZIP code defaults to `02169` in application configuration, but the caller can override it on every request.

Each course or booking-site integration should implement a search adapter that returns a common `TeeTimeOption` shape. Adapters should be isolated from the UI and from one another so a change to one booking site does not affect the rest of the search system.

GolfNow is out of scope and should not be used as a data source or dependency.

## Booking boundary

Booking is intentionally disabled in the first release. To prepare for it, keep these concepts separate:

- `SearchProvider`: reads availability.
- `BookingProvider`: performs a booking only when explicitly invoked.
- `BookingRequest`: references a selected result and the requested player details, but never contains a password or payment credentials.
- `BookingResult`: reports a provider reference and status without persisting sensitive authentication material.

The booking provider must re-check the selected slot before attempting a reservation. A booking attempt should be idempotent where the provider permits it, and all provider-specific authentication should be injected at runtime from a secret manager or isolated browser/session service.

Booking is not implemented yet: there is no `BookingProvider`, `BookingRequest` or `BookingResult` in the codebase. The boundary above describes the shape any future booking integration should take, so search can stay availability-only.

## Privacy and access

The web application should be private by default, with authentication and authorization enforced before search requests are accepted. Search history and booking records, if added later, should be scoped to the owner account and should avoid storing raw provider responses when they contain personal data.
