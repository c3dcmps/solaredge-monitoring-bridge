"""Temporary window validation and exact preservation of provider configuration."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_battery_configuration import DAYS, MONTHS, _mode, _windows
from test_portal_client import _load

planner = _load("battery_charge_schedule")
ZONE = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 1, 19, tzinfo=UTC)
START = datetime(2026, 11, 1, 22, 15, tzinfo=UTC)
END = START + timedelta(minutes=15)


def plan(payload=None, start=START, end=END):
    return planner.plan_charge(
        _mode() if payload is None else payload,
        start=start,
        end=end,
        now=NOW,
        time_zone=ZONE,
    )


def test_plan_preserves_complete_writable_settings_and_uses_all_calendars():
    payload = _mode()
    payload["smartPredictionEnabled"] = None
    payload["touConfiguration"]["touPlan"] = "PEAK_HOURS"
    original = deepcopy(payload)
    before, expected = plan(payload)
    assert payload == original
    assert "token" not in before and "lastUpdateDate" not in before
    assert expected["touConfiguration"] == payload["touConfiguration"]
    assert expected["smartPredictionEnabled"] is None
    owners = expected["manualTouConfiguration"]["touConfiguration"][
        "ownerConfiguration"
    ]
    assert (
        owners[:-1]
        == before["manualTouConfiguration"]["touConfiguration"]["ownerConfiguration"]
    )
    assert owners[-1] == {
        "months": MONTHS,
        "daysSegments": [
            {
                "days": DAYS,
                "hoursSegments": [
                    {
                        "from": 1395,
                        "to": 1410,
                        "manualTouData": {"batteryMode": "CHARGING"},
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "start,end",
    [
        (START.replace(tzinfo=None), END),
        (START, START),
        (START.replace(minute=16), END),
        (START, END.replace(second=1)),
        (NOW, NOW + timedelta(minutes=15)),
        (START, END + timedelta(days=1)),
        (START, END.replace(hour=23, minute=30)),
    ],
)
def test_invalid_or_unverified_intervals_refuse(start, end):
    with pytest.raises(ValueError):
        plan(start=start, end=end)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(batteryMode="MSC"),
        lambda p: p.update(status="FAILED"),
        lambda p: _windows(p)[0].update(enabled=False),
        lambda p: _windows(p)[0].update(to=9999),
        lambda p: _windows(p)[0].update(manualTouData={"batteryMode": "UNKNOWN"}),
    ],
)
def test_unrecognized_or_nonmanual_configuration_refuses(mutation):
    payload = _mode()
    mutation(payload)
    with pytest.raises(ValueError):
        plan(payload)


def test_capacity_and_overlap_on_other_day_or_month_refuse():
    payload = _mode()
    _windows(payload).append(deepcopy(_windows(payload)[0]))
    with pytest.raises(ValueError, match="three"):
        plan(payload)
    payload = _mode()
    owner = payload["manualTouConfiguration"]["touConfiguration"]["ownerConfiguration"][
        0
    ]
    owner["months"] = ["DECEMBER"]
    owner["daysSegments"][0]["days"] = ["MONDAY"]
    _windows(payload)[0].update({"from": 1395, "to": 1410})
    with pytest.raises(ValueError, match="overlap"):
        plan(payload)


def test_comparison_ignores_only_known_calendar_order():
    before, expected = plan()
    reordered = deepcopy(expected)
    program = reordered["manualTouConfiguration"]["touConfiguration"][
        "ownerConfiguration"
    ]
    program.reverse()
    for owner in program:
        owner["months"].reverse()
        for segment in owner["daysSegments"]:
            segment["days"].reverse()
            segment["hoursSegments"].reverse()
    assert planner.same_configuration(reordered, expected)
    assert not planner.same_configuration(before, expected)
    expected["touConfiguration"]["orderedUnknown"] = [1, 2]
    reordered = deepcopy(expected)
    reordered["touConfiguration"]["orderedUnknown"].reverse()
    assert not planner.same_configuration(reordered, expected)


def test_dst_ambiguous_local_boundary_refuses():
    start = datetime(2026, 10, 25, 0, 15, tzinfo=UTC)
    with pytest.raises(ValueError, match="ambiguous"):
        planner.plan_charge(
            _mode(),
            start=start,
            end=start + timedelta(minutes=15),
            now=start - timedelta(hours=1),
            time_zone=ZONE,
        )


def test_boolean_and_numeric_provider_values_are_not_equivalent():
    before, _ = plan()
    before["smartPredictionEnabled"] = False
    changed = deepcopy(before)
    changed["smartPredictionEnabled"] = 0
    assert not planner.same_configuration(before, changed)


def test_tomorrow_interval_that_would_also_run_today_across_dst_is_refused():
    now = datetime(2027, 3, 27, 9, 30, tzinfo=UTC)
    start = datetime(2027, 3, 28, 8, 30, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    with pytest.raises(ValueError, match="earlier"):
        planner.plan_charge(_mode(), start=start, end=end, now=now, time_zone=ZONE)


@pytest.mark.parametrize(
    "field",
    [
        "batteryMode",
        "manualTouConfiguration",
        "touConfiguration",
        "smartPredictionEnabled",
    ],
)
def test_missing_writable_field_refuses_partial_put(field):
    payload = _mode()
    payload["smartPredictionEnabled"] = None
    payload.pop(field)
    with pytest.raises(ValueError, match="[Cc]omplete"):
        plan(payload)
