# Tee Time Finder

A private, personal tool for searching local golf tee times by date and time, with optional booking support through participating course booking sites.

## Project boundaries

- Avoid GolfNow.
- Keep the application private and access-controlled.
- Never commit usernames, passwords, session cookies, API keys, or other account information.
- Use a secret manager or local environment configuration for credentials.
- Respect each booking site's terms, robots policies, rate limits, and authentication requirements.
- Treat booking as an explicit user-confirmed action; searching and presenting options should be separate from placing a reservation.

## Development

Local secrets belong in an untracked `.env` file or an approved secret manager. See `.env.example` when configuration is added.

## Deployment direction

The initial design should favor low-cost AWS services and a small private surface area. Candidate components include a private web application, a lightweight scheduled/search backend, and AWS Secrets Manager or Parameter Store for sensitive configuration. The final architecture will depend on the number and type of booking sites involved.

