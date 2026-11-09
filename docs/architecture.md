# Initial architecture notes

## Search boundary

The application accepts a `SearchRequest` containing a ZIP code, date, time window, and optional player count. The ZIP code defaults to `02169` in application configuration, but the caller can override it on every request.

Each course integration should implement a search adapter that returns a common `TeeTimeOption` shape. Adapters should be isolated from the UI and from one another so a change to one course does not affect the rest of the search system.

GolfNow is out of scope and should not be used as a data source or dependency.

## Privacy and access

The web application should be private by default, with authentication and authorization enforced before search requests are accepted. Search history, if added later, should be scoped to the owner account and should avoid storing raw provider responses when they contain personal data.
