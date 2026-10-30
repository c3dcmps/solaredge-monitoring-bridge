"""Battery configuration semantics, validation and privacy boundaries."""

from copy import deepcopy
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
MONTHS = (
    "JANUARY FEBRUARY MARCH APRIL MAY JUNE JULY AUGUST "
    "SEPTEMBER OCTOBER NOVEMBER DECEMBER"
).split()
DAYS = "MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY".split()


def _envelope(**values):
    return dict(status="PASSED", httpStatus=200, errorMessages=[], **values)


def _program(*intervals, manual=False):
    segments = []
    for start, end in intervals:
        window = {"from": start, "to": end}
        if manual:
            window["manualTouData"] = {"batteryMode": "CHARGING"}
        segments.append(window)
    return {
        "ownerConfiguration": [
            {
                "months": list(reversed(MONTHS)),
                "daysSegments": [
                    {"days": list(reversed(DAYS)), "hoursSegments": segments}
                ],
            }
        ]
    }


def _mode():
    return _envelope(
        batteryMode="MANUAL_TOU",
        touConfiguration=_program((420, 720), (1020, 1380)),
        manualTouConfiguration={
            "touConfiguration": _program((960, 1020), (300, 375), manual=True)
        },
        lastUpdateDate="2026-10-21T13:04:45.240+00:00",
        token="private-secret",
    )


def _reserve():
    return _envelope(
        storageInfo={
            "userBackupReserveValue": 15,
            "backupMinSOE": 0,
            "portiaBackupReserveViewCapable": True,
            "portiaBackupReserveEditCapable": True,
            "weatherGuardServiceConfiguration": "DISABLED",
            "weatherGuardServiceStatus": "NOT_AVAILABLE_DUE_TO_REGION",
            "serialNumber": "private-secret",
        }
    )


def _parse(mode=None, reserve=None):
    from test_portal_client import _load

    return _load("battery_configuration").parse_battery_configuration(
        mode, reserve, time_zone=ZoneInfo("Europe/Zurich"), now=NOW
    )


def _windows(payload, *, manual=True):
    program = (
        payload["manualTouConfiguration"]["touConfiguration"]
        if manual
        else payload["touConfiguration"]
    )
    return program["ownerConfiguration"][0]["daysSegments"][0]["hoursSegments"]


def test_confirmed_manual_configuration_keeps_inactive_tariff_program_and_reserve():
    snapshot = _parse(_mode(), _reserve())
    assert snapshot.mode == "MANUAL_TOU"
    assert snapshot.reserve_pct == 15
    assert snapshot.manual_count == snapshot.tariff_count == 2
    manual = [slot.as_dict() for slot in snapshot.manual_windows]
    assert [(s["start"], s["end"], s["action"]) for s in manual] == [
        ("16:00", "17:00", "CHARGING"),
        ("05:00", "06:15", "CHARGING"),
    ]
    assert manual[0]["months"] == MONTHS
    assert manual[0]["days"] == DAYS
    assert manual[0]["enabled"] is None
    assert [(s.start_minute, s.end_minute) for s in snapshot.tariff_windows] == [
        (420, 720),
        (1020, 1380),
    ]
    assert snapshot.checked_at == NOW
    assert snapshot.time_zone == "Europe/Zurich"
    assert snapshot.updated_at == datetime(2026, 10, 21, 13, 4, 45, 240000, tzinfo=UTC)
    assert snapshot.reserve.minimum_pct == 0
    assert snapshot.reserve.weather_guard_configuration == "DISABLED"
    assert "private-secret" not in repr(snapshot)


@pytest.mark.parametrize("mode", ["MSC", "TOU", "BACKUP_ONLY", "FUTURE_MODE"])
def test_provider_mode_is_preserved_without_discarding_other_programs(mode):
    payload = _mode()
    payload["batteryMode"] = mode
    snapshot = _parse(payload, _reserve())
    assert snapshot.mode == mode
    assert snapshot.manual_count == snapshot.tariff_count == 2


def test_each_endpoint_can_fail_without_losing_the_other():
    assert _parse(None, _reserve()).reserve_pct == 15
    assert _parse(_mode(), None).manual_count == 2
    assert _parse(None, _reserve()).mode is None
    assert _parse(_mode(), None).reserve_pct is None
    assert _parse().manual_count is None


def test_explicit_empty_program_is_zero_and_missing_program_is_unavailable():
    payload = _mode()
    payload["touConfiguration"] = {"ownerConfiguration": []}
    del payload["manualTouConfiguration"]
    snapshot = _parse(payload, _reserve())
    assert snapshot.tariff_count == 0
    assert snapshot.manual_count is None


@pytest.mark.parametrize("bad", [True, "15", -1, 101, float("nan"), float("inf"), None])
def test_invalid_reserve_is_not_coerced_or_replaced_with_minimum(bad):
    payload = _reserve()
    payload["storageInfo"]["userBackupReserveValue"] = bad
    assert _parse(_mode(), payload).reserve_pct is None


@pytest.mark.parametrize("value", [0, 100, 15.5])
def test_reserve_boundaries_and_fractional_percentage(value):
    payload = _reserve()
    payload["storageInfo"]["userBackupReserveValue"] = value
    assert _parse(None, payload).reserve_pct == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "FAILED"),
        ("httpStatus", 403),
        ("httpStatus", True),
        ("errorMessages", ["private-secret"]),
    ],
)
def test_invalid_provider_envelope_does_not_publish_settings(field, value):
    mode, reserve = _mode(), _reserve()
    mode[field] = reserve[field] = value
    snapshot = _parse(mode, reserve)
    assert snapshot.mode is snapshot.manual_count is snapshot.reserve_pct is None
    assert "private-secret" not in repr(snapshot)


@pytest.mark.parametrize("start,end", [(1380, 60), (0, 1440)])
def test_midnight_clocks_are_retained_without_predicting_future_occurrences(start, end):
    payload = _mode()
    _windows(payload)[0].update({"from": start, "to": end})
    slot = _parse(payload).manual_windows[0].as_dict()
    assert slot["crosses_midnight"]
    assert slot["end"] in {"01:00", "24:00"}


@pytest.mark.parametrize(
    "change",
    [
        {"from": True},
        {"to": "60"},
        {"from": -1},
        {"to": 1441},
        {"from": 1440},
        {"from": 960, "to": 960},
        {"manualTouData": {"batteryMode": "private-secret"}},
        {"enable": "false"},
    ],
)
def test_malformed_manual_window_invalidates_only_manual_program(change):
    payload = _mode()
    _windows(payload)[0].update(change)
    snapshot = _parse(payload, _reserve())
    assert snapshot.manual_count is None
    assert snapshot.tariff_count == 2
    assert snapshot.mode == "MANUAL_TOU"


def test_disabled_window_stays_visible_and_has_no_invented_activation():
    payload = _mode()
    _windows(payload)[0]["enable"] = False
    slots = _parse(payload).manual_windows
    assert len(slots) == 2
    assert slots[0].enabled is False
    assert slots[1].enabled is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(months=["BAD"]),
        lambda c: c.update(months=[]),
        lambda c: c["daysSegments"][0].update(days=["BAD"]),
        lambda c: c["daysSegments"][0].update(days=[]),
        lambda c: c["daysSegments"][0].update(hoursSegments="bad"),
    ],
)
def test_invalid_calendar_is_unavailable(mutate):
    payload = _mode()
    configuration = payload["manualTouConfiguration"]["touConfiguration"][
        "ownerConfiguration"
    ][0]
    mutate(configuration)
    assert _parse(payload).manual_count is None


def test_unbounded_program_and_invalid_mode_or_timestamps_are_not_published():
    payload = _mode()
    _windows(payload).extend([deepcopy(_windows(payload)[0])] * 200)
    payload["batteryMode"] = "private-secret"
    payload["lastUpdateDate"] = "not-a-date"
    snapshot = _parse(payload)
    assert snapshot.manual_count is snapshot.mode is snapshot.updated_at is None
    assert snapshot.tariff_count == 2
