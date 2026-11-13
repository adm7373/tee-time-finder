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

## Search contract

Every search accepts:

- ZIP code, defaulting to `02169`
- Date
- Earliest and latest acceptable tee time
- Player count and maximum travel distance

Results use a provider-neutral `TeeTimeOption` so course sites can be added independently. GolfNow is never required.

## Deployment

The running system is a private HTTP API, two scheduled workers, Cognito, and two DynamoDB tables. Layout, auth, and the split between search and discovery are in [docs/architecture.md](docs/architecture.md). Local setup is in [docs/development.md](docs/development.md). Terraform details are in [infra/README.md](infra/README.md).
