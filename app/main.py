from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from .auth import require_user
from .config import get_settings
from .models import SearchRequest, TeeTimeOption
from .providers import search_all
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return """
    <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Tee Time Finder</title><style>
    body{font:16px system-ui;max-width:760px;margin:40px auto;padding:0 20px;color:#18322a}
    input,button{font:inherit;padding:10px;margin:4px 0}button{cursor:pointer}
    .card{border:1px solid #ccd8d2;border-radius:8px;padding:14px;margin:10px 0}
    </style></head><body><h1>Tee Time Finder</h1>
    <p>Search local courses by date, time, players, and ZIP code.</p>
    <button id="login">Sign in with Cognito</button>
    <form id="search" hidden><label>Date <input id="date" type="date" required></label>
    <label>ZIP <input id="zip" inputmode="numeric" value="02169" required></label>
    <label>Players <input id="players" type="number" min="1" max="4" value="2"></label>
    <button>Search tee times</button></form><div id="results"></div>
    <script>
    const base=location.origin; let token=null;
    document.querySelector('#login').onclick=()=>{location.href='https://tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com/login?client_id=2o1mvf7bfiumaet2rpiubo2js1&response_type=code&scope=openid+email&redirect_uri='+encodeURIComponent(base+'/')};
    const code=new URLSearchParams(location.search).get('code');
    if(code){fetch('/auth/token',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({code,redirect_uri:base+'/'})}).then(r=>r.json()).then(x=>{token=x.access_token;document.querySelector('#login').hidden=true;document.querySelector('#search').hidden=false;history.replaceState({},'',base)})}
    document.querySelector('#search').onsubmit=async e=>{e.preventDefault();const r=await fetch('/search',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer '+token},body:JSON.stringify({date:date.value,zip_code:zip.value,players:+players.value})});results.innerHTML=r.ok?(await r.json()).map(x=>`<div class="card"><b>${x.course_name}</b><br>${x.start_time} · ${x.players} players</div>`).join(''):'Please complete sign-in, then try again.'};
    </script></body></html>
    """


@app.post("/auth/token")
def exchange_code(payload: dict[str, str]) -> dict[str, str]:
    """Exchange a Cognito authorization code without storing credentials in the browser."""
    domain = "tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com"
    client_id = settings.cognito_app_client_id
    if not client_id or not payload.get("code") or not payload.get("redirect_uri"):
        return {"error": "invalid_request"}
    body = urlencode({"grant_type": "authorization_code", "client_id": client_id, "code": payload["code"], "redirect_uri": payload["redirect_uri"]}).encode()
    request = Request(f"https://{domain}/oauth2/token", data=body, headers={"content-type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict[str, str]:
    """Return non-sensitive defaults needed by the search form."""
    return {"default_search_zip": settings.default_search_zip}


@app.post("/search", response_model=list[TeeTimeOption])
def search(request: SearchRequest, user: dict = Depends(require_user)) -> list[TeeTimeOption]:
    """Search all configured providers; booking is intentionally disabled."""
    return search_all(request)


handler = Mangum(app)

