from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


def utcnow():
    return datetime.now(timezone.utc)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_demo: bool = False

    @model_validator(mode="after")
    def coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        return self


class Donation(Record):
    description: str = Field(min_length=1)
    portions: int = Field(gt=0, strict=True)
    available_from: AwareDatetime | None = None
    pickup_deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.available_from and self.pickup_deadline:
            if self.pickup_deadline <= self.available_from:
                raise ValueError("Pickup deadline must be after food becomes available.")
        return self


class Recipient(Record):
    accepts_prepared_meals: bool | None = None
    available_portions: int | None = Field(default=None, ge=0, strict=True)
    receiving_start: AwareDatetime | None = None
    receiving_end: AwareDatetime | None = None
    verified_at: AwareDatetime | None = None
    source: str = "coordinator"
    source_note: str = ""

    @model_validator(mode="after")
    def valid_window(self):
        if self.receiving_start and self.receiving_end:
            if self.receiving_end <= self.receiving_start:
                raise ValueError("Receiving end must be after receiving start.")
        return self


def window_status(recipient, now=None):
    now = now or utcnow()
    if not recipient.receiving_start or not recipient.receiving_end:
        return "UNKNOWN"
    if now < recipient.receiving_start:
        return "UPCOMING"
    return "EXPIRED" if now >= recipient.receiving_end else "OPEN"
