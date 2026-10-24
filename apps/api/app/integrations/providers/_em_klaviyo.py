"""Klaviyo API (private API key, JSON:API, dated ``revision`` header).

* People become profiles with the bulk profile import and are added to the
  chosen list. Adding to a list doesn't change anyone's email consent; the
  optional "subscribe" mode also subscribes them, skipping anyone Klaviyo says
  unsubscribed or suppressed.
* AIVA's do-not-contact list becomes Klaviyo suppressions; Klaviyo
  unsubscribes and suppressions (bounces, spam complaints) are polled from
  profiles updated since the last run.
* Chosen CRM events (deal won, quote accepted...) are sent as Klaviyo events, so
  they can trigger flows.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import CRM_EVENTS, Adapter, Campaign, Change, Person, UpsertResult, normal_email
from app.integrations.providers._em_http import AuthFailed, call, json_of
from app.models.integration_hub import IntegrationConnection

API = "https://a.klaviyo.com/api"
REVISION = "2025-10-15"
LIST_ID = re.compile(r"^[A-Za-z0-9]{1,40}$")
PROPERTY = re.compile(r"^[A-Za-z0-9_ .\-]{1,100}$")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
SAFE_EMAIL = re.compile(r'^[^"\\,()\s]+$')
SUPPRESSION = {"HARD_BOUNCE": "bounced", "INVALID_EMAIL": "bounced", "SPAM_COMPLAINT": "complaint", "USER_SUPPRESSED": "unsubscribed", "UNSUBSCRIBE": "unsubscribed"}
METRIC_ACTIONS = {"Opened Email": "open", "Clicked Email": "click", "Received Email": "sent", "Bounced Email": "bounce", "Unsubscribed": "unsubscribe", "Marked Email as Spam": "complaint"}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(attributes: dict[str, Any]) -> tuple[str, str]:
    """(status, note) from a profile's ``subscriptions`` additional field."""

    marketing = (((attributes.get("subscriptions") or {}).get("email") or {}).get("marketing")) or {}
    for entry in marketing.get("suppression") or []:
        reason = str((entry or {}).get("reason") or "").upper()
        if reason in SUPPRESSION:
            return SUPPRESSION[reason], reason.replace("_", " ").lower()
    consent = str(marketing.get("consent") or "").upper()
    if consent == "UNSUBSCRIBED":
        return "unsubscribed", ""
    if consent == "SUBSCRIBED":
        return "subscribed", ""
    return "not_subscribed", ""


class Klaviyo(Adapter):
    key = "klaviyo"
    name = "Klaviyo"
    batch_size = 500
    unsubscribe_batch = 100
    capabilities = {"subscribe_mode": True, "events": True, "free_fields": True, "campaigns": True}
    list_required = False
    builtin_fields = (("Email", "email"), ("First name", "first_name"), ("Last name", "last_name"), ("Title", "job_title"), ("Organization", "company"), ("Phone number (+E.164 only)", "phone"))
    list_label = "List"

    @staticmethod
    def _headers(connection: IntegrationConnection, key: str | None = None) -> dict[str, str]:
        key = key or core.secret(connection, "api_key") or ""
        if not key:
            raise IntegrationError("The Klaviyo private API key is missing. Paste it on the Klaviyo page.", 409)
        return {"Authorization": f"Klaviyo-API-Key {key}", "revision": REVISION, "accept": "application/vnd.api+json", "content-type": "application/vnd.api+json"}

    async def _call(self, db: AsyncSession, connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        url = path if path.startswith(f"{API}/") else f"{API}{path}"
        response = await call(db, connection, method, url, headers=self._headers(connection), **kwargs)
        return response.status_code, json_of(response)

    @staticmethod
    def _list(settings: dict[str, Any]) -> str | None:
        list_id = str(settings.get("list_id") or "")
        if list_id and not LIST_ID.match(list_id):
            raise IntegrationError("That isn't a Klaviyo list.", 409)
        return list_id or None

    @staticmethod
    def _next(data: dict[str, Any]) -> str | None:
        link = ((data.get("links") or {}).get("next")) or ""
        return link if isinstance(link, str) and link.startswith(f"{API}/") else None

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        key = (values.get("api_key") or "").strip()
        if not key.startswith("pk_") or len(key) < 20:
            raise IntegrationError("That isn't a Klaviyo private API key (they start with pk_).", 422)
        probe = IntegrationConnection(provider=self.key)
        try:
            response = await call(None, probe, "GET", f"{API}/accounts", headers=self._headers(probe, key))  # type: ignore[arg-type]
        except AuthFailed as exc:
            raise IntegrationError("Klaviyo didn't accept that key. It needs access to profiles, lists, events, campaigns and subscriptions.", 422) from exc
        rows = json_of(response).get("data") or []
        account = rows[0] if rows else {}
        info = ((account.get("attributes") or {}).get("contact_information")) or {}
        return {"label": info.get("organization_name") or info.get("default_sender_email") or "Klaviyo account", "external_id": account.get("id"), "config": {"organization_name": info.get("organization_name")}}

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        _, data = await self._call(db, connection, "GET", "/accounts")
        rows = data.get("data") or []
        info = (((rows[0] if rows else {}).get("attributes") or {}).get("contact_information")) or {}
        return f"Klaviyo answered: {info.get('organization_name') or 'your account'}."

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        path: str | None = "/lists"
        params: dict[str, Any] | None = {"fields[list]": "name"}
        for _page in range(10):
            if path is None:
                break
            _, data = await self._call(db, connection, "GET", path, params=params)
            out.extend({"id": row.get("id"), "name": (row.get("attributes") or {}).get("name"), "count": None} for row in data.get("data") or [] if row.get("id"))
            path, params = self._next(data), None
        return out

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        values = person.values
        attributes: dict[str, Any] = {"email": person.email}
        for target, source in (("first_name", "first_name"), ("last_name", "last_name"), ("title", "job_title"), ("organization", "company")):
            if values.get(source):
                attributes[target] = str(values[source])[:255]
        phone = str(values.get("phone") or "").replace(" ", "")
        if E164.match(phone):
            attributes["phone_number"] = phone
        properties: dict[str, Any] = {"aiva_record_type": values.get("record_type")}
        for target, source in sorted((settings.get("field_map") or {}).items()):
            if PROPERTY.match(str(target)):
                properties[str(target)] = values.get(source)
        attributes["properties"] = properties
        return {"profile": attributes, "list_id": self._list(settings), "mode": settings.get("subscribe_mode")}

    async def _unsubscribed(self, db: AsyncSession, connection: IntegrationConnection, emails: list[str]) -> set[str]:
        """Emails Klaviyo says are unsubscribed or suppressed (checked before subscribing anyone)."""

        blocked: set[str] = set()
        safe = [e for e in emails if SAFE_EMAIL.match(e)]
        for start in range(0, len(safe), 100):
            chunk = safe[start:start + 100]
            quoted = ",".join(f'"{e}"' for e in chunk)
            _, data = await self._call(
                db, connection, "GET", "/profiles",
                params={"filter": f"any(email,[{quoted}])", "additional-fields[profile]": "subscriptions", "fields[profile]": "email,subscriptions", "page[size]": 100},
            )
            for row in data.get("data") or []:
                attributes = row.get("attributes") or {}
                status, _ = _status(attributes)
                if status in ("unsubscribed", "bounced", "complaint"):
                    email = normal_email(attributes.get("email"))
                    if email:
                        blocked.add(email)
        return blocked | {e for e in emails if not SAFE_EMAIL.match(e)}

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        list_id = self._list(settings)
        body: dict[str, Any] = {
            "data": {
                "type": "profile-bulk-import-job",
                "attributes": {"profiles": {"data": [{"type": "profile", "attributes": self.payload(p, settings)["profile"]} for p in people]}},
            }
        }
        if list_id:
            body["data"]["relationships"] = {"lists": {"data": [{"type": "list", "id": list_id}]}}
        _, data = await self._call(db, connection, "POST", "/profile-bulk-import-jobs", json=body)
        job = (data.get("data") or {}).get("id")
        results = {p.email: UpsertResult(ok=True, status="linked") for p in people}
        if settings.get("subscribe_mode") == "subscribe" and list_id:
            blocked = await self._unsubscribed(db, connection, [p.email for p in people])
            for email in blocked:
                results[email] = UpsertResult(ok=True, status="unsubscribed")
            willing = [p.email for p in people if p.email not in blocked]
            for start in range(0, len(willing), 1000):
                chunk = willing[start:start + 1000]
                await self._call(
                    db, connection, "POST", "/profile-subscription-bulk-create-jobs",
                    json={
                        "data": {
                            "type": "profile-subscription-bulk-create-job",
                            "attributes": {
                                "custom_source": "AIVA CRM",
                                "profiles": {"data": [{"type": "profile", "attributes": {"email": e, "subscriptions": {"email": {"marketing": {"consent": "SUBSCRIBED"}}}}} for e in chunk]},
                            },
                            "relationships": {"list": {"data": {"type": "list", "id": list_id}}},
                        }
                    },
                )
        if job:
            core.log(db, connection.organization_id, self.key, "import_queued", f"Sent {len(people)} people to Klaviyo (import job {str(job)[:40]}).", connection=connection)
        return results

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        for start in range(0, len(emails), 100):
            chunk = emails[start:start + 100]
            await self._call(
                db, connection, "POST", "/profile-suppression-bulk-create-jobs",
                json={"data": {"type": "profile-suppression-bulk-create-job", "attributes": {"profiles": {"data": [{"type": "profile", "attributes": {"email": e}} for e in chunk]}}}},
            )
        return len(emails)

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        started = core.utcnow()
        params: dict[str, Any] | None = {
            "sort": "updated", "page[size]": 100, "additional-fields[profile]": "subscriptions", "fields[profile]": "email,updated,subscriptions",
        }
        if since is not None:
            params["filter"] = f"greater-than(updated,{_stamp(since)})"
        path: str | None = "/profiles"
        found: list[Change] = []
        last_seen: datetime | None = None
        for _page in range(max(1, limit // 100)):
            if path is None:
                return found, started - timedelta(minutes=2)
            _, data = await self._call(db, connection, "GET", path, params=params)
            for row in data.get("data") or []:
                attributes = row.get("attributes") or {}
                last_seen = _parse_time(attributes.get("updated")) or last_seen
                status, note = _status(attributes)
                email = normal_email(attributes.get("email"))
                if email and status in ("unsubscribed", "bounced", "complaint"):
                    found.append(Change(email=email, kind=status, at=_parse_time(attributes.get("updated")), note=note))
            path, params = self._next(data), None
        if path is None:
            return found, started - timedelta(minutes=2)
        return found, last_seen or since

    async def _profile(self, db: AsyncSession, connection: IntegrationConnection, email: str) -> dict[str, Any] | None:
        if not SAFE_EMAIL.match(email):
            return None
        _, data = await self._call(
            db, connection, "GET", "/profiles",
            params={"filter": f'equals(email,"{email}")', "additional-fields[profile]": "subscriptions", "page[size]": 1},
        )
        rows = data.get("data") or []
        return rows[0] if rows else None

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        profile = await self._profile(db, connection, email)
        if profile is None or not LIST_ID.match(str(profile.get("id") or "")):
            return None
        attributes = profile.get("attributes") or {}
        status, note = _status(attributes)
        lists: list[str] = []
        activity: list[dict[str, Any]] = []
        try:
            _, data = await self._call(db, connection, "GET", f"/profiles/{profile['id']}/lists", params={"fields[list]": "name"})
            lists = [(row.get("attributes") or {}).get("name") for row in data.get("data") or [] if (row.get("attributes") or {}).get("name")]
            _, events = await self._call(
                db, connection, "GET", "/events",
                params={"filter": f'equals(profile_id,"{profile["id"]}")', "sort": "-datetime", "page[size]": 10, "include": "metric", "fields[metric]": "name"},
            )
            metrics = {row.get("id"): (row.get("attributes") or {}).get("name") for row in events.get("included") or [] if row.get("type") == "metric"}
            for row in events.get("data") or []:
                metric_id = (((row.get("relationships") or {}).get("metric") or {}).get("data") or {}).get("id")
                name = metrics.get(metric_id) or "Event"
                props = (row.get("attributes") or {}).get("event_properties") or {}
                activity.append({"action": METRIC_ACTIONS.get(name, name), "at": (row.get("attributes") or {}).get("datetime"), "title": props.get("Campaign Name") or props.get("Subject") or name, "url": props.get("URL")})
        except IntegrationError:
            pass
        return {"status": status, "status_note": note, "lists": lists[:10], "tags": [], "changed_at": attributes.get("updated"), "activity": activity[:8]}

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        _, data = await self._call(
            db, connection, "GET", "/campaigns",
            params={"filter": f"and(equals(messages.channel,'email'),greater-than(updated_at,{_stamp(since)}))", "sort": "-updated_at", "fields[campaign]": "name,status,send_time,scheduled_at"},
        )
        out: list[Campaign] = []
        for row in data.get("data") or []:
            attributes = row.get("attributes") or {}
            sent_at = _parse_time(attributes.get("send_time") or attributes.get("scheduled_at"))
            if not row.get("id") or str(attributes.get("status") or "").lower() not in ("sent", "sending"):
                continue
            out.append(Campaign(id=str(row["id"]), title=str(attributes.get("name") or "Campaign")[:300], status=str(attributes.get("status") or "").lower(), sent_at=sent_at, stats={}))
        return out[:30]

    async def send_events(self, db: AsyncSession, connection: IntegrationConnection, events: list[dict[str, Any]]) -> int:
        sent = 0
        for event in events:
            body = {
                "data": {
                    "type": "event",
                    "attributes": {
                        "properties": event.get("properties") or {},
                        "metric": {"data": {"type": "metric", "attributes": {"name": CRM_EVENTS[event["event"]]}}},
                        "profile": {"data": {"type": "profile", "attributes": {"email": event["email"]}}},
                        "unique_id": event["unique_id"],
                        "time": _stamp(event["time"]),
                        **({"value": float(event["value"]), "value_currency": event.get("currency") or "USD"} if event.get("value") is not None else {}),
                    },
                }
            }
            await self._call(db, connection, "POST", "/events", json=body)
            sent += 1
        return sent
