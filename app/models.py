from datetime import date, time
from enum import StrEnum

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    zip_code: str = Field(default="02169", pattern=r"^\d{5}(-\d{4})?$")
    radius_miles: float = Field(default=25, gt=0, le=100)
    date: date
    earliest_time: time | None = None
    latest_time: time | None = None
    players: int = Field(default=2, ge=1, le=8)


class TeeTimeOption(BaseModel):
    provider: str
    course_name: str
    start_time: time
    players: int
    distance_miles: float
    booking_enabled: bool = False


class BookingStatus(StrEnum):
    available = "available"
    booking_requested = "booking_requested"
    confirmed = "confirmed"
    failed = "failed"


class BookingRequest(BaseModel):
    """Reference to a selected tee-time slot plus player details.

    Never contains passwords, session cookies, or payment credentials.
    """

    provider: str
    course_name: str
    start_time: time
    players: int = Field(default=2, ge=1, le=8)


class BookingResult(BaseModel):
    """Outcome of a booking attempt, without sensitive auth material."""

    provider: str
    course_name: str
    start_time: time
    booking_reference: str | None = None
    status: BookingStatus
    detail: str | None = None

