from datetime import date, time

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    zip_code: str = Field(default="02169", pattern=r"^\d{5}(-\d{4})?$")
    date: date
    earliest_time: time | None = None
    latest_time: time | None = None
    players: int = Field(default=2, ge=1, le=8)


class TeeTimeOption(BaseModel):
    provider: str
    course_name: str
    start_time: time
    players: int
    booking_enabled: bool = False

