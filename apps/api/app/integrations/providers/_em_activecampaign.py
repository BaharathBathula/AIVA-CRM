"""ActiveCampaign API v3 (account API URL + ``Api-Token`` header).

* People are sent with the bulk import (up to 250 per call). ActiveCampaign
  skips list subscriptions for people who unsubscribed from that list, so AIVA
  never re-subscribes anyone.
* Unsubscribes, bounces, opens and clicks arrive through a webhook that AIVA
  registers on connect (ActiveCampaign doesn't sign webhooks, so unsubscribes and
  bounces are re-read from the API before AIVA acts), and a poll of the list's
  unsubscribed and bounced contacts.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import Adapter, Campaign, Change, Person, UpsertResult, WebhookEvent, normal_email
from app.integrations.providers._em_http import AuthFailed, call, json_of
from app.models.integration_hub import IntegrationConnection

# https://youraccount.api-us1.com or https://youraccount.activehosted.com
API_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.(api-us\d{1,2}\.com|activehosted\.com)$")
LIST_ID = re.compile(r"^\d{1,12}$")
FIELD_ID = re.compile(r"^\d{1,12}$")


def clean_api_url(value: str) -> str:
    raw = (value or "").strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not API_HOST.match(host) or parts.port not in (None, 443) or parts.username or parts.password:
        raise IntegrationError("That isn't an ActiveCampaign API URL. Copy it from Settings → Developer; it looks like https://youraccount.api-us1.com.", 422)
    return f"https://{host}"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class ActiveCampaign(Adapter):
    key = "activecampaign"
    name = "ActiveCampaign"
    batch_size = 200
    unsubscribe_batch = 200
    capabilities = {"tags": True, "webhooks": True, "campaigns": True}
    builtin_fields = (("Email", "email"), ("First name", "first_name"), ("Last name", "last_name"), ("Phone", "phone"), ("Account name", "company"))

    def _creds(self, connection: IntegrationConnection) -> tuple[str, str]:
        base = clean_api_url((connection.config or {}).get("api_url") or "")
        key = core.secret(connection, "api_key") or ""
        if not key:
            raise IntegrationError("The ActiveCampaign API key is missing. Paste it on the ActiveCampaign page.", 409)
        return base, key

    async def _call(self, db: AsyncSession, connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        base, key = self._creds(connection)
        response = await call(db, connection, method, f"{base}/api/3{path}", headers={"Api-Token": key, "Accept": "application/json"}, **kwargs)
        return response.status_code, json_of(response)

    @staticmethod
    def _list(settings: dict[str, Any]) -> int:
        list_id = str(settings.get("list_id") or "")
        if not LIST_ID.match(list_id):
            raise IntegrationError("Choose the ActiveCampaign list to sync to.", 409)
        return int(list_id)

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        base = clean_api_url(values.get("api_url") or "")
        key = (values.get("api_key") or "").strip()
        if len(key) < 20:
            raise IntegrationError("That doesn't look like an ActiveCampaign API key.", 422)
        probe = IntegrationConnection(provider=self.key)
        try:
            response = await call(None, probe, "GET", f"{base}/api/3/users/me", headers={"Api-Token": key, "Accept": "application/json"})  # type: ignore[arg-type]
        except AuthFailed as exc:
            raise IntegrationError("ActiveCampaign didn't accept that API key for this account URL.", 422) from exc
        user = json_of(response).get("user") or {}
        account = urlsplit(base).hostname.split(".")[0]  # type: ignore[union-attr]
        return {"label": f"{account} ({user.get('email') or user.get('username') or 'ActiveCampaign'})", "external_id": account, "config": {"api_url": base, "user_email": user.get("email")}}

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        _, data = await self._call(db, connection, "GET", "/users/me")
        user = data.get("user") or {}
        return f"ActiveCampaign answered as {user.get('email') or user.get('username') or 'the API user'}."

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/lists", params={"limit": 100})
        return [{"id": str(row.get("id")), "name": row.get("name"), "count": None} for row in data.get("lists") or [] if row.get("id")]

    async def fields(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/fields", params={"limit": 100})
        return [
            {"key": str(row.get("id")), "label": f"{row.get('title') or 'Field'} (%{row.get('perstag')}%)" if row.get("perstag") else str(row.get("title") or "Field")}
            for row in data.get("fields") or []
            if row.get("id")
        ]

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        values = person.values
        body: dict[str, Any] = {"email": person.email, "first_name": str(values.get("first_name") or "")[:255], "last_name": str(values.get("last_name") or "")[:255]}
        if values.get("phone"):
            body["phone"] = str(values["phone"])[:50]
        if values.get("company"):
            body["customer_acct_name"] = str(values["company"])[:255]
        fields = []
        for target, source in sorted((settings.get("field_map") or {}).items()):
            if FIELD_ID.match(str(target)):
                value = values.get(source)
                fields.append({"id": int(target), "value": "" if value is None else str(value)[:255]})
        if fields:
            body["fields"] = fields
        tags = sorted(set(settings.get("tags") or []))
        if tags:
            body["tags"] = [t[:255] for t in tags]
        body["subscribe"] = [{"listid": self._list(settings)}]
        return body

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        results: dict[str, UpsertResult] = {}
        for start in range(0, len(people), 250):
            chunk = people[start:start + 250]
            _, data = await self._call(db, connection, "POST", "/import/bulk_import", json={"contacts": [self.payload(p, settings) for p in chunk]})
            batch = str(data.get("batchId") or "")
            for person in chunk:
                results[person.email] = UpsertResult(ok=True, status="linked", ref=None, error=None)
            if batch:
                core.log(db, connection.organization_id, self.key, "import_queued", f"Sent {len(chunk)} people to ActiveCampaign (import {batch[:40]}).", connection=connection)
        return results

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        list_id = self._list(settings)
        for start in range(0, len(emails), 250):
            chunk = emails[start:start + 250]
            await self._call(db, connection, "POST", "/import/bulk_import", json={"contacts": [{"email": e, "unsubscribe": [{"listid": list_id}]} for e in chunk]})
        return len(emails)

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        list_id = self._list(settings)
        started = core.utcnow()
        found: list[Change] = []
        full_until: list[datetime] = []
        for status, kind in (("2", "unsubscribed"), ("3", "bounced")):
            params: dict[str, Any] = {"listid": list_id, "status": status, "limit": limit, "orders[udate]": "ASC"}
            if since is not None:
                # ActiveCampaign compares in the account's time zone; an hour of overlap is harmless.
                params["filters[updated_after]"] = (since - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
            _, data = await self._call(db, connection, "GET", "/contacts", params=params)
            rows = data.get("contacts") or []
            for row in rows:
                email = normal_email(row.get("email"))
                if email:
                    found.append(Change(email=email, kind=kind, at=_parse_time(row.get("udate"))))
            if len(rows) >= limit:
                last = _parse_time(rows[-1].get("udate"))
                if last is not None:
                    full_until.append(last)
        return found, (min(full_until) if full_until else started - timedelta(minutes=2))

    async def _contact(self, db: AsyncSession, connection: IntegrationConnection, email: str) -> dict[str, Any] | None:
        _, data = await self._call(db, connection, "GET", "/contacts", params={"email": email, "limit": 1})
        rows = data.get("contacts") or []
        return rows[0] if rows else None

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        contact = await self._contact(db, connection, email)
        if contact is None or not contact.get("id"):
            return None
        list_id = str(self._list(settings))
        _, data = await self._call(db, connection, "GET", f"/contacts/{_int(contact['id'])}/contactLists")
        membership = next((row for row in data.get("contactLists") or [] if str(row.get("list")) == list_id), None)
        status = {"0": "pending", "1": "subscribed", "2": "unsubscribed", "3": "bounced"}.get(str((membership or {}).get("status")), "not_in_list")
        if _int(contact.get("bounced_hard")) > 0 and status in ("subscribed", "not_in_list"):
            status = "bounced"
        return {
            "status": status,
            "lists": [settings.get("list_name") or list_id] if membership else [],
            "tags": [],
            "changed_at": contact.get("udate"),
            "activity": [],
        }

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        _, data = await self._call(db, connection, "GET", "/campaigns", params={"orders[sdate]": "DESC", "limit": 30})
        out: list[Campaign] = []
        for row in data.get("campaigns") or []:
            sent_at = _parse_time(row.get("sdate"))
            if sent_at is None or sent_at < since or str(row.get("status")) not in ("2", "5"):
                continue
            sent = _int(row.get("send_amt"))
            opens = _int(row.get("uniqueopens"))
            clicks = _int(row.get("uniquelinkclicks"))
            out.append(
                Campaign(
                    id=str(row.get("id")), title=str(row.get("name") or "Campaign")[:300], status="sent" if str(row.get("status")) == "5" else "sending", sent_at=sent_at,
                    stats={"sent": sent, "unique_opens": opens, "open_rate": round(opens / sent, 4) if sent else None, "clicks": clicks,
                           "click_rate": round(clicks / sent, 4) if sent else None, "unsubscribes": _int(row.get("unsubscribes"))},
                )
            )
        return out

    async def register_webhook(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], url: str) -> dict[str, Any] | None:
        list_id = self._list(settings)
        _, existing = await self._call(db, connection, "GET", "/webhooks", params={"filters[url]": url})
        for row in existing.get("webhooks") or []:
            if row.get("url") == url and row.get("id"):
                return {"id": str(row["id"]), "list_id": str(list_id)}
        _, data = await self._call(
            db, connection, "POST", "/webhooks",
            json={"webhook": {"name": "AIVA CRM", "url": url, "events": ["unsubscribe", "bounce", "open", "click"], "sources": ["public", "admin", "api", "system"], "listid": list_id}},
        )
        hook = data.get("webhook") or {}
        return {"id": str(hook["id"]), "list_id": str(list_id)} if hook.get("id") else None

    async def remove_webhook(self, db: AsyncSession, connection: IntegrationConnection, info: dict[str, Any]) -> None:
        if re.match(r"^\d{1,12}$", str(info.get("id") or "")):
            await self._call(db, connection, "DELETE", f"/webhooks/{info['id']}", ok_statuses=(404,))

    async def parse_webhook(self, body: bytes, content_type: str, form: list[tuple[str, str]] | None) -> list[WebhookEvent]:
        values = dict(form) if form is not None else dict(parse_qsl(body.decode("utf-8", "replace")))
        kind = {"unsubscribe": "unsubscribed", "bounce": "bounced", "open": "open", "click": "click"}.get(values.get("type") or "")
        if not kind:
            return []
        return [
            WebhookEvent(
                kind=kind,
                email=normal_email(values.get("contact[email]")),
                list_id=values.get("list") or values.get("contact[list]") or None,
                campaign_id=values.get("campaign[id]") or None,
                campaign_title=values.get("campaign[name]") or None,
                url=values.get("link[url]") or None,
            )
        ]
