"""Bounded read-only battery settings, separate from measured battery behavior."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

from custom_components.solaredge_one_bridge.ev_schedule import WEEKDAYS

MONTHS = (
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
)
_ENUM = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_MAX_SEGMENTS = 128


@dataclass(frozen=True, slots=True, kw_only=True)
class BatteryProgramWindow:
    """One configured window, including optional explicit activation."""

    months: tuple[str, ...]
    days: tuple[str, ...]
    start_minute: int
    end_minute: int
    action: str | None
    enabled: bool | None

    def as_dict(self) -> dict[str, Any]:
        """Return normalized calendar fields without provider identifiers."""
        return {
            "months": list(self.months),
            "days": list(self.days),
            "start": f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}",
            "end": f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}",
            "action": self.action,
            "enabled": self.enabled,
            "crosses_midnight": self.end_minute < self.start_minute
            or self.end_minute == 1440,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class BatteryReserveConfiguration:
    """Reserve setpoint and explicit provider capability/Weather Guard metadata."""

    percentage: float | None
    minimum_pct: float | None
    view_capable: bool | None
    edit_capable: bool | None
    weather_guard_configuration: str | None
    weather_guard_status: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BatteryConfigurationSnapshot:
    """Independent settings endpoints with no inferred physical operating state."""

    mode: str | None
    manual_windows: tuple[BatteryProgramWindow, ...] | None
    tariff_windows: tuple[BatteryProgramWindow, ...] | None
    reserve: BatteryReserveConfiguration | None
    checked_at: datetime
    updated_at: datetime | None
    time_zone: str

    @property
    def reserve_pct(self) -> float | None:
        """Return the requested reserve, never substitute the minimum or SoC."""
        return self.reserve.percentage if self.reserve else None

    @property
    def manual_count(self) -> int | None:
        """Count configured manual windows, retaining disabled entries."""
        return len(self.manual_windows) if self.manual_windows is not None else None

    @property
    def tariff_count(self) -> int | None:
        """Count stored tariff windows independently of the selected mode."""
        return len(self.tariff_windows) if self.tariff_windows is not None else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _validated_payload(value: object) -> Mapping[str, Any]:
    payload = _mapping(value)
    if (
        payload.get("status") != "PASSED"
        or type(payload.get("httpStatus")) is not int
        or payload.get("httpStatus") != 200
        or payload.get("errorMessages") != []
    ):
        return {}
    return payload


def _label(value: object) -> str | None:
    return value if isinstance(value, str) and _ENUM.fullmatch(value) else None


def _percentage(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not 0 <= value <= 100:
        return None
    return float(value) if math.isfinite(value) else None


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _calendar(value: object, allowed: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= len(allowed):
        raise ValueError("Battery schedule calendar is invalid")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise ValueError("Battery schedule calendar entry is invalid")
    return tuple(item for item in allowed if item in value)


def _segments(value: object) -> list[Any]:
    if not isinstance(value, list) or len(value) > _MAX_SEGMENTS:
        raise ValueError("Battery schedule segments are invalid")
    return value


def _window(
    raw: object, *, months: tuple[str, ...], days: tuple[str, ...], manual: bool
) -> BatteryProgramWindow:
    value = _mapping(raw)
    start, end = value.get("from"), value.get("to")
    if type(start) is not int or type(end) is not int:
        raise ValueError("Battery schedule clocks are invalid")
    if not 0 <= start < 1440 or not 0 <= end <= 1440 or start == end:
        raise ValueError("Battery schedule interval is invalid")
    enabled = value.get("enable")
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("Battery schedule activation is invalid")
    action = _mapping(value.get("manualTouData")).get("batteryMode") if manual else None
    if manual and action not in ("CHARGING", "DISCHARGING", "PAUSE"):
        raise ValueError("Battery manual action is unsupported")
    return BatteryProgramWindow(
        months=months,
        days=days,
        start_minute=start,
        end_minute=end,
        action=action,
        enabled=enabled,
    )


def _configuration_windows(
    configuration: object, *, manual: bool
) -> tuple[BatteryProgramWindow, ...] | None:
    try:
        owners = _segments(_mapping(configuration).get("ownerConfiguration"))
        windows = []
        for owner in owners:
            months = _calendar(_mapping(owner).get("months"), MONTHS)
            for segment in _segments(_mapping(owner).get("daysSegments")):
                days = _calendar(_mapping(segment).get("days"), WEEKDAYS)
                for raw in _segments(_mapping(segment).get("hoursSegments")):
                    windows.append(
                        _window(raw, months=months, days=days, manual=manual)
                    )
                    if len(windows) > _MAX_SEGMENTS:
                        raise ValueError("Too many battery schedule windows")
        return tuple(windows)
    except ValueError:
        return None


def _reserve_configuration(
    payload: Mapping[str, Any],
) -> BatteryReserveConfiguration | None:
    info = payload.get("storageInfo")
    if not isinstance(info, Mapping):
        return None
    return BatteryReserveConfiguration(
        percentage=_percentage(info.get("userBackupReserveValue")),
        minimum_pct=_percentage(info.get("backupMinSOE")),
        view_capable=_bool(info.get("portiaBackupReserveViewCapable")),
        edit_capable=_bool(info.get("portiaBackupReserveEditCapable")),
        weather_guard_configuration=_label(
            info.get("weatherGuardServiceConfiguration")
        ),
        weather_guard_status=_label(info.get("weatherGuardServiceStatus")),
    )


def _updated_at(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def parse_battery_configuration(
    mode_payload: object,
    reserve_payload: object,
    *,
    time_zone: tzinfo,
    now: datetime | None = None,
) -> BatteryConfigurationSnapshot:
    """Validate independent settings responses and discard all unrelated fields.

    Args:
        mode_payload: Response from batteryMode, or None on a failed read.
        reserve_payload: Response from getBatteries, or None on a failed read.
        time_zone: Configured site timezone for interpreting schedule clocks.
        now: Optional aware retrieval timestamp; defaults to the current UTC time.

    Returns:
        A sanitized snapshot; absent or malformed fields remain None.
    """
    mode = _validated_payload(mode_payload)
    reserve = _validated_payload(reserve_payload)
    manual = _mapping(mode.get("manualTouConfiguration"))
    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise ValueError("Battery settings retrieval timestamp must include timezone")
    return BatteryConfigurationSnapshot(
        mode=_label(mode.get("batteryMode")),
        manual_windows=_configuration_windows(
            manual.get("touConfiguration"), manual=True
        ),
        tariff_windows=_configuration_windows(
            mode.get("touConfiguration"), manual=False
        ),
        reserve=_reserve_configuration(reserve),
        checked_at=checked_at.astimezone(UTC),
        updated_at=_updated_at(mode.get("lastUpdateDate")),
        time_zone=str(time_zone),
    )
