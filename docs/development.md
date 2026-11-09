# Local development

Create a virtual environment, install dependencies, and run the API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

The endpoints are:

- `GET /` — the search page
- `GET /health` — service health check
- `GET /config` — safe client defaults, including ZIP `02169` and the Cognito login URL
- `POST /auth/token` — exchanges a Cognito authorization code for a token
- `POST /search` — Cognito-protected tee-time search

Courses are served by the TeeItUp adapters in `app/providers.py`; each subclass only declares its facility ID and `x-be-alias`. New courses should follow the same provider boundary and stay independent from the web layer.

Run the tests with `python -m pytest`.

Cognito settings are loaded from `.env` locally and should come from AWS Secrets Manager or Parameter Store in deployment. The `/search` endpoint requires a Cognito access token and validates its signature, issuer, expiration, token type, and app client ID. `/health` and `/config` remain public.
