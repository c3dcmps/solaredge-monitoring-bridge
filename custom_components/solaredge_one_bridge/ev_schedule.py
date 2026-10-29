"""Typed, bounded read-only EV schedules without provider identifiers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

WEEKDAYS = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)
MAX_WINDOWS = 32


@dataclass(frozen=True, slots=True)
class EVChargingWindow:
    """One configured weekly window; enabled is independent of its existence."""

    enabled: bool
    days: tuple[str, ...]
    start_minute: int
    end_minute: int

    def as_dict(self) -> dict[str, Any]:
        """Return only normalized fields suitable for Home Assistant attributes."""
        return {
            "enabled": self.enabled,
            "days": list(self.days),
            "start": f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}",
            "end": f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}",
            "crosses_midnight": self.end_minute <= self.start_minute
            or self.end_minute == 1440,
        }


@dataclass(frozen=True, slots=True)
class EVScheduleSnapshot:
    """Sanitized configured slots and an optional provider-reported next interval."""

    windows: tuple[EVChargingWindow, ...] | None
    next_start: datetime | None
    next_end: datetime | None
    next_reason: str
    checked_at: datetime
    time_zone: str

    @property
    def schedule_count(self) -> int | None:
        """Count configured slots including those that are disabled."""
        return len(self.windows) if self.windows is not None else None

    @property
    def enabled_count(self) -> int | None:
        """Count only explicitly enabled slots in a valid configuration."""
        if self.windows is None:
            return None
        return sum(window.enabled for window in self.windows)


def _window(value: object) -> EVChargingWindow:
    if not isinstance(value, Mapping) or type(value.get("enable")) is not bool:
        raise ValueError("EV schedule activation flag is invalid")
    start, end = _window_clocks(value)
    days = _weekdays(value.get("scheduledDays"))
    if value.get("smartSaver", False) is not False:
        raise ValueError("EV smart-saver schedules are unsupported")
    return EVChargingWindow(value["enable"], days, start, end)


def _window_clocks(value: Mapping[str, Any]) -> tuple[int, int]:
    start, end = value.get("startTime"), value.get("endTime")
    if type(start) is not int or type(end) is not int:
        raise ValueError("EV schedule clock values are invalid")
    if not 0 <= start < 1440 or not 0 <= end <= 1440 or start == end:
        raise ValueError("EV schedule interval is invalid")
    return start, end


def _weekdays(days: object) -> tuple[str, ...]:
    if not isinstance(days, list) or not 1 <= len(days) <= 7:
        raise ValueError("EV schedule weekdays are invalid")
    if any(not isinstance(day, str) or day not in WEEKDAYS for day in days):
        raise ValueError("EV schedule weekday is invalid")
    return tuple(day for day in WEEKDAYS if day in days)


def _windows(value: object) -> tuple[EVChargingWindow, ...] | None:
    if not isinstance(value, list) or len(value) > MAX_WINDOWS:
        return None
    try:
        return tuple(_window(window) for window in value)
    except ValueError:
        return None


def _timestamp(value: object) -> datetime | None:
    if type(value) is not int or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _matches(window: EVChargingWindow, start: datetime, end: datetime) -> bool:
    begin_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    overnight = window.end_minute <= window.start_minute or window.end_minute == 1440
    expected_end = start.date() + timedelta(days=int(overnight))
    return (
        window.enabled
        and WEEKDAYS[start.weekday()] in window.days
        and begin_minute == window.start_minute
        and end_minute == window.end_minute % 1440
        and end.date() == expected_end
    )


def _reported_interval(
    device: Mapping[str, Any], now: datetime
) -> tuple[datetime | None, datetime | None, str]:
    info = device.get("scheduleInfo")
    if device.get("scheduleTitle") != "NEXT_SCHEDULED_CHARGE" or not isinstance(
        info, Mapping
    ):
        return None, None, "not_reported"
    start, end = _timestamp(info.get("startDate")), _timestamp(info.get("endDate"))
    if start is None or end is None or end <= start:
        return None, None, "invalid_report"
    if start <= now:
        return None, None, "not_future"
    return start, end, "reported"


def _next_interval(
    device: Mapping[str, Any],
    windows: tuple[EVChargingWindow, ...] | None,
    time_zone: tzinfo,
    now: datetime,
) -> tuple[datetime | None, datetime | None, str]:
    if windows is None:
        return None, None, "invalid_configuration"
    if not any(window.enabled for window in windows):
        return None, None, "no_enabled_schedule"
    start, end, reason = _reported_interval(device, now)
    if start is None or end is None:
        return None, None, reason
    local_start, local_end = start.astimezone(time_zone), end.astimezone(time_zone)
    if not any(_matches(window, local_start, local_end) for window in windows):
        return None, None, "does_not_match_enabled_schedule"
    return start, end, "reported"


def parse_ev_schedule(
    device: Mapping[str, Any], *, time_zone: tzinfo, now: datetime | None = None
) -> EVScheduleSnapshot:
    """Read configured windows and validate the next explicitly enabled interval.

    Args:
        device: One EV charger record from authenticated discovery.
        time_zone: Configured site timezone for matching weekly clock fields.
        now: Aware observation time; defaults to current UTC time.

    Returns:
        Only safe schedule fields, without raw trigger or charger identifiers.

    Raises:
        ValueError: If the supplied observation time lacks a timezone.
    """
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None:
        raise ValueError("EV schedule observation time requires a timezone")
    windows = _windows(device.get("deviceTriggers"))
    start, end, reason = _next_interval(device, windows, time_zone, observed)
    return EVScheduleSnapshot(
        windows, start, end, reason, observed.astimezone(UTC), str(time_zone)
    )
