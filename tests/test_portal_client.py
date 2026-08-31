"""Tests for the aggregate-only portal snapshot adapter."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solaredge_one_bridge"
PACKAGE = "solaredge_one_bridge"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package


def _load(name: str):
    qualified = f"{PACKAGE}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, COMPONENT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


_load("const")
_load("model")
_load("portal")
adapter = _load("portal_client")


def test_portal_mapping_exposes_only_aggregate_snapshot_contract() -> None:
    flow = {
        "components": {
            "solar_production": {"current_power_w": 5100},
            "consumption": {"current_power_w": 3300},
            "grid": {"current_power_w": 900, "status": "Export"},
            "dc_storage": {
                "current_power_w": 900,
                "status": "Charging",
                "chargeLevel": 74.5,
                "serialNumber": "must-not-propagate",
            },
        },
        "last_update_time": "2026-08-31T12:30:00-07:00",
        "storage_operating_plan": {
            "plan": "Time of Use",
            "is_active": True,
            "block_count": 3,
            "raw_policy": {"must": "not propagate"},
        },
        "site_id": "must-not-propagate",
    }
    lifetime = {
        "totals_kwh": {
            "production": 12345.6,
            "consumption": 9876.5,
            "import": 2222.2,
            "export": 4444.4,
        },
        "production_distribution": {"production_to_battery_kwh": 850.1},
        "consumption_distribution": {"consumption_from_battery_kwh": 771.2},
        "account": {"email": "must-not-propagate"},
    }

    payload = adapter._build_snapshot_payload(flow, lifetime, "America/Los_Angeles")

    assert payload["provider"] == "solaredge_monitoring_portal"
    assert payload["observed_at"] == "2026-08-31T19:30:00+00:00"
    assert payload["site"] == {
        "production_power_w": 5100.0,
        "consumption_power_w": 3300.0,
        "grid_import_power_w": 0.0,
        "grid_export_power_w": 900.0,
        "battery_charge_power_w": 900.0,
        "battery_discharge_power_w": 0.0,
        "battery_state_of_energy_pct": 74.5,
        "production_energy_kwh": 12345.6,
        "consumption_energy_kwh": 9876.5,
        "grid_import_energy_kwh": 2222.2,
        "grid_export_energy_kwh": 4444.4,
        "battery_charge_energy_kwh": 850.1,
        "battery_discharge_energy_kwh": 771.2,
        "storage_operating_plan": "Time of Use",
        "storage_operating_plan_active": True,
        "storage_operating_plan_block_count": 3,
    }
    rendered = repr(payload)
    assert "serialNumber" not in rendered
    assert "site_id" not in rendered
    assert "email" not in rendered
    assert "raw_policy" not in rendered


def test_direction_split_never_double_counts_power() -> None:
    base = {
        "components": {
            "grid": {"current_power_w": 1200, "direction": "from_grid"},
            "ac_storage": {"current_power_w": 700, "direction": "discharging"},
        },
        "storage_operating_plan": {},
    }
    payload = adapter._build_snapshot_payload(base, {}, "UTC")
    assert payload["site"]["grid_import_power_w"] == 1200
    assert payload["site"]["grid_export_power_w"] == 0
    assert payload["site"]["battery_charge_power_w"] == 0
    assert payload["site"]["battery_discharge_power_w"] == 700
