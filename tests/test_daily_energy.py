"""Daily totals, calendar-scoped caching and HA sensor behavior."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from test_ev_entities import COMPONENT, _CoordinatorEntity
from test_portal_client import adapter

ZONE = ZoneInfo("Europe/Zurich")
DAILY_VALUES = {
    "production_energy_today_kwh": 12.3,
    "consumption_energy_today_kwh": 9.8,
    "grid_import_energy_today_kwh": 2.4,
    "grid_to_home_energy_today_kwh": 1.4,
    "grid_export_energy_today_kwh": 4.9,
    "battery_charge_energy_today_kwh": 3.2,
    "battery_discharge_energy_today_kwh": 2.7,
}


def _summary(selected: date, *, production=12.3):
    return {
        "start_date": selected.isoformat(),
        "end_date": selected.isoformat(),
        "totals_kwh": {
            "production": production,
            "consumption": 9.8,
            "import": 2.4,
            "export": 4.9,
        },
        "production_distribution": {"storage_kwh": 3.2},
        "consumption_distribution": {"storage_kwh": 2.7, "grid_kwh": 1.4},
        "private": "must-not-propagate",
    }


def _clock(monkeypatch, start):
    clock = SimpleNamespace(now=start, elapsed=0.0)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz)

    monkeypatch.setattr(adapter, "datetime", ClockDateTime)
    monkeypatch.setitem(adapter.parse_snapshot.__globals__, "datetime", ClockDateTime)
    monkeypatch.setattr(
        adapter, "time", SimpleNamespace(monotonic=lambda: clock.elapsed)
    )
    return clock


def _client(clock, responses):
    client = adapter.SolarEdgePortalSnapshotClient(
        None,
        username="test",
        password="test",
        site_id="1234567",
        time_zone=ZONE,
        timeout_seconds=10,
        max_snapshot_age_seconds=900,
    )

    async def flow():
        return {
            "last_update_time": clock.now.isoformat(),
            "components": {"solar_production": {"current_power_w": 800}},
        }

    client._portal.live_power_flow = flow
    client._portal.lifetime_energy_summary = AsyncMock(
        return_value={"totals_kwh": {"production": 54321}}
    )
    client._portal.current_day_energy_summary = AsyncMock(side_effect=responses)
    return client


def test_daily_totals_are_distinct_from_lifetime_and_cached_for_five_minutes(
    monkeypatch,
):
    async def exercise():
        clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
        client = _client(
            clock,
            [
                _summary(date(2026, 10, 29)),
                _summary(date(2026, 10, 29), production=14.5),
            ],
        )
        first = await client.async_get_snapshot()
        assert {key: first.value(key) for key in DAILY_VALUES} == DAILY_VALUES
        assert first.daily_energy_date == date(2026, 10, 29)
        assert first.value("production_energy_kwh") == 54321
        assert "must-not-propagate" not in repr(first)
        clock.elapsed = 299
        assert (await client.async_get_snapshot()).value(
            "production_energy_today_kwh"
        ) == 12.3
        clock.elapsed = 300
        assert (await client.async_get_snapshot()).value(
            "production_energy_today_kwh"
        ) == 14.5
        assert client._portal.current_day_energy_summary.await_count == 2
        assert client._portal.lifetime_energy_summary.await_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 10, 28, 23, 59, tzinfo=ZONE),
        datetime(2026, 3, 29, 23, 59, tzinfo=ZONE),
        datetime(2026, 10, 25, 23, 59, tzinfo=ZONE),
    ],
)
def test_midnight_invalidates_cache_using_site_date_even_on_dst_days(
    monkeypatch, start
):
    async def exercise():
        clock = _clock(monkeypatch, start)
        tomorrow = start.date() + timedelta(days=1)
        client = _client(
            clock, [_summary(start.date()), _summary(tomorrow, production=0)]
        )
        await client.async_get_snapshot()
        clock.now += timedelta(minutes=2)
        clock.elapsed = 120
        result = await client.async_get_snapshot()
        assert result.daily_energy_date == tomorrow
        assert result.value("production_energy_today_kwh") == 0
        assert client._portal.current_day_energy_summary.call_args.kwargs == {
            "local_date": tomorrow
        }

    asyncio.run(exercise())


def test_request_spanning_midnight_never_exposes_yesterday_as_today(monkeypatch):
    async def exercise():
        start = datetime(2026, 10, 29, 23, 59, 59, tzinfo=ZONE)
        clock = _clock(monkeypatch, start)
        client = _client(clock, [])

        async def daily(*, local_date):
            clock.now += timedelta(seconds=2)
            return _summary(local_date)

        client._portal.current_day_energy_summary = daily
        result = await client.async_get_snapshot()
        assert result.value("production_energy_today_kwh") is None
        assert result.daily_energy_date is None

    asyncio.run(exercise())


def test_daily_failure_preserves_other_metrics_and_recovers_without_stale_values(
    monkeypatch,
):
    async def exercise():
        clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
        client = _client(
            clock,
            [
                _summary(clock.now.date()),
                adapter.SolarEdgePortalError("private-sentinel"),
                _summary(clock.now.date(), production=14.5),
            ],
        )
        await client.async_get_snapshot()
        clock.elapsed = 300
        failed = await client.async_get_snapshot()
        assert failed.value("production_energy_today_kwh") is None
        assert failed.value("production_energy_kwh") == 54321
        assert failed.value("production_power_w") == 800
        assert "private-sentinel" not in repr(failed)
        recovered = await client.async_get_snapshot()
        assert recovered.value("production_energy_today_kwh") == 14.5

    asyncio.run(exercise())


@pytest.mark.parametrize("bad", [None, -1, float("nan"), float("inf"), True, "12.3"])
def test_invalid_daily_metric_is_unavailable_without_hiding_valid_values(
    monkeypatch, bad
):
    async def exercise():
        clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
        client = _client(clock, [_summary(clock.now.date(), production=bad)])
        result = await client.async_get_snapshot()
        assert result.value("production_energy_today_kwh") is None
        assert result.value("consumption_energy_today_kwh") == 9.8

    asyncio.run(exercise())


@dataclass(frozen=True, kw_only=True)
class _Description:
    key: str
    translation_key: str = ""
    device_class: str = ""
    native_unit_of_measurement: str = ""
    state_class: str = ""
    suggested_display_precision: int = 0


def _sensor_module():
    path = COMPONENT / "sensor.py"
    tree = ast.parse(path.read_text())
    nodes = [
        node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *nodes], type_ignores=[])
    )
    namespace = {
        "__name__": __name__,
        "dataclass": dataclass,
        "SensorEntityDescription": _Description,
        "SensorDeviceClass": SimpleNamespace(
            POWER="power", BATTERY="battery", ENERGY="energy"
        ),
        "SensorStateClass": SimpleNamespace(
            MEASUREMENT="measurement",
            TOTAL_INCREASING="total_increasing",
            TOTAL="total",
        ),
        "SensorEntity": type("Sensor", (), {}),
        "CoordinatorEntity": _CoordinatorEntity,
        "SolarEdgeBridgeCoordinator": SimpleNamespace,
        "UnitOfEnergy": SimpleNamespace(KILO_WATT_HOUR="kWh"),
        "UnitOfPower": SimpleNamespace(WATT="W"),
        "PERCENTAGE": "%",
        "DeviceInfo": lambda **kwargs: kwargs,
        "DOMAIN": "solaredge_one_bridge",
        "datetime": datetime,
        "UTC": UTC,
        "date": date,
        "tzinfo": tzinfo,
        "ZoneInfo": ZoneInfo,
        "DAILY_ENERGY_KEYS": adapter.DAILY_ENERGY_KEYS,
        "CONF_MODE": "mode",
        "MODE_PORTAL": "portal",
        "CONF_TIME_ZONE": "time_zone",
        "async_setup_ev_sensors": AsyncMock(),
        "async_setup_ev_schedule_sensors": AsyncMock(),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("mode, expected_daily", [("portal", 7), ("bridge", 0)])
def test_daily_entities_are_portal_only_with_resettable_energy_metadata(
    mode, expected_daily
):
    module = _sensor_module()
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(),
        data={"mode": mode, "time_zone": "Europe/Zurich"},
    )
    entities = []
    asyncio.run(module["async_setup_entry"](None, entry, entities.extend))
    daily = [
        item
        for item in entities
        if getattr(item, "entity_description", SimpleNamespace(key="")).key
        in DAILY_VALUES
    ]
    assert len(daily) == expected_daily
    for item in daily:
        assert item.entity_description.native_unit_of_measurement == "kWh"
        assert item.entity_description.state_class == "total"


def test_daily_sensor_rejects_previous_day_and_exposes_calendar_date(monkeypatch):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    client = _client(clock, [_summary(clock.now.date())])
    snapshot = asyncio.run(client.async_get_snapshot())
    module = _sensor_module()
    module["datetime"] = adapter.datetime
    coordinator = SimpleNamespace(data=snapshot, last_update_success=True)
    description = next(
        item
        for item in module["DAILY_SENSOR_DESCRIPTIONS"]
        if item.key == "production_energy_today_kwh"
    )
    entity = module["SolarEdgeBridgeSensor"](coordinator, description, time_zone=ZONE)
    assert entity.available
    assert entity.native_value == 12.3
    assert entity.extra_state_attributes["date"] == "2026-10-29"
    clock.now += timedelta(days=1)
    assert not entity.available
    assert entity.native_value is None


def test_daily_authentication_failure_is_not_hidden(monkeypatch):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    client = _client(clock, [adapter.SolarEdgePortalAuthenticationError("expired")])
    with pytest.raises(adapter.SolarEdgePortalAuthenticationError):
        asyncio.run(client.async_get_snapshot())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {
            "start_date": "2026-10-28",
            "end_date": "2026-10-28",
            "totals_kwh": {"production": 99},
        },
        {
            "start_date": "2026-10-29",
            "end_date": "2026-10-30",
            "totals_kwh": {"production": 99},
        },
        {
            "start_date": "2026-10-29",
            "end_date": "2026-10-29",
            "totals_kwh": [],
            "production_distribution": False,
        },
    ],
)
def test_missing_malformed_or_wrong_date_daily_data_never_becomes_zero(
    monkeypatch, response
):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    client = _client(clock, [response])
    result = asyncio.run(client.async_get_snapshot())
    assert all(result.value(key) is None for key in DAILY_VALUES)
    assert result.value("production_power_w") == 800


def test_simultaneous_polls_share_one_daily_request(monkeypatch):
    async def exercise():
        clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
        client = _client(clock, [_summary(clock.now.date())])
        snapshots = await asyncio.gather(
            *(client.async_get_snapshot() for _ in range(3))
        )
        assert [item.value("production_energy_today_kwh") for item in snapshots] == [
            12.3
        ] * 3
        assert client._portal.current_day_energy_summary.await_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("bad_date", [None, "2026-02-30", "20261029", "secret", 123])
def test_invalid_daily_calendar_date_keeps_sensor_unavailable(monkeypatch, bad_date):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    snapshot = adapter.parse_snapshot(
        {
            "schema_version": 1,
            "connected": True,
            "observed_at": clock.now.isoformat(),
            "daily_energy_date": bad_date,
            "site": {"production_energy_today_kwh": 12.3},
        }
    )
    module = _sensor_module()
    module["datetime"] = adapter.datetime
    description = next(
        item
        for item in module["DAILY_SENSOR_DESCRIPTIONS"]
        if item.key == "production_energy_today_kwh"
    )
    entity = module["SolarEdgeBridgeSensor"](
        SimpleNamespace(data=snapshot, last_update_success=True),
        description,
        time_zone=ZONE,
    )
    assert snapshot.daily_energy_date is None
    assert not entity.available
    assert entity.native_value is None


def test_daily_data_is_retrieved_on_first_poll_after_restart(monkeypatch):
    async def exercise():
        clock = _clock(monkeypatch, datetime(2026, 10, 29, 15, tzinfo=ZONE))
        first = await _client(clock, [_summary(clock.now.date())]).async_get_snapshot()
        restarted = await _client(
            clock, [_summary(clock.now.date())]
        ).async_get_snapshot()
        assert first.value("production_energy_today_kwh") == 12.3
        assert restarted.value("production_energy_today_kwh") == 12.3

    asyncio.run(exercise())


@pytest.mark.parametrize("grid", [None, -1, float("nan"), True, "4.0"])
def test_missing_or_invalid_grid_to_home_never_falls_back_to_total_import(
    monkeypatch, grid
):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    summary = _summary(clock.now.date())
    summary["consumption_distribution"]["grid_kwh"] = grid
    result = asyncio.run(_client(clock, [summary]).async_get_snapshot())
    assert result.value("grid_to_home_energy_today_kwh") is None
    assert result.value("grid_import_energy_today_kwh") == 2.4


@pytest.mark.parametrize("field", ["grid_kwh", "consumption_from_grid_kwh"])
def test_grid_to_home_is_direct_consumption_not_billed_import(monkeypatch, field):
    clock = _clock(monkeypatch, datetime(2026, 10, 29, 12, tzinfo=ZONE))
    summary = _summary(clock.now.date())
    summary["totals_kwh"]["import"] = 10.2
    summary["consumption_distribution"] = {field: 4.0}
    result = asyncio.run(_client(clock, [summary]).async_get_snapshot())
    assert result.value("grid_import_energy_today_kwh") == 10.2
    assert result.value("grid_to_home_energy_today_kwh") == 4.0


@pytest.mark.parametrize(
    "start, previous_reset, next_reset",
    [
        (
            datetime(2026, 10, 29, 23, 59, tzinfo=ZONE),
            "2026-10-28T23:00:00+00:00",
            "2026-10-29T23:00:00+00:00",
        ),
        (
            datetime(2026, 3, 29, 23, 59, tzinfo=ZONE),
            "2026-03-28T23:00:00+00:00",
            "2026-03-29T22:00:00+00:00",
        ),
        (
            datetime(2026, 10, 25, 23, 59, tzinfo=ZONE),
            "2026-10-24T22:00:00+00:00",
            "2026-10-25T23:00:00+00:00",
        ),
    ],
)
def test_known_reset_survives_missing_midnight_reading_and_higher_next_total(
    monkeypatch, start, previous_reset, next_reset
):
    async def exercise():
        clock = _clock(monkeypatch, start)
        tomorrow = start.date() + timedelta(days=1)
        client = _client(
            clock,
            [_summary(start.date(), production=1), _summary(tomorrow, production=2)],
        )
        module = _sensor_module()
        module["datetime"] = adapter.datetime
        coordinator = SimpleNamespace(
            data=await client.async_get_snapshot(), last_update_success=True
        )
        description = next(
            item
            for item in module["DAILY_SENSOR_DESCRIPTIONS"]
            if item.key == "production_energy_today_kwh"
        )
        entity = module["SolarEdgeBridgeSensor"](
            coordinator, description, time_zone=ZONE
        )
        assert entity.native_value == 1
        assert entity.last_reset.isoformat() == previous_reset
        clock.now += timedelta(hours=2)
        clock.elapsed += 7200
        coordinator.data = await client.async_get_snapshot()
        assert entity.native_value == 2
        assert entity.last_reset.isoformat() == next_reset

    asyncio.run(exercise())
