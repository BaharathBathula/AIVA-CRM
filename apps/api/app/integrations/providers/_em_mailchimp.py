"""Mailchimp Marketing API v3 (API key, HTTP Basic).

* Members are added or updated one by one with ``PUT /lists/{id}/members/{hash}``
  and ``status_if_new``, so an existing member's status (unsubscribed, cleaned)
  is never changed by AIVA.
* Unsubscribes and cleaned addresses come back through a list webhook (Mailchimp
  doesn't sign webhooks, so AIVA re-reads the member before acting) and a poll of
  ``/lists/{id}/members?status=unsubscribed|cleaned&since_last_changed=``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import Adapter, Campaign, Change, Engagement, Person, UpsertResult, WebhookEvent, normal_email
from app.integrations.providers._em_http import AuthFailed, RateLimited, call, json_of
from app.models.integration_hub import IntegrationConnection

KEY_PATTERN = re.compile(r"^[0-9a-f]{32}-([a-z]{2,4}\d{1,3})$")
LIST_ID = re.compile(r"^[A-Za-z0-9]{1,40}$")
CONCURRENCY = 5
STATUS = {"subscribed": "linked", "transactional": "linked", "pending": "pending", "unsubscribed": "unsubscribed", "cleaned": "cleaned"}


def subscriber_hash(email: str) -> str:
    return hashlib.md5(email.lower().encode(), usedforsecurity=False).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class Mailchimp(Adapter):
    key = "mailchimp"
    name = "Mailchimp"
    batch_size = 60
    unsubscribe_batch = 40
    capabilities = {"tags": True, "status_for_new": True, "webhooks": True, "campaigns": True, "engagement": True}
    builtin_fields = (("Email address", "email"), ("First name (FNAME)", "first_name"), ("Last name (LNAME)", "last_name"), ("Phone (PHONE)", "phone"))
    list_label = "Audience"

    # ---------------------------------------------------------------- plumbing

    @staticmethod
    def _key(connection: IntegrationConnection) -> str:
        key = core.secret(connection, "api_key") or ""
        if not KEY_PATTERN.match(key):
            raise IntegrationError("The Mailchimp API key is missing. Paste it on the Mailchimp page.", 409)
        return key

    def _base(self, connection: IntegrationConnection, key: str | None = None) -> str:
        match = KEY_PATTERN.match(key or self._key(connection))
        assert match is not None
        return f"https://{match.group(1)}.api.mailchimp.com/3.0"

    async def _call(self, db: AsyncSession, connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> Any:
        key = self._key(connection)
        response = await call(db, connection, method, f"{self._base(connection, key)}{path}", auth=("aiva", key), headers={"Accept": "application/json"}, **kwargs)
        return response.status_code, json_of(response)

    @staticmethod
    def _list(settings: dict[str, Any]) -> str:
        list_id = settings.get("list_id") or ""
        if not LIST_ID.match(list_id):
            raise IntegrationError("Choose the Mailchimp audience to sync to.", 409)
        return list_id

    # ---------------------------------------------------------------- connect

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        key = (values.get("api_key") or "").strip()
        if not KEY_PATTERN.match(key):
            raise IntegrationError("That doesn't look like a Mailchimp API key. It ends with your data center, like -us21.", 422)
        probe = IntegrationConnection(provider=self.key)
        try:
            response = await call(None, probe, "GET", f"{self._base(probe, key)}/", auth=("aiva", key), params={"fields": "account_id,account_name,email,login_id"})  # type: ignore[arg-type]
        except AuthFailed as exc:
            raise IntegrationError("Mailchimp didn't accept that API key. Create a new one under Profile → Extras → API keys.", 422) from exc
        data = json_of(response)
        return {
            "label": data.get("account_name") or data.get("email") or "Mailchimp account",
            "external_id": data.get("account_id"),
            "config": {"data_center": KEY_PATTERN.match(key).group(1), "account_name": data.get("account_name"), "login_email": data.get("email")},  # type: ignore[union-attr]
        }

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        _, data = await self._call(db, connection, "GET", "/ping")
        return f"Mailchimp answered: {data.get('health_status') or 'OK'}"

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/lists", params={"count": 200, "fields": "lists.id,lists.name,lists.stats.member_count,lists.double_optin"})
        return [
            {"id": row.get("id"), "name": row.get("name"), "count": (row.get("stats") or {}).get("member_count"), "double_optin": row.get("double_optin")}
            for row in data.get("lists") or []
            if row.get("id")
        ]

    async def _merge_fields(self, db: AsyncSession, connection: IntegrationConnection, list_id: str) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", f"/lists/{list_id}/merge-fields", params={"count": 100, "fields": "merge_fields.tag,merge_fields.name,merge_fields.type"})
        return [row for row in data.get("merge_fields") or [] if row.get("tag")]

    async def fields(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> list[dict[str, Any]]:
        if not settings.get("list_id"):
            return []
        rows = await self._merge_fields(db, connection, self._list(settings))
        return [{"key": row["tag"], "label": f"{row.get('name') or row['tag']} ({row['tag']})"} for row in rows if row["tag"] not in ("EMAIL", "FNAME", "LNAME", "PHONE")]

    # ---------------------------------------------------------------- sync

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        values = person.values
        merge: dict[str, str] = {"FNAME": str(values.get("first_name") or ""), "LNAME": str(values.get("last_name") or "")}
        if values.get("phone"):
            merge["PHONE"] = str(values["phone"])[:50]
        for target, source in (settings.get("field_map") or {}).items():
            value = values.get(source)
            merge[target] = "" if value is None else str(value)[:255]
        return {
            "email_address": person.email,
            "status_if_new": settings.get("status_for_new") if settings.get("status_for_new") in ("subscribed", "pending", "transactional") else "subscribed",
            "merge_fields": merge,
            "tags": sorted(set(settings.get("tags") or [])),
        }

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        list_id = self._list(settings)
        known = {row["tag"] for row in await self._merge_fields(db, connection, list_id)}
        semaphore = asyncio.Semaphore(CONCURRENCY)
        stop: list[IntegrationError] = []

        async def one(person: Person) -> tuple[str, UpsertResult]:
            body = self.payload(person, settings)
            tags = body.pop("tags")
            body["merge_fields"] = {k: v for k, v in body["merge_fields"].items() if k in known}
            path = f"/lists/{list_id}/members/{subscriber_hash(person.email)}"
            async with semaphore:
                if stop:
                    return person.email, UpsertResult(ok=False, status="error", error="Not sent: the sync stopped early.")
                try:
                    _, data = await self._call(db, connection, "PUT", path, params={"skip_merge_validation": "true"}, json=body)
                    if tags:
                        await self._call(db, connection, "POST", f"{path}/tags", json={"tags": [{"name": t[:100], "status": "active"} for t in tags]})
                except (RateLimited, AuthFailed) as exc:
                    stop.append(exc)
                    return person.email, UpsertResult(ok=False, status="error", error=str(exc))
                except IntegrationError as exc:
                    text = str(exc)
                    if "compliance state" in text.lower():
                        return person.email, UpsertResult(ok=True, status="unsubscribed", error=None)
                    return person.email, UpsertResult(ok=False, status="error", error=text[:500])
            remote = str(data.get("status") or "")
            return person.email, UpsertResult(ok=True, status=STATUS.get(remote, "linked"), ref=data.get("id"))

        results = dict(await asyncio.gather(*(one(p) for p in people)))
        if stop:
            raise stop[0]
        return results

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        list_id = self._list(settings)
        done = 0
        for email in emails:
            status, _ = await self._call(db, connection, "PATCH", f"/lists/{list_id}/members/{subscriber_hash(email)}", json={"status": "unsubscribed"}, ok_statuses=(404,))
            done += status < 400
        return done

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        list_id = self._list(settings)
        started = core.utcnow()
        found: list[Change] = []
        full_page_until: list[datetime] = []
        for status, kind in (("unsubscribed", "unsubscribed"), ("cleaned", "cleaned")):
            params: dict[str, Any] = {
                "status": status, "count": limit, "sort_field": "last_changed", "sort_dir": "ASC",
                "fields": "members.email_address,members.status,members.last_changed,members.unsubscribe_reason",
            }
            if since is not None:
                params["since_last_changed"] = since.isoformat(timespec="seconds")
            _, data = await self._call(db, connection, "GET", f"/lists/{list_id}/members", params=params)
            rows = data.get("members") or []
            for row in rows:
                email = normal_email(row.get("email_address"))
                if email:
                    found.append(Change(email=email, kind=kind, at=_parse_time(row.get("last_changed")), note=str(row.get("unsubscribe_reason") or "")[:200]))
            if len(rows) >= limit:
                last = _parse_time(rows[-1].get("last_changed"))
                if last is not None:
                    full_page_until.append(last)
        cursor = min(full_page_until) if full_page_until else started - timedelta(minutes=2)
        return found, cursor

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        list_id = self._list(settings)
        path = f"/lists/{list_id}/members/{subscriber_hash(email)}"
        status, data = await self._call(
            db, connection, "GET", path, ok_statuses=(404,),
            params={"fields": "id,email_address,status,tags,last_changed,stats,unsubscribe_reason,web_id"},
        )
        if status == 404:
            return None
        activity: list[dict[str, Any]] = []
        try:
            _, feed = await self._call(db, connection, "GET", f"{path}/activity-feed", params={"count": 8})
            for item in feed.get("activity") or []:
                activity.append(
                    {
                        "action": item.get("activity_type") or item.get("action"),
                        "at": item.get("created_at_timestamp") or item.get("timestamp"),
                        "title": item.get("campaign_title") or item.get("title"),
                        "url": item.get("link_clicked") or item.get("url"),
                    }
                )
        except IntegrationError:
            pass
        stats = data.get("stats") or {}
        remote = str(data.get("status") or "")
        return {
            "status": remote if remote in ("subscribed", "pending", "unsubscribed", "cleaned", "transactional", "archived") else "unknown",
            "status_note": data.get("unsubscribe_reason"),
            "lists": [settings.get("list_name") or list_id],
            "tags": [t.get("name") for t in data.get("tags") or [] if t.get("name")],
            "changed_at": data.get("last_changed"),
            "open_rate": stats.get("avg_open_rate"),
            "click_rate": stats.get("avg_click_rate"),
            "activity": activity,
        }

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        list_id = self._list(settings)
        _, data = await self._call(
            db, connection, "GET", "/campaigns",
            params={
                "status": "sent", "list_id": list_id, "since_send_time": since.isoformat(timespec="seconds"), "count": 50,
                "sort_field": "send_time", "sort_dir": "DESC",
                "fields": "campaigns.id,campaigns.settings.title,campaigns.settings.subject_line,campaigns.send_time,campaigns.emails_sent,campaigns.report_summary,campaigns.long_archive_url,campaigns.status",
            },
        )
        out: list[Campaign] = []
        for row in data.get("campaigns") or []:
            info = row.get("settings") or {}
            report = row.get("report_summary") or {}
            out.append(
                Campaign(
                    id=str(row.get("id")),
                    title=str(info.get("title") or info.get("subject_line") or "Campaign")[:300],
                    status=row.get("status"),
                    sent_at=_parse_time(row.get("send_time")),
                    url=row.get("long_archive_url"),
                    stats={
                        "sent": row.get("emails_sent"), "unique_opens": report.get("unique_opens"), "open_rate": report.get("open_rate"),
                        "clicks": report.get("subscriber_clicks"), "click_rate": report.get("click_rate"), "subject": info.get("subject_line"),
                    },
                )
            )
        return out

    async def engagement(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], campaign: Campaign, since: datetime | None, limit: int
    ) -> list[Engagement]:
        params: dict[str, Any] = {"count": limit, "fields": "emails.email_address,emails.activity"}
        if since is not None:
            params["since"] = since.isoformat(timespec="seconds")
        _, data = await self._call(db, connection, "GET", f"/reports/{campaign.id}/email-activity", params=params)
        out: list[Engagement] = []
        for row in data.get("emails") or []:
            email = normal_email(row.get("email_address"))
            if not email:
                continue
            for item in row.get("activity") or []:
                action = item.get("action")
                if action in ("open", "click"):
                    out.append(Engagement(email=email, kind=action, campaign_id=campaign.id, campaign_title=campaign.title, at=_parse_time(item.get("timestamp")), url=item.get("url")))
        return out

    async def register_webhook(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], url: str) -> dict[str, Any] | None:
        list_id = self._list(settings)
        _, existing = await self._call(db, connection, "GET", f"/lists/{list_id}/webhooks")
        for row in existing.get("webhooks") or []:
            if row.get("url") == url and row.get("id"):
                return {"id": row["id"], "list_id": list_id}
        _, data = await self._call(
            db, connection, "POST", f"/lists/{list_id}/webhooks",
            json={
                "url": url,
                "events": {"unsubscribe": True, "cleaned": True, "subscribe": False, "profile": False, "upemail": False, "campaign": False},
                "sources": {"user": True, "admin": True, "api": False},
            },
        )
        return {"id": data.get("id"), "list_id": list_id} if data.get("id") else None

    async def remove_webhook(self, db: AsyncSession, connection: IntegrationConnection, info: dict[str, Any]) -> None:
        if info.get("id") and LIST_ID.match(str(info.get("list_id") or "")) and re.match(r"^[A-Za-z0-9]{1,40}$", str(info["id"])):
            await self._call(db, connection, "DELETE", f"/lists/{info['list_id']}/webhooks/{info['id']}", ok_statuses=(404,))

    async def parse_webhook(self, body: bytes, content_type: str, form: list[tuple[str, str]] | None) -> list[WebhookEvent]:
        values = dict(form) if form is not None else dict(parse_qsl(body.decode("utf-8", "replace")))
        kind = {"unsubscribe": "unsubscribed", "cleaned": "cleaned"}.get(values.get("type") or "")
        if not kind:
            return []
        return [WebhookEvent(kind=kind, email=normal_email(values.get("data[email]")), list_id=values.get("data[list_id]"))]
