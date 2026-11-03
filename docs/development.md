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
- `POST /search` — search contract; returns no results until a provider adapter is added

Cognito settings are configuration placeholders at this stage. The next authentication step will validate Cognito-issued JWTs at the API boundary before allowing search requests.

