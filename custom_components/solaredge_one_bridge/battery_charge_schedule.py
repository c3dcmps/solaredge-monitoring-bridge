"""Pure planning and comparison for the validated temporary charging path."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, tzinfo
from typing import Any

from custom_components.solaredge_one_bridge.battery_configuration import (
    MONTHS,
    parse_battery_configuration,
)
from custom_components.solaredge_one_bridge.ev_schedule import WEEKDAYS

WRITABLE_FIELDS = (
    "batteryMode",
    "manualTouConfiguration",
    "touConfiguration",
    "smartPredictionEnabled",
)
_CALENDAR_LIST = re.compile(
    r"^(?:manualTouConfiguration\.touConfiguration|touConfiguration)"
    r"\.ownerConfiguration(?:\.\d+\.months|\.\d+\.daysSegments"
    r"(?:\.\d+\.days|\.\d+\.hoursSegments)?)?$"
)


def writable_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy writable fields only, rejecting failed provider envelopes."""
    if (
        payload.get("status") != "PASSED"
        or type(payload.get("httpStatus")) is not int
        or payload.get("httpStatus") != 200
        or payload.get("errorMessages") != []
    ):
        raise ValueError("Battery configuration response was not validated")
    if not all(key in payload for key in WRITABLE_FIELDS):
        raise ValueError("Complete battery configuration is required")
    return deepcopy({key: payload[key] for key in WRITABLE_FIELDS})


def _normalized(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _normalized(item, f"{path}.{key}" if path else key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [
            _normalized(item, f"{path}.{index}") for index, item in enumerate(value)
        ]
        return (
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
            if (_CALENDAR_LIST.fullmatch(path))
            else items
        )
    return value


def same_configuration(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Ignore calendar order only; preserve all other differences as conflicts."""
    return json.dumps(_normalized(left), sort_keys=True, allow_nan=False) == json.dumps(
        _normalized(right), sort_keys=True, allow_nan=False
    )


def _local_interval(
    start: datetime, end: datetime, *, now: datetime, time_zone: tzinfo
) -> tuple[int, int]:
    if any(
        value.tzinfo is None or value.utcoffset() is None for value in (start, end, now)
    ):
        raise ValueError("Start and end must include timezone offsets")
    if not now < start < end <= now + timedelta(days=1):
        raise ValueError("Choose a future interval ending within 24 hours")
    local_start, local_end = start.astimezone(time_zone), end.astimezone(time_zone)
    if local_start.date() != local_end.date():
        raise ValueError("Cross-midnight battery charging is not supported")
    for value in (local_start, local_end):
        if value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset():
            raise ValueError("Choose unambiguous local times outside a DST transition")
        if value.minute % 15 or value.second or value.microsecond:
            raise ValueError("Choose exact quarter-hour boundaries")
    _check_earlier_occurrence(local_start, local_end, now.astimezone(time_zone))
    start_minute = local_start.hour * 60 + local_start.minute
    end_minute = local_end.hour * 60 + local_end.minute
    if start_minute >= end_minute:
        raise ValueError("Choose an increasing local-time interval")
    return start_minute, end_minute


def _check_earlier_occurrence(start: datetime, end: datetime, now: datetime) -> None:
    if start.date() > now.date() and now.time().replace(
        tzinfo=None
    ) < end.time().replace(tzinfo=None):
        raise ValueError("This repeating calendar could charge on an earlier date")


def plan_charge(
    payload: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    time_zone: tzinfo,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an all-calendar window while preserving existing writable settings.

    Args:
        payload: Fresh complete batteryMode response.
        start: Aware start datetime in the future.
        end: Aware end datetime on the same site-local day.
        now: Aware current datetime for interval validation.
        time_zone: Site timezone used by the native battery calendar.

    Returns:
        Original and proposed writable configurations.

    Raises:
        ValueError: If settings or an interval cannot be safely changed.
    """
    start_minute, end_minute = _local_interval(start, end, now=now, time_zone=time_zone)
    before = writable_configuration(payload)
    snapshot = parse_battery_configuration(payload, None, time_zone=time_zone, now=now)
    if snapshot.mode != "MANUAL_TOU" or snapshot.manual_windows is None:
        raise ValueError(
            "A valid MANUAL_TOU program is required; no mode change is made"
        )
    if len(snapshot.manual_windows) >= 3:
        raise ValueError("The battery already has three manual windows")
    for window in snapshot.manual_windows:
        if window.enabled is False or window.start_minute >= window.end_minute:
            raise ValueError(
                "Disabled or cross-midnight existing windows are not supported"
            )
        if start_minute < window.end_minute and end_minute > window.start_minute:
            raise ValueError("The temporary interval would overlap an existing window")
    expected = deepcopy(before)
    owners = expected["manualTouConfiguration"]["touConfiguration"][
        "ownerConfiguration"
    ]
    _validate_raw_slots(owners)
    owners.append(
        {
            "months": list(MONTHS),
            "daysSegments": [
                {
                    "days": list(WEEKDAYS),
                    "hoursSegments": [
                        {
                            "from": start_minute,
                            "to": end_minute,
                            "manualTouData": {"batteryMode": "CHARGING"},
                        }
                    ],
                }
            ],
        }
    )
    return before, expected


def _validate_raw_slots(owners: list[dict[str, Any]]) -> None:
    for owner in owners:
        for segment in owner["daysSegments"]:
            for slot in segment["hoursSegments"]:
                if set(slot) - {"from", "to", "manualTouData", "enable"}:
                    raise ValueError("An existing manual window has unsupported fields")


def temporary_interval_absent(
    configuration: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    time_zone: tzinfo,
) -> bool:
    """Confirm manual removal conservatively without reverting other app changes."""
    snapshot = parse_battery_configuration(
        {**configuration, "status": "PASSED", "httpStatus": 200, "errorMessages": []},
        None,
        time_zone=time_zone,
    )
    if snapshot.manual_windows is None:
        return False
    local_start, local_end = start.astimezone(time_zone), end.astimezone(time_zone)
    first = local_start.hour * 60 + local_start.minute
    last = local_end.hour * 60 + local_end.minute
    for slot in snapshot.manual_windows:
        ranges = (
            [(slot.start_minute, slot.end_minute)]
            if slot.start_minute < slot.end_minute
            else [(slot.start_minute, 1440), (0, slot.end_minute)]
        )
        if any(first < right and last > left for left, right in ranges):
            return False
    return True
