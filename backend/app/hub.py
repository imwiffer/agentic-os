"""Broadcast hub: pushes server-initiated events (job results, notifications) to every connected dashboard."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

log = logging.getLogger("jarvis.hub")
Sender = Callable[[dict], Awaitable[None]]
_clients: set[Sender] = set()


def register(send: Sender) -> None:
    _clients.add(send)


def unregister(send: Sender) -> None:
    _clients.discard(send)


def client_count() -> int:
    return len(_clients)


async def broadcast(message: dict) -> None:
    for send in list(_clients):
        try:
            await send(message)
        except Exception as exc:  # dead socket; it unregisters itself on disconnect
            log.debug("broadcast failed: %s", exc)
