"""Deliver outbox rows to Home Assistant, ntfy, and signed generic webhooks."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.config import Settings, get_settings
from mediatracker.core.public_url import effective_public_base_url
from mediatracker.db.models import Event, Media, Setting, Webhook, WebhookDelivery
from mediatracker.events.catalog import GROUPABLE_EVENTS
from mediatracker.events.channels import for_hook
from mediatracker.events.scrub import scrub_payload
from mediatracker.security.ssrf import OutboundUrlRejected, enforce_outbound_request_url, settings_rejection_message
from mediatracker.settings_int import int_setting as _int_setting

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
)


class Posted:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


def post_request(url: str, body: bytes, headers: dict[str, str]) -> int:
    """HTTP POST. Tests replace this function. The body is never logged."""
    enforce_outbound_request_url(url)
    response = httpx.post(url, content=body, headers=headers, timeout=10.0)
    return response.status_code


def schedule_deliveries(
    db: Session,
    event: Event,
    now: datetime,
    settings: Settings | None = None,
) -> None:
    if event.media_id is not None and event.type != "disk.low":
        media = db.get(Media, event.media_id)
        if media is not None and media.on_hold:
            return
    cfg = settings or get_settings()
    hooks = list(db.scalars(select(Webhook).where(Webhook.enabled.is_(True))).all())
    window = _int_setting(db, "notify_group_seconds", cfg.notify_group_seconds)
    quiet_start = _text_setting(db, "notify_quiet_start")
    quiet_end = _text_setting(db, "notify_quiet_end")
    zone = _text_setting(db, "notify_timezone") or "Europe/Paris"
    for hook in hooks:
        # disk.low is operational: every enabled target hears it, even an older subscription.
        _always = {"disk.low", "update.available", "library.scan_suspended", "system.updated"}
        if event.type not in set(hook.events or []) and event.type not in _always:
            continue
        due = now
        if window > 0 and event.type in GROUPABLE_EVENTS:
            due = now + timedelta(seconds=window)
        deferred = quiet_deadline(due, quiet_start, quiet_end, zone)
        if deferred is not None:
            due = deferred
        _add_delivery(db, hook.id, event.id, due)


def deliver_due(db: Session, settings: Settings, now: datetime | None = None, *, commit: bool = True) -> int:
    """Send pending deliveries whose next attempt has arrived. Returns the number of HTTP calls."""
    moment = now or datetime.now(timezone.utc)
    pending = list(
        db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.status == "pending").order_by(WebhookDelivery.id)
        ).all()
    )
    sent = 0
    seen: set[int] = set()
    notified: set[tuple[str, tuple]] = set()
    notify_window = _int_setting(db, "notify_group_seconds", settings.notify_group_seconds)
    for row in pending:
        if row.id in seen or row.status != "pending":
            continue
        due = _as_utc(row.next_attempt_at)
        if due is not None and due > moment:
            continue
        event = db.get(Event, row.event_id)
        hook = db.get(Webhook, row.webhook_id)
        if event is None or hook is None or not for_hook(hook).is_enabled(hook):
            row.status = "failed"
            row.last_response = "cible absente"
            continue
        batch = [row]
        if event.type in GROUPABLE_EVENTS and notify_window > 0:
            batch = _group(db, row, event, moment, notify_window)
        fresh: list[WebhookDelivery] = []
        for item in batch:
            seen.add(item.id)
            other = event if item.id == row.id else db.get(Event, item.event_id)
            if other is not None and _ha_duplicate(db, hook, other, notified):
                item.status = "sent"
                item.last_response = "doublon"
                continue
            fresh.append(item)
        if not fresh:
            continue
        if _send_batch(db, settings, hook, fresh, moment):
            sent += 1
            if hook.type == "homeassistant":
                for item in fresh:
                    if item.status != "sent":
                        continue
                    other = db.get(Event, item.event_id)
                    if other is not None:
                        notified.add((hook.url, _fingerprint(other)))
    if commit:
        db.commit()
    else:
        db.flush()
    return sent


def send_test(db: Session, settings: Settings, hook: Webhook, secret_key: str) -> tuple[bool, int, str]:
    """One immediate delivery. Quiet hours and the outbox are not involved."""
    payload = {
        "title": "MediaTracker",
        "level": "info",
        "page_url": page_url(effective_public_base_url(db, settings) or "", None),
    }
    try:
        status = _post(db, settings, hook, "notify.test", payload, secret_key)
    except OutboundUrlRejected:
        return False, 0, settings_rejection_message()
    except httpx.HTTPError as exc:
        logger.info("Test webhook %s injoignable (%s).", hook.id, type(exc).__name__)
        return False, 0, _transport_message(exc)
    if 200 <= status < 300:
        hook.failure_count = 0
        db.commit()
        return True, status, f"Notification de test envoyée (HTTP {status})."
    db.commit()
    return False, status, f"La cible a répondu HTTP {status}."


def _transport_message(exc: Exception) -> str:
    """French reason for a failed test POST. The URL is not included."""
    if isinstance(exc, httpx.TimeoutException):
        return "Délai dépassé."
    if isinstance(exc, httpx.ConnectError):
        detail = str(exc).lower()
        if "refused" in detail or "errno 111" in detail:
            return "Connexion refusée."
        return "Connexion impossible."
    return "La cible n'a pas répondu."


def page_url(base: str, media_id: int | None) -> str | None:
    if not base:
        return None
    root = base.rstrip("/")
    if isinstance(media_id, int):
        return f"{root}/medias/{media_id}"
    return root


def quiet_deadline(moment: datetime, start: str, end: str, zone_name: str) -> datetime | None:
    """UTC instant when quiet hours end, if ``moment`` falls inside them."""
    if not start or not end or start == end:
        return None
    try:
        zone = ZoneInfo(zone_name or "Europe/Paris")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Paris")
    local = _as_utc(moment).astimezone(zone)
    start_hour, start_minute = _clock(start)
    end_hour, end_minute = _clock(end)
    if start_hour is None or end_hour is None:
        return None
    start_at = local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_at = local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if start_at < end_at:
        if not (start_at <= local < end_at):
            return None
        return end_at.astimezone(timezone.utc)
    if local >= start_at:
        return (end_at + timedelta(days=1)).astimezone(timezone.utc)
    if local < end_at:
        return end_at.astimezone(timezone.utc)
    return None


def _fingerprint(event: Event) -> tuple:
    """Same episode and same outcome. A second Home Assistant post is a duplicate."""
    payload = event.payload or {}
    error = payload.get("error_code") or payload.get("error") or ""
    item = event.item_id
    if item is None and isinstance(payload.get("item_id"), int):
        item = payload["item_id"]
    media = event.media_id
    if media is None and isinstance(payload.get("media_id"), int):
        media = payload["media_id"]
    return (event.type, media, item, str(error))


def _ha_duplicate(db: Session, hook: Webhook, event: Event, notified: set[tuple[str, tuple]]) -> bool:
    if hook.type != "homeassistant":
        return False
    identity = _fingerprint(event)
    if (hook.url, identity) in notified:
        return True
    stmt = (
        select(Event)
        .join(WebhookDelivery, WebhookDelivery.event_id == Event.id)
        .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
        .where(
            Webhook.type == "homeassistant",
            Webhook.url == hook.url,
            WebhookDelivery.status == "sent",
            Event.type == event.type,
            Event.id != event.id,
        )
    )
    if event.item_id is not None:
        stmt = stmt.where(Event.item_id == event.item_id)
    for other in db.scalars(stmt).all():
        if _fingerprint(other) == identity:
            return True
    return False


def _send_batch(db: Session, settings: Settings, hook: Webhook, rows: list[WebhookDelivery], now: datetime) -> bool:
    events = [db.get(Event, row.event_id) for row in rows]
    events = [event for event in events if event is not None]
    if not events:
        for row in rows:
            row.status = "failed"
            row.last_response = "événement absent"
        return False
    payload = _bundle(db, events, settings)
    try:
        status = _post(db, settings, hook, events[0].type, payload, settings.secret_key)
    except OutboundUrlRejected:
        _fail_rows(hook, rows, "url refusée", now)
        return True
    except httpx.HTTPError:
        logger.info("Webhook %s injoignable.", hook.id)
        _fail_rows(hook, rows, "injoignable", now)
        return True
    if 200 <= status < 300:
        for row in rows:
            row.status = "sent"
            row.attempts += 1
            row.last_response = str(status)
        hook.failure_count = 0
        return True
    _fail_rows(hook, rows, f"HTTP {status}", now)
    return True


def _post(
    db: Session,
    settings: Settings,
    hook: Webhook,
    event_type: str,
    payload: dict[str, Any],
    secret_key: str,
) -> int:
    del settings
    body, headers = _render(db, hook, event_type, payload, secret_key)
    return post_request(hook.url, body, headers)


def _render(
    db: Session,
    hook: Webhook,
    event_type: str,
    payload: dict[str, Any],
    secret_key: str,
) -> tuple[bytes, dict[str, str]]:
    del db
    return for_hook(hook).render(hook, event_type, payload, secret_key)


def _bundle(db: Session, events: list[Event], settings: Settings) -> dict[str, Any]:
    base = effective_public_base_url(db, settings) or ""
    enriched = [_enrich(event, base) for event in events]
    if len(enriched) == 1:
        return enriched[0]
    first = enriched[0]
    items = [_item_view(payload) for payload in enriched]
    return {
        **first,
        "grouped": True,
        "count": len(items),
        "items": items,
        "level": first.get("level") or "info",
    }


def _enrich(event: Event, base: str) -> dict[str, Any]:
    payload = dict(event.payload or {})
    payload["level"] = event.level
    media_id = payload.get("media_id")
    if not isinstance(media_id, int):
        media_id = event.media_id
        payload["media_id"] = media_id
    payload["page_url"] = page_url(base, media_id if isinstance(media_id, int) else None)
    return scrub_payload(payload)


def _item_view(payload: dict[str, Any]) -> dict[str, Any]:
    episode = payload.get("episode") if isinstance(payload.get("episode"), int) else None
    return scrub_payload(
        {
            "media_id": payload.get("media_id"),
            "tmdb_id": payload.get("tmdb_id"),
            "item_id": payload.get("item_id"),
            "season": payload.get("season") if isinstance(payload.get("season"), int) else None,
            "episode": episode,
            "title": payload.get("title"),
            "poster_url": payload.get("poster_url"),
            "url": payload.get("page_url"),
        }
    )


def _group(
    db: Session,
    anchor: WebhookDelivery,
    event: Event,
    now: datetime,
    window: int,
) -> list[WebhookDelivery]:
    start = _as_utc(event.ts) or now
    limit = start + timedelta(seconds=window)
    rows = list(
        db.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == anchor.webhook_id,
                WebhookDelivery.status == "pending",
            )
        ).all()
    )
    grouped = [anchor]
    for row in rows:
        if row.id == anchor.id:
            continue
        other = db.get(Event, row.event_id)
        if other is None or other.type != event.type:
            continue
        stamp = _as_utc(other.ts)
        if stamp is None or stamp < start or stamp > limit:
            continue
        due = _as_utc(row.next_attempt_at)
        if due is not None and due > now + timedelta(seconds=window):
            continue
        grouped.append(row)
    grouped.sort(key=lambda row: row.id)
    return grouped


def _fail_rows(hook: Webhook, rows: list[WebhookDelivery], response: str, now: datetime) -> None:
    permanent = False
    for row in rows:
        row.attempts += 1
        row.last_response = response[:200]
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            permanent = True
        else:
            delay = RETRY_DELAYS[min(row.attempts - 1, len(RETRY_DELAYS) - 1)]
            row.next_attempt_at = now + delay
            row.status = "pending"
    if permanent:
        hook.failure_count += 1


def _add_delivery(db: Session, webhook_id: int, event_id: int, due: datetime) -> None:
    existing = db.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.event_id == event_id,
        )
    )
    if existing is not None:
        return
    row = WebhookDelivery(
        webhook_id=webhook_id,
        event_id=event_id,
        status="pending",
        attempts=0,
        next_attempt_at=due,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return




def _text_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _clock(value: str) -> tuple[int | None, int]:
    parts = value.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None, 0
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        return None, 0
    return hour, minute
