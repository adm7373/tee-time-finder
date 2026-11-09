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
    date: date
    start_time: time
    players: int
    distance_miles: float


class StrEnum(StrEnum):
    available = "available"
    booking_requested = "booking_requested"
    confirmed = "confirmed"
    failed = "failed"


# Booking concept removed - these classes were related to booking operations
# and are kept only if needed for other functionality
