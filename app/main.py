from datetime import date
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from urllib.parse import urlencode
from urllib.request import HTTPError, Request, urlopen
import json

from .auth import require_user
from .config import get_settings
from .geo import GeocodeStore, Geocoder
from .models import SearchRequest, TeeTimeOption
from .providers import search_all
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")

# One store and one geocoder per process: the store memoises coordinates it has
# already read, so a warm Lambda avoids DynamoDB entirely on repeat searches.
_geocode_store = lru_cache(maxsize=1)(lambda: GeocodeStore(settings.geocode_table_name))
_geocoder = lru_cache(maxsize=1)(lambda: Geocoder(settings.nominatim_user_agent))


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return """
    <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Tee Time Finder</title><style>
    body{font:16px system-ui;max-width:760px;margin:40px auto;padding:0 20px;color:#18322a}
    input,button{font:inherit;padding:10px;margin:4px 0}button{cursor:pointer}
    .card{border:1px solid #ccd8d2;border-radius:8px;padding:14px;margin:10px 0}
    </style></head><body><h1>Tee Time Finder</h1>
    <p>Search local courses by date, time, players, ZIP code, and radius.</p>
    <button id="login">Sign in with Cognito</button>
    <form id="search" hidden><label>Date <input id="date" type="date" required></label>
    <label>ZIP <input id="zip" inputmode="numeric" value="02169" required></label>
    <label>Radius (miles) <input id="radius" type="number" min="1" max="100"
    step="1" value="25"></label>
    <label>Earliest <input id="earliest" type="time"></label>
    <label>Latest <input id="latest" type="time"></label>
    <label>Players <input id="players" type="number" min="1" max="4" value="2"></label>
    <button>Search tee times</button></form><div id="results"></div>
    <script>
    let token = null;
    const results = document.querySelector('#results');
    const $ = id => document.getElementById(id);

    async function post(url, body, bearer) {
      const headers = {'content-type': 'application/json'};
      if (bearer) headers.authorization = 'Bearer ' + bearer;
      const response = await fetch(url, {method: 'POST', headers, body: JSON.stringify(body)});
      return {ok: response.ok, status: response.status, body: await response.json()};
    }

    // The login URL and the registered redirect URI both come from /config, so
    // the browser never has to guess the redirect URI: a guessed one is what
    // makes Cognito answer with error=redirect_mismatch.
    async function signIn() {
      const auth = await (await fetch('/config')).json();
      if (!auth.cognito_login_url) {
        results.textContent = 'Sign-in is not configured.';
        return;
      }
      $('login').onclick = () => { location.href = auth.cognito_login_url; };
      $('date').value = auth.default_date;

      const code = new URLSearchParams(location.search).get('code');
      if (!code) return;
      const {ok, body} = await post('/auth/token', {code});
      if (!ok || !body.access_token) {
        const detail = body.detail || {};
        throw new Error(detail.error_description || detail.error || 'Token exchange failed');
      }
      token = body.access_token;
      $('login').hidden = true;
      $('search').hidden = false;
      history.replaceState({}, '', location.pathname);
    }

    $('search').onsubmit = async event => {
      event.preventDefault();
      const {ok, status, body} = await post('/search', {
        date: $('date').value,
        zip_code: $('zip').value,
        radius_miles: +$('radius').value,
        earliest_time: $('earliest').value || null,
        latest_time: $('latest').value || null,
        players: +$('players').value,
      }, token);
      if (!ok) {
        results.textContent = 'Search failed (' + status + ').';
        return;
      }
      results.innerHTML = body.map(x =>
        `<div class="card"><b>${x.course_name}</b><br>${x.date} ${x.start_time}` +
        ` · ${x.distance_miles ? "~" + x.distance_miles + " miles" : "distance unknown"}` +
        ` · ${x.players} players</div>`
      ).join('') || 'No tee times found.';
    };

    signIn().catch(error => {
      results.textContent = 'Sign-in could not be completed: ' + error.message;
    });
    </script></body></html>
    """


@app.post("/auth/token")
def exchange_code(payload: dict[str, str]) -> dict[str, object]:
    """Exchange a Cognito authorization code without storing credentials in the browser.

    The redirect URI is taken from configuration rather than from the request:
    Cognito binds the code to the URI it was issued for, so the exchange must
    repeat that exact value.
    """
    client_id = settings.cognito_app_client_id
    token_url = settings.cognito_token_url
    redirect_uri = settings.effective_redirect_uri
    if not (client_id and token_url and redirect_uri and payload.get("code")):
        raise HTTPException(status_code=400, detail={"error": "invalid_request"})
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": payload["code"],
            "redirect_uri": redirect_uri,
        }
    ).encode()
    request = Request(
        token_url,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except HTTPError as error:
        # Surface Cognito's own error rather than a generic message: the
        # difference between invalid_grant and invalid_redirect is the whole
        # diagnosis when sign-in fails.
        try:
            details = json.loads(error.read())
        except ValueError:
            details = {}
        raise HTTPException(
            status_code=error.code,
            detail={
                "error": details.get("error", "token_exchange_failed"),
                "error_description": details.get(
                    "error_description", "Cognito rejected the authorization code"
                ),
            },
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict[str, str | None]:
    """Non-sensitive values the page needs: defaults and the Cognito login URL.

    Everything here is already visible to any browser that loads the page; no
    secret is exposed.
    """
    return {
        "default_search_zip": settings.default_search_zip,
        "default_date": date.today().isoformat(),
        "cognito_login_url": settings.cognito_login_url,
        "cognito_redirect_uri": settings.effective_redirect_uri,
    }


@app.post("/search", response_model=list[TeeTimeOption])
def search(
    request: SearchRequest, user: dict = Depends(require_user)
) -> list[TeeTimeOption]:
    """Search all configured providers.

    Guarded by ``require_user`` alone. The API Gateway JWT authorizer checks the
    token's signature, issuer and expiry but not its audience: Cognito access
    tokens carry ``client_id`` rather than ``aud``, so the app client is
    verified here instead.
    """
    return search_all(request, _geocode_store(), _geocoder())


handler = Mangum(app)
