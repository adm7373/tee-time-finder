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

The mock provider returns deterministic sample results and is deliberately marked with `booking_enabled=false`. Real course adapters should implement the same provider boundary and remain independent from the web layer.

Cognito settings are loaded from `.env` locally and should come from AWS Secrets Manager or Parameter Store in deployment. The `/search` endpoint requires a Cognito access token and validates its signature, issuer, expiration, token type, and app client ID. `/health` and `/config` remain public.

