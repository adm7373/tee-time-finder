"""Tests for TLS handling in the provider HTTP layer.

The provider used to retry a failed request with ``verify_mode = CERT_NONE``,
which silently disabled certificate verification for every search that hit the
fallback. These tests pin that verification stays on.
"""

import ssl
from datetime import date

import pytest

from app import providers as providers_module
from app.models import SearchRequest
from app.providers import NorwoodCCProvider


def test_tls_context_verifies_certificates():
    ctx = providers_module._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_tls_context_uses_certifi_bundle():
    import certifi

    # A context loaded from certifi's bundle has trusted roots; the default
    # context may not, which is what made the old code fall back to CERT_NONE.
    assert providers_module._tls_context().get_ca_certs() == (
        ssl.create_default_context(cafile=certifi.where()).get_ca_certs()
    )


def test_failed_request_does_not_retry_without_verification(monkeypatch):
    """A failure must be reported, not retried with verification disabled."""
    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(context)
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(providers_module, "urlopen", fake_urlopen)

    provider = NorwoodCCProvider()
    assert provider._fetch_json("https://example.test/api") is None

    assert len(calls) == 1, "request should not be retried"
    for context in calls:
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True


def test_availability_returns_nothing_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(NorwoodCCProvider, "_fetch_json", lambda self, url: None)
    assert (
        NorwoodCCProvider().fetch_availability(
            SearchRequest(date=date(2026, 11, 10), players=2)
        )
        == []
    )


@pytest.mark.parametrize("alias", ["norwood-country-club", "presidents-golf-course"])
def test_each_provider_reuses_one_context(alias):
    # The context is built once and cached; rebuilding it per request would
    # re-read the CA bundle on every search.
    first = providers_module._tls_context()
    second = providers_module._tls_context()
    assert first is second
