"""Constant Contact API v3 (OAuth 2.0; the token is handled by ``core``).

* People are sent with the JSON contacts import. Constant Contact never changes
  an existing contact's permission to send in an import, so nobody who
  unsubscribed is re-subscribed.
* Constant Contact has no unsubscribe webhook for most accounts, so AIVA polls
  contacts with ``status=unsubscribed`` updated since the last run.
* AIVA unsubscribes someone by re-saving the contact with
  ``permission_to_send = unsubscribed`` (opt-out source: Account).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_base import Adapter, Campaign, Change, Person, UpsertResult, normal_email
from app.integrations.providers._em_http import call, json_of
from app.models.integration_hub import IntegrationConnection

HOST = "https://api.cc.email"
API = f"{HOST}/v3"
LIST_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
FIELD_NAME = re.compile(r"^[A-Za-z0-9_]{1,50}$")
CONTACT_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
# Fields kept when AIVA re-saves a contact to unsubscribe it.
PUT_FIELDS = (
    "first_name", "last_name", "job_title", "company_name", "birthday_month", "birthday_day", "anniversary",
    "custom_fields", "phone_numbers", "street_addresses", "list_memberships", "taggings", "notes",
)
STATUS = {"implicit": "subscribed", "explicit": "subscribed", "unsubscribed": "unsubscribed", "pending_confirmation": "pending", "temp_hold": "bounced", "not_set": "not_in_list"}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ConstantContact(Adapter):
    key = "constant_contact"
    name = "Constant Contact"
    batch_size = 500
    unsubscribe_batch = 20
    capabilities = {"campaigns": True}
    builtin_fields = (("Email", "email"), ("First name", "first_name"), ("Last name", "last_name"), ("Job title", "job_title"), ("Company", "company"), ("Phone", "phone"))

    async def _call(self, db: AsyncSession, connection: IntegrationConnection, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        url = path if path.startswith(HOST) else f"{API}{path}"
        response = await call(db, connection, method, url, headers={"Accept": "application/json"}, **kwargs)
        return response.status_code, json_of(response)

    @staticmethod
    def _list(settings: dict[str, Any]) -> str:
        list_id = str(settings.get("list_id") or "")
        if not LIST_ID.match(list_id):
            raise IntegrationError("Choose the Constant Contact list to sync to.", 409)
        return list_id

    async def validate(self, values: dict[str, str]) -> dict[str, Any]:
        raise IntegrationError("Constant Contact connects by signing in. Click Connect.", 400)

    async def test(self, db: AsyncSession, connection: IntegrationConnection) -> str:
        _, data = await self._call(db, connection, "GET", "/account/summary")
        return f"Constant Contact answered: {data.get('organization_name') or data.get('contact_email') or 'your account'}."

    async def lists(self, db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/contact_lists", params={"limit": 500, "include_count": "true", "status": "active"})
        return [{"id": row.get("list_id"), "name": row.get("name"), "count": row.get("membership_count")} for row in data.get("lists") or [] if row.get("list_id")]

    async def fields(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> list[dict[str, Any]]:
        _, data = await self._call(db, connection, "GET", "/contact_custom_fields", params={"limit": 100})
        return [{"key": row.get("name"), "label": row.get("label") or row.get("name")} for row in data.get("custom_fields") or [] if row.get("name")]

    def payload(self, person: Person, settings: dict[str, Any]) -> dict[str, Any]:
        values = person.values
        body: dict[str, Any] = {"email": person.email}
        for target, source in (("first_name", "first_name"), ("last_name", "last_name"), ("job_title", "job_title"), ("company_name", "company"), ("phone", "phone")):
            if values.get(source):
                body[target] = str(values[source])[:50]
        for target, source in sorted((settings.get("field_map") or {}).items())[:25]:
            if FIELD_NAME.match(str(target)):
                value = values.get(source)
                body[f"cf:{target}"] = "" if value is None else str(value)[:255]
        return body

    async def upsert(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], people: list[Person]) -> dict[str, UpsertResult]:
        list_id = self._list(settings)
        _, data = await self._call(db, connection, "POST", "/activities/contacts_json_import", json={"import_data": [self.payload(p, settings) for p in people], "list_ids": [list_id]})
        if data.get("activity_id"):
            core.log(db, connection.organization_id, self.key, "import_queued", f"Sent {len(people)} people to Constant Contact (import {data['activity_id']}).", connection=connection)
        return {p.email: UpsertResult(ok=True, status="linked") for p in people}

    async def _find(self, db: AsyncSession, connection: IntegrationConnection, email: str, include: str) -> dict[str, Any] | None:
        _, data = await self._call(db, connection, "GET", "/contacts", params={"email": email, "status": "all", "include": include, "limit": 1})
        rows = data.get("contacts") or []
        return rows[0] if rows else None

    async def unsubscribe(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], emails: list[str]) -> int:
        done = 0
        for email in emails:
            contact = await self._find(db, connection, email, "custom_fields,list_memberships,phone_numbers,street_addresses,taggings,notes")
            if contact is None or not CONTACT_ID.match(str(contact.get("contact_id") or "")):
                continue
            address = contact.get("email_address") or {}
            if address.get("permission_to_send") == "unsubscribed":
                done += 1
                continue
            body = {k: contact[k] for k in PUT_FIELDS if contact.get(k) not in (None, [], "")}
            body["email_address"] = {"address": address.get("address") or email, "permission_to_send": "unsubscribed"}
            body["update_source"] = "Account"
            await self._call(db, connection, "PUT", f"/contacts/{contact['contact_id']}", json=body)
            done += 1
        return done

    async def changes(
        self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime | None, limit: int
    ) -> tuple[list[Change], datetime | None]:
        started = core.utcnow()
        params: dict[str, Any] = {"status": "unsubscribed", "limit": min(max(limit, 50), 500)}
        if since is not None:
            params["updated_after"] = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        found: list[Change] = []
        path = "/contacts"
        for _page in range(10):
            _, data = await self._call(db, connection, "GET", path, params=params)
            for row in data.get("contacts") or []:
                address = row.get("email_address") or {}
                email = normal_email(address.get("address"))
                if email and address.get("permission_to_send") == "unsubscribed":
                    found.append(Change(email=email, kind="unsubscribed", at=_parse_time(address.get("opt_out_date") or row.get("updated_at")), note=str(address.get("opt_out_reason") or "")[:200]))
            next_href = (((data.get("_links") or {}).get("next")) or {}).get("href") or ""
            if not next_href.startswith("/v3/contacts"):
                return found, started - timedelta(minutes=2)
            path, params = f"{HOST}{next_href}", {}
        # More than ten pages: keep the cursor and carry on next run.
        return found, since

    async def member(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], email: str) -> dict[str, Any] | None:
        contact = await self._find(db, connection, email, "list_memberships,taggings")
        if contact is None:
            return None
        address = contact.get("email_address") or {}
        memberships = contact.get("list_memberships") or []
        status = STATUS.get(str(address.get("permission_to_send") or ""), "unknown")
        list_id = settings.get("list_id")
        if status == "subscribed" and list_id and list_id not in memberships:
            status = "not_in_list"
        activity: list[dict[str, Any]] = []
        if CONTACT_ID.match(str(contact.get("contact_id") or "")):
            try:
                _, report = await self._call(
                    db, connection, "GET", f"/reports/contact_reports/{contact['contact_id']}/activity_details",
                    params={"tracking_activity_type": "em_opens,em_clicks,em_bounces,em_optouts", "limit": 8},
                )
                labels = {"em_opens": "open", "em_clicks": "click", "em_bounces": "bounce", "em_optouts": "unsubscribe"}
                for row in report.get("tracking_activities") or []:
                    activity.append({"action": labels.get(row.get("tracking_activity_type"), row.get("tracking_activity_type")), "at": row.get("created_time"),
                                     "title": row.get("campaign_name") or "Email campaign", "url": row.get("link_url")})
            except IntegrationError:
                pass
        return {
            "status": status,
            "status_note": address.get("opt_out_reason"),
            "lists": [settings.get("list_name") or "Your list"] if list_id in memberships else ([f"{len(memberships)} other lists"] if memberships else []),
            "tags": [],
            "changed_at": contact.get("updated_at"),
            "activity": activity,
        }

    async def campaigns(self, db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], since: datetime) -> list[Campaign]:
        _, summaries = await self._call(db, connection, "GET", "/reports/summary_reports/email_campaign_summaries", params={"limit": 50})
        _, emails = await self._call(db, connection, "GET", "/emails", params={"limit": 50})
        names = {row.get("campaign_id"): row.get("name") for row in emails.get("campaigns") or []}
        out: list[Campaign] = []
        for row in summaries.get("bulk_email_campaign_summaries") or []:
            sent_at = _parse_time(row.get("last_sent_date"))
            if sent_at is None or sent_at < since:
                continue
            counts = row.get("unique_counts") or {}
            sent = int(counts.get("sends") or 0)
            opens = int(counts.get("opens") or 0)
            clicks = int(counts.get("clicks") or 0)
            out.append(
                Campaign(
                    id=str(row.get("campaign_id")), title=str(names.get(row.get("campaign_id")) or "Email campaign")[:300], status="sent", sent_at=sent_at,
                    stats={"sent": sent, "unique_opens": opens, "open_rate": round(opens / sent, 4) if sent else None, "clicks": clicks,
                           "click_rate": round(clicks / sent, 4) if sent else None, "unsubscribes": counts.get("optouts"), "bounces": counts.get("bounces")},
                )
            )
        return out
