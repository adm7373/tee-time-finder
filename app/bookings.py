"""Booking provider boundary.

Booking is intentionally disabled in the initial release. The types and provider
boundary are designed now so a real booking integration can be added later
without touching the search layer.

Per the project boundaries:
- Booking is an explicit user-confirmed action, separate from search.
- A BookingRequest never contains passwords, session cookies, or payment data.
- A BookingResult reports a provider reference and status without persisting
  sensitive authentication material.
- Providers must re-check availability before attempting a reservation.
- Provider-specific authentication is injected at runtime from a secret manager
  or isolated browser/session service.
"""

from abc import ABC, abstractmethod
from datetime import time

from .models import BookingRequest, BookingResult, BookingStatus


class BookingProvider(ABC):
    """Boundary used by real course booking integrations later."""

    name = "base"

    @abstractmethod
    def book(self, request: BookingRequest) -> BookingResult:
        """Attempt to book the selected slot.

        Implementations must re-check availability immediately before reserving
        and must never store credentials or payment data.
        """
        raise NotImplementedError


class DisabledBookingProvider(BookingProvider):
    """Placeholder provider that keeps booking disabled in this release."""

    name = "disabled"

    def book(self, request: BookingRequest) -> BookingResult:
        return BookingResult(
            provider=self.name,
            course_name=request.course_name,
            start_time=request.start_time,
            booking_reference=None,
            status=BookingStatus.failed,
            detail="Booking is not enabled in this release.",
        )


def get_booking_providers() -> list[BookingProvider]:
    return [DisabledBookingProvider()]


def book_all(request: BookingRequest) -> list[BookingResult]:
    results = [provider.book(request) for provider in get_booking_providers()]
    return sorted(results, key=lambda result: result.start_time)
