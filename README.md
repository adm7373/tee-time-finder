# Tee Time Finder

A private, personal tool for searching local golf tee times by date, time, players and travel distance.

## Project boundaries

- Avoid GolfNow.
- Keep the application private and access-controlled.
- Never commit usernames, passwords, session cookies, API keys, or other account information.
- Use a secret manager or local environment configuration for credentials.
- Respect each site's terms, robots policies, rate limits, and authentication requirements.
- The default search ZIP code is `02169`; it must remain overridable per search and must not be treated as a credential.

## Development

Local secrets belong in an untracked `.env` file or an approved secret manager. See `.env.example` when configuration is added.

## Initial search contract

Every search should accept:

- ZIP code, defaulting to `02169`
- Date
- Earliest and latest acceptable tee time
- Optional player count and maximum travel distance

Search results should use a provider-neutral format so that course sites can be added independently and GolfNow is never required.

## Deployment direction

The initial design should favor low-cost AWS services and a small private surface area. Candidate components include a private web application, a lightweight scheduled/search backend, and AWS Secrets Manager or Parameter Store for sensitive configuration.
