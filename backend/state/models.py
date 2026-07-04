"""Pydantic models shared across the state store, API, simulator, and bot.

These mirror backend/db/schema.sql exactly -- if you add/rename a column,
update the matching model field here too.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DeviceKind = Literal["fan", "light"]
DeviceStateValue = Literal["on", "off"]
AlertKind = Literal["after_hours", "all_on_2h"]


class Room(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


class Device(BaseModel):
    """Internal attribute is `device_index` (matches the DB column). The frozen
    docs/API_CONTRACT.md JSON key is `index` -- the alias below means
    `Device(device_index=1, ...)` and `device.device_index` keep working
    everywhere in our own code, while `device.model_dump(by_alias=True)`
    produces the exact contract key for the API layer (Phase A3) to use.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    room_id: str
    kind: DeviceKind
    device_index: int = Field(alias="index")
    name: str
    state: DeviceStateValue
    wattage: int
    last_changed: str  # ISO8601


class RoomState(BaseModel):
    room_id: str
    all_on_since: Optional[str] = None   # ISO8601, None = not currently all-on
    last_alert_at: Optional[str] = None  # debounce for 'all_on_2h'
    last_hours_alert: Optional[str] = None  # debounce for 'after_hours'


class Alert(BaseModel):
    id: Optional[int] = None
    room_id: Optional[str] = None
    device_id: Optional[str] = None
    kind: AlertKind
    message: str
    created_at: str
    acked: bool = False


class Usage(BaseModel):
    day: str  # 'YYYY-MM-DD'
    watt_seconds: float = 0.0

    @property
    def kwh(self) -> float:
        return self.watt_seconds / 3_600_000


class RoomSnapshot(BaseModel):
    room: Room
    devices: list[Device]
    watts_now: int


class Snapshot(BaseModel):
    """Full state returned by GET /api/state and used by the bot's commands."""

    rooms: list[RoomSnapshot]
    devices: list[Device]
    watts_total: int
    generated_at: str
