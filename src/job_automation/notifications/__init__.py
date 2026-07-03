"""
Centralized notification and event system: every important action in the
application can generate an in-app notification, without the module that
took the action ever importing `NotificationService` directly.

**Only in-app notifications are implemented.** Email/SMS/push exist as
provider *interfaces* (`notification_providers.py`) that raise
`NotImplementedError` — never real sending. See docs/NOTIFICATIONS.md.

- `notification_models.py` — `NotificationType`, `NotificationSeverity`:
  dependency-free value objects, same convention as every other
  `*_models.py` file in this project.
- `events.py` — `EventType`, `Event`: the event-bus payload shape,
  equally dependency-free and with zero knowledge of notifications.
- `event_bus.py` — `EventBus`, and the shared `event_bus` singleton every
  publisher/subscriber uses.
- `notification_repository.py` — `NotificationRepository`: pure data
  access for `Notification` rows.
- `notification_providers.py` — `NotificationProvider` interface,
  `InAppNotificationProvider` (real), `Email`/`SMS`/`PushNotificationProvider`
  (placeholders — raise `NotImplementedError`).
- `notification_service.py` — `NotificationService`: the orchestrator
  (create/mark_read/mark_all_read/unread_count/list_notifications).
- `notification_listeners.py` — `register_notification_listeners()`:
  subscribes event-to-notification handlers to the bus. The only module
  that imports both `events.py` and `notification_service.py`.

**Listener registration happens automatically on import of this package**
(see the bottom of this file) — every publisher (`scheduler_service.py`,
`document_service.py`, `status_manager.py`, `auth_service.py`) already
imports `event_bus` from `job_automation.notifications.event_bus`, and
importing any submodule of a package always executes that package's
`__init__.py` first. This guarantees the shared `event_bus` singleton has
its listeners wired up the moment anything in the app publishes to it —
including standalone scripts (`scripts/seed_demo_data.py`) that never
import `web.app` — rather than depending on `web/app.py` remembering to
call `register_notification_listeners()` itself. `register_notification_listeners()`
is idempotent per bus instance (see its own docstring), so this is safe
even if something else also calls it explicitly (e.g. tests using a fresh
`EventBus()`).
"""

from job_automation.notifications.event_bus import EventBus, event_bus
from job_automation.notifications.events import Event, EventType
from job_automation.notifications.notification_models import NotificationSeverity, NotificationType
from job_automation.notifications.notification_service import NotificationService
from job_automation.notifications.notification_listeners import register_notification_listeners

__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "NotificationService",
    "NotificationSeverity",
    "NotificationType",
    "event_bus",
    "register_notification_listeners",
]

register_notification_listeners(event_bus)
