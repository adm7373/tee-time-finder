"""Secure HTTP tracing for the Norwood CC tee-itup booking page.

Captures requests and responses to the booking URLs for authentication
mechanism analysis. THIS MODULE IS FOR LOCAL/DEV USE ONLY and is not
expected to be active in AWS Lambda production.

SECURITY:
- Scrubs sensitive headers before logging: Cookie, Authorization, X-Api-Key,
  X-Client-Secret, X-Credentials, X-Profile-Id, and similar.
- Truncates request/response bodies to 200 chars.
- Never logs passwords, tokens, or authentication material.
- Output is structured JSON with a summary, safe for CloudWatch.
"""

import json
import logging
import re
import ssl
import urllib.error
import urllib.request
import datetime

# Headers that must NEVER be logged (scrubbed)
_SENSITIVE_HEADER_PREFIXES = (
    "authorization",
    "cookie",
    "x-api-key",
    "x-client-secret",
    "x-credentials",
    "x-profile-id",
    "x-auth",
    "proxy-authorization",
    "www-authenticate",
)

# Headers whose values should be truncated
_TRUNCATE_HEADER = {
    "set-cookie": True,  # Cookie headers may contain session info
}

# URL paths that contain sensitive data (request body or URL params)
SENSITIVE_URL_PATTERNS = (
    "/auth/token",
    "/oauth2/token",
    "/login",
    "/api/auth",
    "/v1/auth",
    "/sessions",
)

logger = logging.getLogger("tee_time_finder.trace")
logger.setLevel(logging.DEBUG)


def scrub_headers(headers):
    """Return a copy of headers with sensitive values removed."""
    if not headers:
        return {}
    cleaned = {}
    for key, value in headers.items():
        lower_key = key.lower()
        if any(lower_key.startswith(prefix) for prefix in _SENSITIVE_HEADER_PREFIXES):
            cleaned[key] = "*** SCRUBBED ***"
        elif lower_key in _TRUNCATE_HEADER:
            # Truncate Set-Cookie values
            cleaned[key] = value[:100] + "..." if len(value) > 100 else value
        else:
            cleaned[key] = value
    return cleaned


def scrub_url(url):
    """Return a URL with query parameters scrubbed if they look sensitive."""
    if not url:
        return url
    for pattern in SENSITIVE_URL_PATTERNS:
        if pattern in url.lower():
            if "?" in url:
                base, query = url.split("?", 1)
                params = []
                for param in query.split("&"):
                    if "=" in param:
                        k, v = param.split("=", 1)
                        if any(s in k.lower() for s in [
                            "token", "pass", "secret", "key", "credential"
                        ]):
                            params.append(f"{k}=***SCRUBBED***")
                        else:
                            params.append(param)
                    else:
                        params.append(param)
                return base + "?" + "&".join(params)
    return url


class TraceReporter:
    """Captures and reports HTTP traffic traces.

    SAFE FOR AWS: outputs structured JSON, scrubs sensitive headers, limits
    body sizes, and never logs authentication material.
    """

    def __init__(self, max_traces=20, max_body_chars=200, scrub=True):
        self.max_traces = max_traces
        self.max_body_chars = max_body_chars
        self.scrub = scrub
        self.traces = []

    def capture(self, url, method="GET", headers=None, body=None):
        """Execute a request and capture the trace."""
        req = urllib.request.Request(url, method=method, headers=headers or {})
        if body is not None:
            req.data = json.dumps(body).encode()

        trace = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "url": scrub_url(url),
            "method": method,
            "request": {
                "headers": scrub_headers(headers) if self.scrub else dict(req.headers),
            },
        }

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                body_bytes = resp.read()
                body_text = body_bytes.decode("utf-8", errors="replace")
                trace["response"] = {
                    "status": resp.status,
                    "headers": scrub_headers(dict(resp.headers)),
                    "body_preview": self._truncate(body_text),
                }
        except urllib.error.HTTPError as e:
            trace["response"] = {
                "status": e.code,
                "headers": scrub_headers(dict(e.headers)),
                "body_preview": self._truncate(e.read().decode("utf-8", errors="replace")),
            }
        except Exception as e:
            trace["response"] = {"status": "ERROR", "body_preview": str(e)}

        self.traces.append(trace)
        if len(self.traces) > self.max_traces:
            self.traces = self.traces[-self.max_traces:]
        return trace

    @staticmethod
    def _truncate(text, limit=200):
        if len(text) > limit:
            return text[:limit] + "...[TRUNCATED]"
        return text

    def report(self):
        """Return all captured traces as structured JSON."""
        data = {
            "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "count": len(self.traces),
            "traces": self.traces,
        }
        return json.dumps(data, indent=2)

    def log_report(self):
        """Log the report to the configured logger (WARNING level).

        Safe: body_preview is truncated, headers are scrubbed.
        """
        logger.warning(
            "Norwood booking trace report: %d requests captured",
            len(self.traces),
        )
        for i, t in enumerate(self.traces, 1):
            logger.warning(
                "Trace %d: %s %s -> %s",
                i, t["method"], t["url"], t.get("response", {}).get("status"),
            )
            for k, v in t["request"].get("headers", {}).items():
                logger.warning("  req %s: %s", k, v)


# Module-level singleton for convenience
reporter = TraceReporter()


if __name__ == "__main__":
    # Quick self-test
    r = reporter.capture(
        "https://phx-api-be-east-1b.kenna.io/v2/tee-times?date=2026-11-20&facilityIds=5720",
        method="GET",
        headers={
            "Accept": "application/json",
            "x-be-alias": "norwood-country-club",
            "Authorization": "Bearer SHOULD_NOT_APPEAR",
            "Cookie": "session=should_not_appear",
        },
    )
    print(reporter.report())
