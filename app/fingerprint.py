"""Booking-page fingerprinting: who takes this course's bookings, and where?

Discovery knows a course exists and where it is; this module answers the next
question. Given a course row (name plus either a website or a list of website
guesses), it fetches each candidate page and classifies what it finds:

- **teeitup**: the page is (or redirects to, or frames) a TeeItUp booking
  engine. The facility ID is read out of the page when it is there.
- **other**: the course books elsewhere — the verdict names the provider when
  it is recognized (GolfNow, Chronogolf/Lightspeed, foreUP, Club Caddie,
  GolfBack, Teesnap, Gallus, Supreme Golf/Notes) and ``other`` otherwise.
- **unknown**: nothing loaded, or nothing recognizable was found. The row keeps
  its guesses so the next run can try again without repeating failures.

Guesses come from three places, cheapest first: the course's own website from
OpenStreetMap, the usual ``golf<town>.com``-style domains, and — for names that
look like a town course — the town site. Nothing here creates an account or
submits a form; it reads public pages the way a browser would.
"""

import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache

import certifi

logger = logging.getLogger(__name__)

#: A page that reached a booking engine, directly or through one hop.
TEEITUP_HOST_SUFFIX = ".book.teeitup.golf"

#: TeeItUp's booking widget, embedded by courses that keep their own site.
TEEITUP_WIDGET_MARKERS = (
    "book.teeitup.golf",
    "teeitup",
    "lightspeed",  # TeeItUp runs on Lightspeed commerce infra; the widget says so
)

#: Recognizable booking providers, matched against page URLs and content.
#: Order matters: more specific markers first.
OTHER_PROVIDERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("golfnow", ("golfnow.com", "golfnow")),
    ("chronogolf", ("chronogolf",)),
    ("foreup", ("foreup", "foreupsoftware")),
    ("clubcaddie", ("clubcaddie",)),
    ("golfback", ("golfback",)),
    ("teesnap", ("teesnap",)),
    ("gallus", ("gallusgolf", "gallus")),
    ("supreme-golf", ("supremenow.com", "supreme golf")),
)

#: Paths on a course's own site that usually hold the booking link.
BOOKING_PATH_HINTS = (
    "/tee-times",
    "/book-a-tee-time",
    "/book-tee-time",
    "/reservations",
    "/golf/tee-times",
    "/rates-and-tee-times",
)

#: How many external pages a single course costs at most. Course sites are slow
#: and redirect-happy; without a cap one bad site eats the Lambda's budget.
MAX_FETCHES_PER_COURSE = 12

_last_fetch = 0.0
FETCH_INTERVAL_SECONDS = 1.0


@lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _fetch(url: str) -> tuple[str, str]:
    """Fetch a page, following redirects. Returns (final_url, body)."""
    global _last_fetch
    import time

    elapsed = time.monotonic() - _last_fetch
    if elapsed < FETCH_INTERVAL_SECONDS:
        time.sleep(FETCH_INTERVAL_SECONDS - elapsed)
    _last_fetch = time.monotonic()

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "tee-time-finder/0.1 (personal tool)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=_tls_context()) as r:
            final = r.geturl()
            body = r.read().decode("utf-8", "replace")
            return final, body[:200_000]
    except (urllib.error.URLError, TimeoutError) as error:
        logger.info("fingerprint fetch failed for %s: %s", url, error)
        return url, ""


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _teeitup_facility_id(body: str) -> str | None:
    pushes = "".join(
        re.findall(r'self\.__next_f\.push\(\[1,"(.{0,60000}?)"\]\)', body, re.S)
    )
    match = re.search(
        r'gnFacilityIds\\?"?:\s*\\?"?\[([\d,\s]*)\]', pushes.replace('\\"', '"')
    )
    if not match:
        return None
    ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
    return ids[0] if ids else None


def _other_provider(url: str, body: str) -> str | None:
    haystack = f"{url}\n{body[:50_000]}".lower()
    for provider, markers in OTHER_PROVIDERS:
        if any(marker in haystack for marker in markers):
            return provider
    return None


def _booking_links(body: str, base: str) -> list[str]:
    """Anchor targets on a course page that look like they lead to booking."""
    links: list[str] = []
    for match in re.finditer(
        r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', body, re.I | re.S
    ):
        href, text = match.group(1), re.sub(r"<[^>]+>", "", match.group(2))
        blob = f"{href} {text}".lower()
        if any(
            word in blob
            for word in ("tee time", "teetime", "book", "reserve", "reservation")
        ):
            links.append(urllib.parse.urljoin(base, href.strip()))
    # Deduplicate while keeping page order: the first booking link is usually
    # the real one and the rest are nav/footer repeats.
    seen: set[str] = set()
    return [u for u in links if u and not (u in seen or seen.add(u))]


def website_guesses(course: dict) -> list[str]:
    """Candidate starting pages for a course, cheapest first.

    The OSM website wins when present. Otherwise the course name plus its town
    (from the display_name discovery saved) suggest the usual domain shapes —
    but those are guesses, so the course name is only ever used to *suggest*
    pages, never as evidence of anything.
    """
    guesses: list[str] = []
    if course.get("website"):
        guesses.append(course["website"])
    town = ""
    for part in (course.get("display_name") or "").split(","):
        part = part.strip()
        if part and part not in (course.get("name"),):
            town = part
            break
    slug = re.sub(r"[^a-z0-9]+", "", (course.get("name") or "").lower())
    town_slug = re.sub(r"[^a-z0-9]+", "", town.lower())
    for host in (f"{slug}.com", f"{slug}golf.com", f"golf{town_slug}.com"):
        if slug and host not in guesses:
            guesses.append(f"https://{host}")
    return guesses


@dataclass
class Fingerprint:
    provider: str = "unknown"  # teeitup | <other name> | other | unknown
    booking_domain: str = ""
    facility_id: str = ""
    evidence: str = ""
    checked_urls: list[str] = field(default_factory=list)


def fingerprint(course: dict) -> Fingerprint:
    """Classify how a course takes bookings, starting from its website guesses."""
    result = Fingerprint()
    fetches = 0

    def visit(url: str) -> tuple[str, str] | None:
        nonlocal fetches
        if fetches >= MAX_FETCHES_PER_COURSE or url in result.checked_urls:
            return None
        fetches += 1
        result.checked_urls.append(url)
        return _fetch(url)

    # The discovery Lambda may already have confirmed TeeItUp; re-check it is
    # still there rather than taking the old row's word for it.
    known_domain = course.get("booking_domain") or ""
    if known_domain and course.get("provider") in ("teeitup", "teeitup-confirmed"):
        final, body = _fetch(f"https://{known_domain}/")
        result.checked_urls.append(f"https://{known_domain}/")
        facility_id = _teeitup_facility_id(body)
        if TEEITUP_HOST_SUFFIX in _host(final) or facility_id:
            result.provider = "teeitup"
            result.booking_domain = known_domain
            result.facility_id = facility_id or course.get("facility_id") or ""
            result.evidence = f"booking engine still answers at {known_domain}"
            return result

    for start in website_guesses(course):
        first = visit(start)
        if first is None:
            continue
        final, body = first
        if TEEITUP_HOST_SUFFIX in _host(final):
            facility_id = _teeitup_facility_id(body)
            result.provider = "teeitup"
            result.booking_domain = _host(final)
            result.facility_id = facility_id or ""
            result.evidence = f"website {start} redirects to TeeItUp engine"
            return result
        lowered = body.lower()
        if any(marker in lowered for marker in TEEITUP_WIDGET_MARKERS):
            facility_id = _teeitup_facility_id(body)
            result.provider = "teeitup"
            result.booking_domain = _host(start) + " (embedded widget)"
            result.facility_id = facility_id or ""
            result.evidence = f"course site {start} embeds the TeeItUp widget"
            return result
        other = _other_provider(final, body)
        if other:
            result.provider = other
            result.booking_domain = _host(final)
            result.evidence = f"course site {start} points at {other}"
            return result

        # Follow the site's own booking links one hop: that is usually where
        # the provider — TeeItUp engine, GolfNow, or something else — lives.
        for link in _booking_links(body, final)[:4]:
            step = visit(link)
            if step is None:
                continue
            link_final, link_body = step
            if TEEITUP_HOST_SUFFIX in _host(link_final) or _teeitup_facility_id(
                link_body
            ):
                result.provider = "teeitup"
                result.booking_domain = _host(link_final)
                result.facility_id = _teeitup_facility_id(link_body) or ""
                result.evidence = f"booking link {link} leads to TeeItUp engine"
                return result
            other = _other_provider(link_final, link_body)
            if other:
                result.provider = other
                result.booking_domain = _host(link_final)
                result.evidence = f"booking link {link} leads to {other}"
                return result

        # A couple of conventional booking paths, for sites whose booking link
        # is JavaScript rather than an anchor.
        for hint in BOOKING_PATH_HINTS[:2]:
            probe = urllib.parse.urljoin(final, hint)
            step = visit(probe)
            if step is None:
                continue
            probe_final, probe_body = step
            if TEEITUP_HOST_SUFFIX in _host(probe_final) or _teeitup_facility_id(
                probe_body
            ):
                result.provider = "teeitup"
                result.booking_domain = _host(probe_final)
                result.facility_id = _teeitup_facility_id(probe_body) or ""
                result.evidence = f"booking path {probe} is a TeeItUp engine"
                return result

    return result
