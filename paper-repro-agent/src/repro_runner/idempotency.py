"""Bounded, app-wide idempotency for asynchronous operations."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from threading import Lock
from typing import Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


class IdempotencyConflictError(ValueError):
    """Raised when one key is reused for a different request fingerprint."""


class IdempotencyCapacityError(RuntimeError):
    """Raised when every bounded registry entry is still reserved."""


@dataclass
class _Entry(Generic[T]):
    ready: Future[bool] | None
    fingerprint: str | None = None
    result: T | None = None
    users: int = 0
    verifying: bool = False
    verification_waiters: deque[Future[bool]] = field(default_factory=deque)


@dataclass(frozen=True)
class IdempotencyReservation(Generic[T]):
    """Opaque lease that keeps one registry entry from being evicted."""

    key: str
    entry: _Entry[T]
    owner: bool


class IdempotencyRegistry(Generic[T]):
    """Coordinate one owner per key and retain a bounded app-wide LRU.

    Pending callers synchronize through thread-safe futures, which can be
    awaited from any event loop. Operations run in the owner's request task so
    cancellation cannot leave detached work behind. Request bodies are never
    stored.
    """

    def __init__(self, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._state_lock = Lock()

    def reserve(self, key: str) -> IdempotencyReservation[T]:
        """Reserve a new key or lease its existing pending/completed entry."""
        with self._state_lock:
            entry = self._entries.get(key)
            owner = entry is None
            if entry is None:
                self._make_room()
                if len(self._entries) >= self._max_entries:
                    raise IdempotencyCapacityError
                entry = _Entry(ready=Future())
                self._entries[key] = entry
            entry.users += 1
            self._entries.move_to_end(key)
            return IdempotencyReservation(key=key, entry=entry, owner=owner)

    async def wait(self, reservation: IdempotencyReservation[T]) -> bool:
        """Wait for the current owner; false means the owner aborted."""
        with self._state_lock:
            if self._entries.get(reservation.key) is not reservation.entry:
                return False
            future = reservation.entry.ready
            if future is None:
                return True
        wrapped = asyncio.wrap_future(future, loop=asyncio.get_running_loop())
        return await asyncio.shield(wrapped)

    def complete(
        self,
        reservation: IdempotencyReservation[T],
        fingerprint: str,
        result: T,
    ) -> None:
        """Publish one successful owner result to all current waiters."""
        with self._state_lock:
            entry = self._owned_entry(reservation)
            future = entry.ready
            if future is None:
                raise RuntimeError("idempotency reservation is already complete")
            entry.fingerprint = fingerprint
            entry.result = result
            entry.ready = None
            self._entries.move_to_end(reservation.key)
            future.set_result(True)

    def abort(self, reservation: IdempotencyReservation[T]) -> None:
        """Remove failed/cancelled owner work and wake waiters to retry."""
        with self._state_lock:
            if self._entries.get(reservation.key) is not reservation.entry:
                return
            entry = self._entries.pop(reservation.key)
            if entry.ready is not None:
                entry.ready.set_result(False)

    def replay(
        self, reservation: IdempotencyReservation[T], fingerprint: str
    ) -> T:
        """Return a leased completed result after fingerprint validation."""
        with self._state_lock:
            entry = self._leased_entry(reservation)
            if entry.ready is not None:
                raise RuntimeError("idempotency result is not complete")
            if entry.fingerprint != fingerprint:
                raise IdempotencyConflictError
            self._entries.move_to_end(reservation.key)
            return entry.result  # type: ignore[return-value]

    async def acquire_verification(
        self, reservation: IdempotencyReservation[T]
    ) -> None:
        """Serialize admitted fingerprint checks for followers of one key."""
        with self._state_lock:
            entry = self._leased_entry(reservation)
            if entry.ready is not None:
                raise RuntimeError("idempotency result is not complete")
            if not entry.verifying:
                entry.verifying = True
                return
            future: Future[bool] = Future()
            entry.verification_waiters.append(future)

        wrapped = asyncio.wrap_future(future, loop=asyncio.get_running_loop())
        try:
            await asyncio.shield(wrapped)
        except BaseException:
            with self._state_lock:
                if future in entry.verification_waiters:
                    entry.verification_waiters.remove(future)
                elif future.done() and future.result():
                    self._release_verification(entry)
            raise

    def release_verification(
        self, reservation: IdempotencyReservation[T]
    ) -> None:
        """Hand one completed-result verification lease to the next waiter."""
        with self._state_lock:
            entry = self._leased_entry(reservation)
            if not entry.verifying:
                raise RuntimeError("idempotency verification is not acquired")
            self._release_verification(entry)

    def release(self, reservation: IdempotencyReservation[T]) -> None:
        """Release a lease after its owner or waiter finishes."""
        with self._state_lock:
            if self._entries.get(reservation.key) is reservation.entry:
                reservation.entry.users = max(0, reservation.entry.users - 1)

    async def execute(
        self,
        key: str,
        fingerprint: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Convenience path when callers already know the full fingerprint."""
        while True:
            reservation = self.reserve(key)
            try:
                if reservation.owner:
                    try:
                        result = await operation()
                    except BaseException:
                        self.abort(reservation)
                        raise
                    self.complete(reservation, fingerprint, result)
                    return result
                if await self.wait(reservation):
                    return self.replay(reservation, fingerprint)
            finally:
                self.release(reservation)

    def _make_room(self) -> None:
        while len(self._entries) >= self._max_entries:
            for key, entry in list(self._entries.items()):
                if entry.ready is None and entry.users == 0:
                    self._entries.pop(key)
                    break
            else:
                return

    def _owned_entry(self, reservation: IdempotencyReservation[T]) -> _Entry[T]:
        entry = self._leased_entry(reservation)
        if not reservation.owner:
            raise RuntimeError("idempotency reservation is not the owner")
        return entry

    def _leased_entry(self, reservation: IdempotencyReservation[T]) -> _Entry[T]:
        entry = self._entries.get(reservation.key)
        if entry is not reservation.entry or entry.users < 1:
            raise RuntimeError("idempotency reservation is unavailable")
        return entry

    @staticmethod
    def _release_verification(entry: _Entry[T]) -> None:
        if entry.verification_waiters:
            entry.verification_waiters.popleft().set_result(True)
        else:
            entry.verifying = False
