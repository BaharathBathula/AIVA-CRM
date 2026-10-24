"""Brevo (formerly Sendinblue) API v3 (``api-key`` header).

* People are sent with the contacts import (JSON body, one list).
* Unsubscribes, hard bounces, spam complaints, opens and clicks arrive through a
  marketing webhook AIVA registers on connect (unsigned, so unsubscribes and
  bounces are re-read before AIVA acts), plus a poll of contacts modified since
  the last run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import Adapter, Campaign, Change, Person, UpsertResult, WebhookEvent, normal_email
from app.integrations.providers._em_http import AuthFailed, call, json_of
from app.models.integration_hub import IntegrationConnection

API = "https://api.brevo.com/v3"
LIST_ID = re.compile(r"^\d{1,12}$")
ATTRIBUTE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Brevo(Adapter):
    key = "brevo"
    name = "Brevo"
    batch_size = 500
    unsubscribe_batch = 40
    capabilities = {"webhooks": True, "campaigns": True}
    builtin_fields = (("Email", "email"), ("FIRSTNAME", "first_name"), ("LASTNAME", "last_name"))

    @staticmethod
    def _headers(connection: IntegrationConnection, key: str | None = None) -> dict[str, str]:
        key = key or core.secret(connection, "api_key") or ""
        if not key:
            raise IntegrationError("The Brevo API key is missing. Paste it on the Brevo page.", 409)
        return {"api-key": key, "accept": "application/json"}

    async def _call(self, db: AsyncSession, connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = await call(db, connection, method, f"{API}{path}", headers=self._headers(connection), **kwargs)
        return response.status_code, json_of(response)

    @staticmethod
    def _list(settings: dict[str, Any]) -> int:
        list_id = str(settings.get("list_id") or "")
        if not LIST_ID.match(list_id):
            raise IntegrationError("Choose the Brevo list to sync to.", 409)
        return int(list_id)

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        key = (values.get("api_key") or "").strip()
        if len(key) < 20:
            raise IntegrationError("That doesn't look like a Brevo API key (they start with xkeysib-).", 422)
        probe = IntegrationConnection(provider=self.key)
        try:
            response = await call(None, probe, "GET", f"{API}/account", headers=self._headers(probe, key))  # type: ignore[arg-type]
        except AuthFailed as exc:
            raise IntegrationError("Brevo didn't accept that API key. Create one under SMTP & API → API keys.", 422) from exc
        data = json_of(response)
        return {"label": data.get("companyName") or data.get("email") or "Brevo account", "external_id": data.get("email"), "config": {"account_email": data.get("email")}}

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        _, data = await self._call(db, connection, "GET", "/account")
        return f"Brevo answered: {data.get('companyName') or data.get('email') or 'your account'}."

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(4):
            _, data = await self._call(db, connection, "GET", "/contacts/lists", params={"limit": 50, "offset": page * 50, "sort": "desc"})
            rows = data.get("lists") or []
            out.extend({"id": str(row.get("id")), "name": row.get("name"), "count": row.get("uniqueSubscribers") or row.get("totalSubscribers")} for row in rows if row.get("id"))
            if len(rows) < 50:
                break
        return out

    async def fields(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/contacts/attributes")
        return [
            {"key": row.get("name"), "label": row.get("name")}
            for row in data.get("attributes") or []
            if row.get("category") == "normal" and row.get("name") and row.get("name") not in ("FIRSTNAME", "LASTNAME", "EMAIL")
        ]

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        values = person.values
        attributes: dict[str, Any] = {"FIRSTNAME": str(values.get("first_name") or "")[:255], "LASTNAME": str(values.get("last_name") or "")[:255]}
        for target, source in sorted((settings.get("field_map") or {}).items()):
            if ATTRIBUTE.match(str(target)):
                value = values.get(source)
                attributes[str(target)] = "" if value is None else str(value)[:255]
        return {"email": person.email, "attributes": attributes}

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        list_id = self._list(settings)
        _, data = await self._call(
            db, connection, "POST", "/contacts/import",
            json={"jsonBody": [self.payload(p, settings) for p in people], "listIds": [list_id], "updateExistingContacts": True,
                  "emptyContactsAttributes": False, "disableNotification": True},
        )
        if data.get("processId"):
            core.log(db, connection.organization_id, self.key, "import_queued", f"Sent {len(people)} people to Brevo (import {data['processId']}).", connection=connection)
        return {p.email: UpsertResult(ok=True, status="linked") for p in people}

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        done = 0
        for email in emails:
            status, _ = await self._call(db, connection, "PUT", f"/contacts/{quote(email, safe='')}", json={"emailBlacklisted": True}, ok_statuses=(404,))
            done += status < 400
        return done

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        list_id = self._list(settings)
        started = core.utcnow()
        params: dict[str, Any] = {"limit": min(limit, 1000), "offset": 0, "sort": "asc"}
        if since is not None:
            params["modifiedSince"] = _stamp(since)
        _, data = await self._call(db, connection, "GET", "/contacts", params=params)
        rows = data.get("contacts") or []
        found: list[Change] = []
        for row in rows:
            email = normal_email(row.get("email"))
            if not email:
                continue
            if row.get("emailBlacklisted") or list_id in (row.get("listUnsubscribed") or []):
                found.append(Change(email=email, kind="unsubscribed", at=_parse_time(row.get("modifiedAt"))))
        if len(rows) >= params["limit"]:
            last = _parse_time(rows[-1].get("modifiedAt"))
            return found, last or since
        return found, started - timedelta(minutes=2)

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        list_id = self._list(settings)
        status_code, data = await self._call(db, connection, "GET", f"/contacts/{quote(email, safe='')}", ok_statuses=(404,))
        if status_code == 404:
            return None
        if data.get("emailBlacklisted") or list_id in (data.get("listUnsubscribed") or []):
            status = "unsubscribed"
        else:
            status = "subscribed" if list_id in (data.get("listIds") or []) else "not_in_list"
        activity: list[dict[str, Any]] = []
        hard_bounced = False
        try:
            _, stats = await self._call(db, connection, "GET", f"/contacts/{quote(email, safe='')}/campaignStats")
            for row in stats.get("opened") or []:
                activity.append({"action": "open", "at": row.get("eventTime"), "title": f"Campaign {row.get('campaignId')}"})
            for row in stats.get("clicked") or []:
                for link in row.get("links") or []:
                    activity.append({"action": "click", "at": link.get("eventTime"), "title": f"Campaign {row.get('campaignId')}", "url": link.get("url")})
            hard_bounced = bool(stats.get("hardBounces"))
        except IntegrationError:
            pass
        if hard_bounced and status != "unsubscribed":
            status = "bounced"
        activity.sort(key=lambda a: str(a.get("at") or ""), reverse=True)
        return {"status": status, "lists": [settings.get("list_name") or str(list_id)] if status != "not_in_list" else [], "tags": [], "changed_at": data.get("modifiedAt"), "activity": activity[:8]}

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        _, data = await self._call(
            db, connection, "GET", "/emailCampaigns",
            params={"status": "sent", "limit": 50, "offset": 0, "sort": "desc", "statistics": "globalStats", "startDate": _stamp(since), "endDate": _stamp(core.utcnow())},
        )
        out: list[Campaign] = []
        for row in data.get("campaigns") or []:
            stats = ((row.get("statistics") or {}).get("globalStats")) or {}
            sent = int(stats.get("sent") or stats.get("delivered") or 0)
            opens = int(stats.get("uniqueViews") or 0)
            clicks = int(stats.get("uniqueClicks") or 0)
            out.append(
                Campaign(
                    id=str(row.get("id")), title=str(row.get("name") or row.get("subject") or "Campaign")[:300], status=row.get("status"), sent_at=_parse_time(row.get("sentDate")),
                    stats={"sent": sent, "unique_opens": opens, "open_rate": round(opens / sent, 4) if sent else None, "clicks": clicks,
                           "click_rate": round(clicks / sent, 4) if sent else None, "unsubscribes": stats.get("unsubscriptions"), "subject": row.get("subject")},
                )
            )
        return out

    async def confirm(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> Change | None:
        found = await self.member(db, connection, settings, email)
        if found and found.get("status") in ("unsubscribed", "bounced"):
            return Change(email=email, kind=found["status"])
        return None

    async def register_webhook(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], url: str) -> dict[str, Any] | None:
        _, existing = await self._call(db, connection, "GET", "/webhooks", params={"type": "marketing"})
        for row in existing.get("webhooks") or []:
            if row.get("url") == url and row.get("id"):
                return {"id": str(row["id"])}
        _, data = await self._call(
            db, connection, "POST", "/webhooks",
            json={"url": url, "description": "AIVA CRM", "events": ["unsubscribed", "hardBounce", "spam", "opened", "click"], "type": "marketing"},
        )
        return {"id": str(data["id"])} if data.get("id") else None

    async def remove_webhook(self, db: AsyncSession, connection: IntegrationConnection, info: dict[str, Any]) -> None:
        if re.match(r"^\d{1,12}$", str(info.get("id") or "")):
            await self._call(db, connection, "DELETE", f"/webhooks/{info['id']}", ok_statuses=(404,))

    async def parse_webhook(self, body: bytes, content_type: str, form: list[tuple[str, str]] | None) -> list[WebhookEvent]:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return []
        rows = data if isinstance(data, list) else [data]
        out: list[WebhookEvent] = []
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            kind = {"unsubscribe": "unsubscribed", "unsubscribed": "unsubscribed", "hard_bounce": "bounced", "hardbounce": "bounced", "spam": "complaint",
                    "opened": "open", "unique_opened": "open", "click": "click"}.get(str(row.get("event") or "").lower())
            if not kind:
                continue
            out.append(
                WebhookEvent(kind=kind, email=normal_email(row.get("email")), campaign_id=str(row.get("camp_id") or "") or None,
                             campaign_title=row.get("campaign name") or row.get("campaign_name"), url=row.get("URL") or row.get("url"))
            )
        return out
