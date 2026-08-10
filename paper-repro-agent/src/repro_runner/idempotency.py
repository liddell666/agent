"""Bounded, event-loop-safe idempotency for asynchronous operations."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Awaitable, Callable, Generic, TypeVar
from weakref import WeakKeyDictionary


T = TypeVar("T")


class IdempotencyConflictError(ValueError):
    """Raised when one key is reused for a different request fingerprint."""


class IdempotencyCapacityError(RuntimeError):
    """Raised when every bounded registry entry is still in flight."""


@dataclass
class _Entry(Generic[T]):
    fingerprint: str
    task: asyncio.Task[T] | None = None
    result: T | None = None


@dataclass
class _LoopState(Generic[T]):
    entries: OrderedDict[str, _Entry[T]]


class IdempotencyRegistry(Generic[T]):
    """Join identical in-flight work and retain a bounded LRU of successes.

    Async tasks never cross event-loop boundaries. Completed entries retain only
    the operation result and fingerprint; request bodies are not stored.
    """

    def __init__(self, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._states: WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopState[T]
        ] = WeakKeyDictionary()
        self._state_lock = Lock()

    async def execute(
        self,
        key: str,
        fingerprint: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Return cached/joined work or start one operation for a new key."""
        loop = asyncio.get_running_loop()
        with self._state_lock:
            state = self._states.setdefault(loop, _LoopState(entries=OrderedDict()))
            entry = self._current_entry(state, key)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    raise IdempotencyConflictError
                state.entries.move_to_end(key)
                if entry.task is None:
                    return entry.result  # type: ignore[return-value]
                task = entry.task
            else:
                self._make_room(state)
                if len(state.entries) >= self._max_entries:
                    raise IdempotencyCapacityError
                task = loop.create_task(operation())
                entry = _Entry(fingerprint=fingerprint, task=task)
                state.entries[key] = entry
                task.add_done_callback(
                    lambda completed: self._complete(loop, key, entry, completed)
                )

        return await asyncio.shield(task)

    def _current_entry(
        self, state: _LoopState[T], key: str
    ) -> _Entry[T] | None:
        entry = state.entries.get(key)
        if entry is None or entry.task is None or not entry.task.done():
            return entry
        if entry.task.cancelled() or entry.task.exception() is not None:
            state.entries.pop(key, None)
            return None
        entry.result = entry.task.result()
        entry.task = None
        return entry

    def _make_room(self, state: _LoopState[T]) -> None:
        while len(state.entries) >= self._max_entries:
            evicted = False
            for key in list(state.entries):
                entry = self._current_entry(state, key)
                if entry is None:
                    evicted = True
                    break
                if entry.task is None:
                    state.entries.pop(key, None)
                    evicted = True
                    break
            if not evicted:
                return

    def _complete(
        self,
        loop: asyncio.AbstractEventLoop,
        key: str,
        entry: _Entry[T],
        task: asyncio.Task[T],
    ) -> None:
        with self._state_lock:
            state = self._states.get(loop)
            if state is None or state.entries.get(key) is not entry:
                if not task.cancelled():
                    task.exception()
                return
            if task.cancelled() or task.exception() is not None:
                state.entries.pop(key, None)
                return
            entry.result = task.result()
            entry.task = None
            state.entries.move_to_end(key)
