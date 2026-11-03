from fastapi import Depends, FastAPI

from .auth import require_user
from .config import get_settings
from .models import SearchRequest, TeeTimeOption
from .providers import search_all
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")


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

