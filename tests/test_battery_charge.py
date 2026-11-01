"""Durable charging commands with external boundaries doubled."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_battery_charge_schedule import END, NOW, START, ZONE
from test_battery_configuration import _mode
from test_portal_client import _load

module = _load("battery_charge")


class Boundary:
    def __init__(self):
        self.remote = _mode()
        self.record = None
        self.clock = NOW
        self.writes = []
        self.fail_read = False
        self.fail_save = False
        self.interrupt = False
        self.reject = False

    async def read(self):
        if self.fail_read:
            raise TimeoutError("private-connection-detail")
        return deepcopy(self.remote)

    async def write(self, configuration):
        assert self.record is not None
        self.writes.append(deepcopy(configuration))
        if not self.reject:
            self.remote = dict(
                status="PASSED",
                httpStatus=200,
                errorMessages=[],
                **deepcopy(configuration),
            )
        if self.interrupt:
            raise TimeoutError("private-response-detail")

    async def save(self, record):
        if self.fail_save:
            raise OSError("private-path")
        self.record = deepcopy(record)

    def manager(self):
        client = SimpleNamespace(
            async_read_battery_program=self.read, async_write_battery_program=self.write
        )
        return module.TemporaryBatteryCharge(
            client, save=self.save, time_zone=ZONE, now=lambda: self.clock
        )


def test_add_persists_before_write_and_cleanup_occurs_after_native_end():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        assert manager.pending is not None and len(b.writes) == 1
        b.clock = END
        await manager.recover()
        assert len(b.writes) == 1
        b.clock = END + timedelta(minutes=2)
        await manager.recover()
        assert b.record is manager.pending is None
        assert len(b.writes) == 2
        assert b.remote["manualTouConfiguration"] == _mode()["manualTouConfiguration"]

    asyncio.run(exercise())


def test_restart_and_offline_cleanup_preserve_intent_and_recover():
    async def exercise():
        b = Boundary()
        await b.manager().schedule(start=START, end=END)
        b.clock = END + timedelta(minutes=3)
        manager = b.manager()
        manager.restore(b.record)
        b.fail_read = True
        await manager.recover()
        assert manager.issue == "cleanup_pending" and b.record
        assert len(b.writes) == 1
        b.fail_read = False
        await manager.recover()
        assert manager.issue is None and b.record is None

    asyncio.run(exercise())


def test_ambiguous_add_is_not_retried_and_can_be_cancelled():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        b.interrupt = True
        with pytest.raises(module.BatteryChargeError, match="confirm") as captured:
            await manager.schedule(start=START, end=END)
        assert "private" not in str(captured.value)
        assert b.record and len(b.writes) == 1
        with pytest.raises(module.BatteryChargeError):
            await manager.schedule(start=START, end=END)
        b.interrupt = False
        await manager.cancel()
        assert b.record is None and len(b.writes) == 2

    asyncio.run(exercise())


def test_conflict_never_overwrites_app_change_and_manual_resolution_clears_record():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        before = deepcopy(b.record["before"])
        b.remote["smartPredictionEnabled"] = True
        with pytest.raises(module.BatteryChargeError, match="changed"):
            await manager.cancel()
        assert len(b.writes) == 1 and b.record and manager.issue == "conflict"
        b.remote = dict(status="PASSED", httpStatus=200, errorMessages=[], **before)
        await manager.cancel()
        assert b.record is None and len(b.writes) == 1

    asyncio.run(exercise())


def test_failed_initial_disk_write_prevents_remote_command():
    async def exercise():
        b = Boundary()
        b.fail_save = True
        manager = b.manager()
        with pytest.raises(module.BatteryChargeError, match="persist"):
            await manager.schedule(start=START, end=END)
        assert b.writes == [] and manager.pending is None

    asyncio.run(exercise())


def test_rejected_command_is_not_repeated_and_original_readback_clears_intent():
    async def exercise():
        b = Boundary()
        b.reject = True
        manager = b.manager()
        with pytest.raises(module.BatteryChargeError):
            await manager.schedule(start=START, end=END)
        assert len(b.writes) == 1
        await manager.cancel()
        assert b.record is None and len(b.writes) == 1

    asyncio.run(exercise())


def test_corrupt_record_blocks_new_commands_without_any_write():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        manager.restore({"end": "garbage"})
        assert manager.issue == "invalid_record"
        with pytest.raises(module.BatteryChargeError):
            await manager.schedule(start=START, end=END)
        await manager.recover()
        assert b.writes == []

    asyncio.run(exercise())


def test_start_cancel_are_serialized_and_second_start_does_not_duplicate():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        outcomes = await asyncio.gather(
            manager.schedule(start=START, end=END),
            manager.schedule(start=START, end=END),
            return_exceptions=True,
        )
        assert (
            len([x for x in outcomes if isinstance(x, module.BatteryChargeError)]) == 1
        )
        assert len(b.writes) == 1
        await manager.cancel()
        assert b.record is None

    asyncio.run(exercise())


def test_interrupted_removal_is_confirmed_on_retry_without_a_second_put():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        b.interrupt = True
        with pytest.raises(module.BatteryChargeError, match="Removal"):
            await manager.cancel()
        assert b.record and len(b.writes) == 2
        b.interrupt = False
        await manager.cancel()
        assert b.record is None and len(b.writes) == 2

    asyncio.run(exercise())


def test_crash_before_addition_clears_intent_without_any_remote_write():
    async def exercise():
        from test_battery_charge_schedule import plan

        b = Boundary()
        before, expected = plan()
        record = {
            "before": before,
            "expected": expected,
            "start": START.isoformat(),
            "end": END.isoformat(),
        }
        b.record = deepcopy(record)
        b.clock = END + timedelta(minutes=3)
        manager = b.manager()
        manager.restore(record)
        await manager.recover()
        assert b.record is None and b.writes == []

    asyncio.run(exercise())


def test_failed_clear_persistence_retains_ownership_and_can_retry_without_rewrite():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        b.fail_save = True
        with pytest.raises(module.BatteryChargeError):
            await manager.cancel()
        assert b.record and manager.pending and len(b.writes) == 2
        b.fail_save = False
        await manager.cancel()
        assert b.record is None and len(b.writes) == 2

    asyncio.run(exercise())


def test_manual_removal_clears_ownership_without_reverting_other_app_changes():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        b.remote["smartPredictionEnabled"] = True
        b.remote["batteryMode"] = "MSC"
        with pytest.raises(module.BatteryChargeError, match="changed"):
            await manager.cancel()
        b.remote["manualTouConfiguration"]["touConfiguration"][
            "ownerConfiguration"
        ].pop()
        await manager.cancel()
        assert manager.pending is None and b.record is None
        assert len(b.writes) == 1
        assert b.remote["smartPredictionEnabled"] is True
        assert b.remote["batteryMode"] == "MSC"

    asyncio.run(exercise())


def test_overdue_recovery_never_cancels_a_new_operation_queued_after_old_cancel():
    async def exercise():
        b = Boundary()
        manager = b.manager()
        await manager.schedule(start=START, end=END)
        b.clock = END + timedelta(minutes=3)
        entered, release = asyncio.Event(), asyncio.Event()
        original = b.read

        async def blocked_read():
            entered.set()
            await release.wait()
            return await original()

        manager._client.async_read_battery_program = blocked_read
        cancel = asyncio.create_task(manager.cancel())
        await entered.wait()
        new = asyncio.create_task(
            manager.schedule(
                start=START + timedelta(days=1), end=END + timedelta(days=1)
            )
        )
        await asyncio.sleep(0)
        recovery = asyncio.create_task(manager.recover())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(cancel, new, recovery)
        assert len(b.writes) == 3
        assert manager.pending is not None

    asyncio.run(exercise())


def test_restored_future_uncertainty_is_verified_before_dismissing_warning():
    async def exercise():
        b = Boundary()
        await b.manager().schedule(start=START, end=END)
        manager = b.manager()
        manager.restore(b.record)
        assert manager.issue == "write_uncertain"
        b.fail_read = True
        await manager.recover()
        assert manager.issue == "write_uncertain" and b.record
        b.fail_read = False
        b.remote["smartPredictionEnabled"] = True
        await manager.recover()
        assert manager.issue == "conflict" and b.record
        b.remote["manualTouConfiguration"]["touConfiguration"][
            "ownerConfiguration"
        ].pop()
        await manager.recover()
        assert manager.issue is None and b.record is None and len(b.writes) == 1

    asyncio.run(exercise())
