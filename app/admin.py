"""Admin routes: trigger the worker Lambdas and report what they last did.

The work itself is slow by design — a discovery sweep is paced against
Nominatim's usage policy and a fingerprint batch fetches up to a dozen pages
per course. Neither fits in the API's 30-second timeout, so these routes
**invoke the worker Lambdas asynchronously** and return immediately. The worker
runs with its own ten-minute budget, which is also why it is not behind API
Gateway in the first place.
"""

import datetime
import json
from typing import Any

import boto3
from fastapi import Depends, HTTPException, status

from .auth import require_user
from .config import get_settings

#: Cognito group whose members may trigger the workers. Membership is the
#: permission: nothing else is checked.
ADMIN_GROUP = "admins"

#: What each button sends to its worker. Kept here rather than in the page so
#: the browser cannot pick arbitrary parameters for an expensive run.
STEPS: dict[str, dict[str, Any]] = {
    "discover": {
        "function": "discover",
        "payload": {"force_sweep": True},
        "label": "Discovery sweep",
        "detail": "Sweeps OpenStreetMap for courses near the configured ZIP.",
    },
    "fingerprint": {
        "function": "fingerprint",
        "payload": {"limit": 10},
        "label": "Fingerprint batch",
        "detail": "Fetches up to 10 unclassified course sites to find their booking engine.",
    },
}


def is_admin(claims: dict) -> bool:
    """Whether these Cognito claims belong to an admin.

    ``cognito:groups`` is only present in an access token when the client
    requested the ``aws.cognito.signin.user.admin`` scope, so a missing claim
    is a "no", never a crash.
    """
    groups = claims.get("cognito:groups") or []
    return ADMIN_GROUP in groups


def require_admin(claims: dict = Depends(require_user)) -> dict:
    if not is_admin(claims):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Admin access requires membership of the {ADMIN_GROUP} group",
        )
    return claims


def _function_name(step: str) -> str:
    return f"{get_settings().project_name}-{STEPS[step]['function']}"


def trigger(step: str, payload: dict | None = None) -> dict:
    """Start a worker and return at once.

    ``InvocationType="Event"`` is the point: a synchronous invoke would hold the
    API request open for the worker's whole runtime and hit the 30-second
    timeout, killing the worker halfway through and leaving a partial table
    with nothing to show for it.
    """
    if step not in STEPS:
        raise HTTPException(status_code=404, detail=f"Unknown step: {step}")

    settings = get_settings()
    if not settings.aws_region:
        raise HTTPException(
            status_code=503, detail="No AWS region configured for worker invocation"
        )

    body = dict(STEPS[step]["payload"])
    body.update(payload or {})

    try:
        response = boto3.client("lambda", region_name=settings.aws_region).invoke(
            FunctionName=_function_name(step),
            InvocationType="Event",
            Payload=json.dumps(body).encode(),
        )
    except Exception as error:  # botocore errors are not worth enumerating here
        raise HTTPException(
            status_code=502, detail=f"Could not start {step}: {error}"
        ) from error

    return {
        "step": step,
        "started": True,
        "status_code": response.get("StatusCode", 202),
        "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "payload": body,
    }
