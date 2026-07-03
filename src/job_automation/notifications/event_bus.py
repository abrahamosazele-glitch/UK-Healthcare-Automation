"""
A lightweight, synchronous, in-process publish/subscribe dispatcher.

Deliberately minimal — no message broker, no persistence, no async, no
retry queue. This app is one Python process (see docs/BACKGROUND_SCHEDULER
.md's locking design for the same "this is a single process, don't build
distributed-systems machinery" reasoning); an in-memory dispatcher is the
right amount of infrastructure for "future modules publish events instead
of directly calling `NotificationService`."

Handlers receive the triggering `Session` alongside the `Event` — not
because `Event` itself carries any infrastructure, but because a
subscriber that wants to write a `Notification` needs to do so **in the
same transaction** as whatever triggered the event (a workflow transition,
a document generation), so the notification either commits or rolls back
together with the change it's about. See `notification_listeners.py`.

**A subscriber's failure never breaks the publisher.** `publish()` catches
and logs any exception a handler raises rather than letting it propagate —
a bug in notification creation must never fail the actual business
operation (approving a document, matching a job) that published the event.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from sqlalchemy.orm import Session

from job_automation.notifications.events import Event, EventType
from job_automation.utils.logger import logger

EventHandler = Callable[[Event, Session], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event: Event, session: Session) -> None:
        for handler in self._subscribers.get(event.event_type, []):
            try:
                handler(event, session)
            except Exception:
                logger.exception(
                    "Notification handler {!r} failed for event {} — continuing without it",
                    getattr(handler, "__name__", handler),
                    event.event_type.value,
                )

    def clear(self) -> None:
        """Removes every subscription — used by tests to get a clean bus
        between cases, and by `notification_listeners
        .register_notification_listeners()` to stay idempotent if it's
        ever called more than once (e.g. app reload in a dev server)."""
        self._subscribers.clear()


#: One shared bus for the whole app — every publisher and every subscriber
#: (registered via `notification_listeners.register_notification_listeners()`)
#: uses this exact instance, the same singleton-plus-DI pattern already
#: used for `web.app.scheduler_service` / `get_scheduler_service()`.
event_bus = EventBus()
