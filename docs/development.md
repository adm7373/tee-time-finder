# Local development

Create a virtual environment, install dependencies, and run the API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

The initial endpoints are:

- `GET /health` — service health check
- `GET /config` — safe client defaults, including ZIP `02169`
- `POST /search` — Cognito-protected search contract using the temporary mock provider

Cognito settings are loaded from `.env` locally and should come from AWS Secrets Manager or Parameter Store in deployment. The `/search` endpoint requires a Cognito access token and validates its signature, issuer, expiration, token type, and app client ID. `/health` and `/config` remain public.

## Cognito sign-in configuration

Sign-in is a standard authorization-code flow against the Cognito hosted UI. Three
values have to agree, and the app now takes all of them from its own configuration
instead of letting the browser derive them:

| Setting | What it is | Local example |
| --- | --- | --- |
| `BASE_URL` | Where the app itself is served | `http://localhost:8000` |
| `COGNITO_DOMAIN_URL` | The hosted UI domain; `/oauth2/authorize` and `/oauth2/token` live here | `https://tee-time-finder-123456789012.auth.us-east-1.amazoncognito.com` |
| `COGNITO_REDIRECT_URI` | The OAuth callback; must be listed in the app client's `callback_urls` | `http://localhost:8000/` (defaults to `BASE_URL` + `/`) |

`GET /config` returns `cognito_login_url` and `cognito_redirect_uri` so the page
never has to construct either. `POST /auth/token` always sends the configured
redirect URI, because Cognito binds the authorization code to it.

If Cognito redirects to `/error?error=redirect_mismatch`, the redirect URI the page
sent is not in the app client's `callback_urls`. In Terraform both sides come from
the `cognito_callback_url` local in `infra/lambda.tf`, so compare `/config`'s
`cognito_redirect_uri` with the app client's list after any URL change.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_auth_flow.py` covers the sign-in flow: the redirect URI resolution, the
login URL served by `/config`, the token endpoint host, and the requirement that the
template does not build its own login URL.
