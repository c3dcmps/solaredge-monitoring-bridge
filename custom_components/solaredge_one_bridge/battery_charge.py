"""Serialized, durable temporary battery calendar operations, independent of HA."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Protocol

import aiohttp

from custom_components.solaredge_one_bridge.battery_charge_schedule import (
    plan_charge,
    same_configuration,
    temporary_interval_absent,
    writable_configuration,
)
from custom_components.solaredge_one_bridge.portal import SolarEdgePortalError

_RECOVERABLE = (
    SolarEdgePortalError,
    aiohttp.ClientError,
    TimeoutError,
    ValueError,
    OSError,
)


class BatteryChargeError(Exception):
    """Actionable error without raw provider details or stored settings."""


class BatteryProgramClient(Protocol):
    """Fixed battery configuration transport boundary."""

    async def async_read_battery_program(self) -> dict[str, Any]:
        """Return a complete validated provider response."""

    async def async_write_battery_program(self, configuration: dict[str, Any]) -> None:
        """Submit a single configuration write without automatic retries."""


class TemporaryBatteryCharge:
    """Persist intent before writes and remove only an unchanged owned operation."""

    def __init__(
        self,
        client: BatteryProgramClient,
        *,
        save: Callable[[dict[str, Any] | None], Awaitable[None]],
        time_zone: tzinfo,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._save = save
        self._time_zone = time_zone
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self.pending: dict[str, Any] | None = None
        self.issue: str | None = None
        self._invalid_record = False

    def restore(self, record: Any) -> None:
        """Validate persisted intent; block writes rather than discard corrupt state."""
        if record is None:
            return
        try:
            self._validate_record(record)
        except (ValueError, KeyError, TypeError, OverflowError):
            self._invalid_record = True
            self.issue = "invalid_record"
            return
        self.pending = deepcopy(record)
        self.issue = "write_uncertain"

    def _validate_record(self, record: Any) -> None:
        if not isinstance(record, dict) or set(record) != {
            "before",
            "expected",
            "start",
            "end",
        }:
            raise ValueError("Invalid record fields")
        start = datetime.fromisoformat(record["start"])
        end = datetime.fromisoformat(record["end"])
        before, expected = plan_charge(
            {
                **record["before"],
                "status": "PASSED",
                "httpStatus": 200,
                "errorMessages": [],
            },
            start=start,
            end=end,
            now=start - timedelta(seconds=1),
            time_zone=self._time_zone,
        )
        if not (
            same_configuration(before, record["before"])
            and same_configuration(expected, record["expected"])
        ):
            raise ValueError("Invalid stored operation")

    async def schedule(self, *, start: datetime, end: datetime) -> None:
        """Schedule one future window, preserving settings and recording ownership.

        Args:
            start: Aware future datetime aligned to a quarter hour.
            end: Aware end datetime on the same local day, within 24 hours.

        Raises:
            BatteryChargeError: If validation, persistence or confirmation fails.
        """
        async with self._lock:
            if self.pending is not None or self._invalid_record:
                raise BatteryChargeError(
                    "Resolve the existing temporary charge before adding another"
                )
            try:
                before, expected = plan_charge(
                    await self._client.async_read_battery_program(),
                    start=start,
                    end=end,
                    now=self._now(),
                    time_zone=self._time_zone,
                )
            except _RECOVERABLE:
                raise BatteryChargeError(
                    "Cannot plan charge: check connection, MANUAL_TOU, quarter hours, "
                    "same-day interval, capacity and overlapping windows"
                ) from None
            record = {
                "before": before,
                "expected": expected,
                "start": start.astimezone(UTC).isoformat(),
                "end": end.astimezone(UTC).isoformat(),
            }
            try:
                await self._save(record)
            except _RECOVERABLE:
                raise BatteryChargeError(
                    "Cannot persist charge intent; no command sent"
                ) from None
            self.pending = record
            try:
                if start <= self._now():
                    raise ValueError("Start elapsed while preparing operation")
                await self._client.async_write_battery_program(expected)
                current = writable_configuration(
                    await self._client.async_read_battery_program()
                )
                if not same_configuration(current, expected):
                    raise ValueError("Write not reflected by fresh read")
            except _RECOVERABLE:
                self.issue = "write_uncertain"
                raise BatteryChargeError(
                    "Cannot confirm addition; do not repeat it. Check the "
                    "app or cancel the temporary charge"
                ) from None
            self.issue = None

    async def cancel(self) -> None:
        """Remove the temporary window; never overwrite a conflicting app change."""
        async with self._lock:
            if self._invalid_record:
                raise BatteryChargeError(
                    "Stored operation is invalid; inspect the app and "
                    "integration storage"
                )
            if self.pending is None:
                return
            try:
                await self._cleanup()
            except _RECOVERABLE:
                self.issue = "cleanup_pending"
                raise BatteryChargeError(
                    "Removal not confirmed; retained for retry. Check "
                    "connection and the app"
                ) from None

    async def recover(self) -> None:
        """Retry overdue cleanup without repeating addition."""
        async with self._lock:
            if self._invalid_record or self.pending is None:
                return
            deadline = datetime.fromisoformat(self.pending["end"]) + timedelta(
                minutes=2
            )
            if self._now() < deadline:
                await self._reconcile_future()
                return
            try:
                await self._cleanup()
            except BatteryChargeError:
                # _cleanup() preserves ownership and marks the app conflict.
                return
            except _RECOVERABLE:
                self.issue = "cleanup_pending"

    def _removed(self, current: dict[str, Any]) -> bool:
        record = self.pending
        if record is None:
            return True
        return same_configuration(
            current, record["before"]
        ) or temporary_interval_absent(
            current,
            start=datetime.fromisoformat(record["start"]),
            end=datetime.fromisoformat(record["end"]),
            time_zone=self._time_zone,
        )

    async def _reconcile_future(self) -> None:
        if self.issue is None or self.pending is None:
            return
        try:
            current = writable_configuration(
                await self._client.async_read_battery_program()
            )
            if same_configuration(current, self.pending["expected"]):
                self.issue = None
            elif self._removed(current):
                await self._save(None)
                self.pending = None
                self.issue = None
            else:
                self.issue = "conflict"
        except _RECOVERABLE:
            # Keep the unresolved warning and durable intent until fresh verification.
            return

    async def _cleanup(self) -> None:
        record = self.pending
        if record is None:
            return
        current = writable_configuration(
            await self._client.async_read_battery_program()
        )
        if not self._removed(current):
            if not same_configuration(current, record["expected"]):
                self.issue = "conflict"
                raise BatteryChargeError(
                    "Battery settings changed in the app; removal refused. "
                    "Remove only the temporary window in the app, "
                    "then retry cancellation"
                )
            await self._client.async_write_battery_program(record["before"])
            current = writable_configuration(
                await self._client.async_read_battery_program()
            )
            if not self._removed(current):
                raise ValueError("Removal not confirmed")
        await self._save(None)
        self.pending = None
        self.issue = None
