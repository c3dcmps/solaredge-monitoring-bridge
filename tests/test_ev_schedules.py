"""Read-only schedule semantics, including configured but disabled windows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from test_portal_client import _load, adapter

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Zurich")


def _slot(enabled=True, **changes):
    return {
        "enable": enabled,
        "startTime": 1320,
        "endTime": 360,
        "scheduledDays": ["FRIDAY", "THURSDAY"],
        "smartSaver": False,
        "triggerId": "private-trigger",
        **changes,
    }


def _device(slots=None, **changes):
    return {
        "reporterId": 12345,
        "deviceTriggers": [_slot()] if slots is None else slots,
        "scheduleTitle": "NEXT_SCHEDULED_CHARGE",
        "scheduleInfo": {
            "startDate": 1793307600000,
            "endDate": 1793336400000,
        },
        "password": "private-password",
        **changes,
    }


def _parse(device, *, now=NOW, zone=ZONE):
    return _load("ev_schedule").parse_ev_schedule(device, time_zone=zone, now=now)


def test_configured_disabled_windows_are_retained_and_never_the_next_charge():
    result = _parse(
        _device(
            [
                _slot(False, startTime=1140, endTime=1200),
                _slot(True),
            ]
        )
    )
    assert result.schedule_count == 2
    assert result.enabled_count == 1
    assert result.next_start == datetime(2026, 10, 29, 21, tzinfo=UTC)
    assert result.next_end == datetime(2026, 10, 30, 5, tzinfo=UTC)
    assert result.windows[0].as_dict() == {
        "enabled": False,
        "days": ["THURSDAY", "FRIDAY"],
        "start": "19:00",
        "end": "20:00",
        "crosses_midnight": False,
    }
    assert result.windows[1].as_dict() == {
        "enabled": True,
        "days": ["THURSDAY", "FRIDAY"],
        "start": "22:00",
        "end": "06:00",
        "crosses_midnight": True,
    }
    assert "private" not in repr(result)


@pytest.mark.parametrize("slots", [[], [_slot(False)]])
def test_empty_or_all_disabled_configuration_is_valid_but_has_no_next_window(slots):
    result = _parse(_device(slots))
    assert result.schedule_count == len(slots)
    assert result.enabled_count == 0
    assert result.next_start is None
    assert result.next_end is None
    assert result.next_reason == "no_enabled_schedule"


def test_stale_next_info_for_a_disabled_slot_is_not_a_future_charge():
    device = _device(
        [
            _slot(False),
            _slot(True, startTime=1140, endTime=1200),
        ]
    )
    result = _parse(device)
    assert result.enabled_count == 1
    assert result.next_start is None
    assert result.next_reason == "does_not_match_enabled_schedule"


@pytest.mark.parametrize("bad", [None, {}, "", 0, True])
def test_missing_or_invalid_schedule_list_does_not_become_zero_schedules(bad):
    result = _parse(_device(deviceTriggers=bad))
    assert result.schedule_count is None
    assert result.next_start is None
    assert result.next_reason == "invalid_configuration"


@pytest.mark.parametrize(
    "changes",
    [
        {"enable": 1},
        {"enable": "false"},
        {"startTime": True},
        {"startTime": -1},
        {"startTime": 1440},
        {"endTime": 1441},
        {"endTime": 1320},
        {"endTime": 0.5},
        {"scheduledDays": []},
        {"scheduledDays": ["private-email"]},
        {"scheduledDays": "THURSDAY"},
        {"smartSaver": True},
    ],
)
def test_bad_slot_cannot_hide_behind_a_valid_or_disabled_slot(changes):
    result = _parse(_device([_slot(False), _slot(**changes)]))
    assert result.schedule_count is None
    assert result.next_start is None
    assert "private" not in repr(result)


def test_missing_enabled_flag_is_not_assumed_enabled():
    slot = _slot()
    del slot["enable"]
    assert _parse(_device([slot])).schedule_count is None


@pytest.mark.parametrize(
    "change",
    [
        {"scheduleTitle": "OTHER_TITLE"},
        {"scheduleInfo": None},
        {"scheduleInfo": {"startDate": True, "endDate": 1793336400000}},
        {"scheduleInfo": {"startDate": "1793307600000", "endDate": 1793336400000}},
        {"scheduleInfo": {"startDate": 10**1000, "endDate": 1793336400000}},
        {"scheduleInfo": {"startDate": 1793336400000, "endDate": 1793307600000}},
    ],
)
def test_untrusted_next_interval_never_overrides_valid_configuration(change):
    result = _parse(_device(**change))
    assert result.schedule_count == 1
    assert result.next_start is None
    assert result.next_end is None


def test_a_past_report_is_not_displayed_as_next_and_is_not_recalculated():
    result = _parse(_device(), now=datetime(2026, 11, 1, tzinfo=UTC))
    assert result.next_start is None
    assert result.next_reason == "not_future"
    assert result.schedule_count == 1


@pytest.mark.parametrize(
    "start,end,days,start_time,end_time",
    [
        ("2026-03-29T00:30:00Z", "2026-03-29T01:30:00Z", ["SUNDAY"], 90, 210),
        ("2026-10-25T00:30:00Z", "2026-10-25T01:45:00Z", ["SUNDAY"], 150, 165),
        ("2026-10-29T21:00:00Z", "2026-10-29T23:00:00Z", ["THURSDAY"], 1320, 1440),
    ],
)
def test_provider_interval_matches_local_clocks_across_dst_and_midnight(
    start, end, days, start_time, end_time
):
    begin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(end.replace("Z", "+00:00"))
    device = _device(
        [_slot(startTime=start_time, endTime=end_time, scheduledDays=days)],
        scheduleInfo={
            "startDate": int(begin.timestamp() * 1000),
            "endDate": int(finish.timestamp() * 1000),
        },
    )
    result = _parse(device, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert result.next_start == begin
    assert result.next_end == finish


def test_schedule_identity_remains_site_scoped_and_duplicate_devices_are_rejected():
    module = _load("ev_charger")
    payload = {"devicesByType": {"EV_CHARGER": [_device()]}}
    result = module.parse_ev_schedules(
        payload, site_id="1234567", time_zone=ZONE, now=NOW
    )
    assert len(result) == 1
    key = next(iter(result))
    assert key == next(iter(module.parse_ev_chargers(payload, site_id="1234567")))
    assert "12345" not in key
    assert "private" not in repr(result)
    second = module.parse_ev_schedules(
        payload, site_id="7654321", time_zone=ZONE, now=NOW
    )
    assert next(iter(second)) != key
    payload["devicesByType"]["EV_CHARGER"].append(_device())
    assert module.parse_ev_schedules(payload, site_id="1234567", time_zone=ZONE) == {}


def test_adapter_reads_existing_get_without_any_control_request():
    async def exercise():
        client = adapter.SolarEdgePortalSnapshotClient(
            None,
            username="test",
            password="test",
            site_id="1234567",
            time_zone=ZONE,
            timeout_seconds=10,
            max_snapshot_age_seconds=900,
        )
        client._portal.ev_chargers = AsyncMock(
            return_value={
                "devicesByType": {"EV_CHARGER": [_device()]},
            }
        )
        client._portal.set_ev_charging = AsyncMock()
        result = await client.async_get_ev_schedules()
        assert next(iter(result.values())).schedule_count == 1
        client._portal.ev_chargers.assert_awaited_once()
        client._portal.set_ev_charging.assert_not_awaited()

    asyncio.run(exercise())
