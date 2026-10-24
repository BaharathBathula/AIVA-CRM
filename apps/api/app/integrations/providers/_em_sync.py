"""The sync shared by the email marketing integrations.

For each connected tool, on a schedule (and on "Sync now"):

1. Keep the tool's unsubscribe webhook registered (when AIVA has a public address).
2. Pull unsubscribes, bounces and spam complaints from the tool into AIVA's
   do-not-contact list (``SuppressionEntry``) and opt the matching contacts and
   leads out of email.
3. Push AIVA's do-not-contact list to the tool for people AIVA synced there.
4. Send contacts and leads changed since the last run (backfill first), skipping
   unchanged people (``sync_hash``) and never adding anyone on the do-not-contact
   list or anyone the tool says unsubscribed.
5. Hourly: campaigns and their stats, and opens/clicks of synced people on the
   timeline (Mailchimp); CRM events to Klaviyo.

Every run is bounded: a time budget per run and per connection, batch sizes per
call, and caps on pulled changes, activities and events.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers._em_activecampaign import ActiveCampaign
from app.integrations.providers._em_base import (
    KEYS,
    LINK_TYPE,
    SUPPRESSION_REASON,
    Adapter,
    Change,
    Engagement,
    Person,
    UpsertResult,
    digest,
    normal_email,
    settings_from,
)
from app.integrations.providers._em_brevo import Brevo
from app.integrations.providers._em_constant_contact import ConstantContact
from app.integrations.providers._em_http import AuthFailed, RateLimited
from app.integrations.providers._em_klaviyo import Klaviyo
from app.integrations.providers._em_mailchimp import Mailchimp
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.governance import SuppressionEntry
from app.models.integration_hub import IntegrationConnection, IntegrationObject, IntegrationRecordLink
from app.models.lead import Lead
from app.models.user import User

logger = logging.getLogger("aiva.integrations.email_marketing")

ADAPTERS: dict[str, Adapter] = {a.key: a for a in (Mailchimp(), ActiveCampaign(), Brevo(), ConstantContact(), Klaviyo())}

SYNC_EVERY = timedelta(minutes=5)
BACKLOG_EVERY = timedelta(minutes=1)
POLL_EVERY = timedelta(minutes=15)
CAMPAIGNS_EVERY = timedelta(hours=1)
WEBHOOK_RETRY = timedelta(hours=6)
RUN_BUDGET_SECONDS = 30.0  # all email marketing connections, per scheduler tick
CONNECTION_BUDGET_SECONDS = 12.0
MAX_CONNECTIONS_PER_RUN = 8
MAX_RECORDS_PER_RUN = 1500  # records looked at per connection per run
CHANGES_LIMIT = 500
SUPPRESSIONS_PER_RUN = 500
ENGAGEMENT_PER_RUN = 200
EVENTS_PER_RUN = 50
CAMPAIGN_DAYS = 30
ENGAGEMENT_DAYS = 7
OPTED_OUT = ("unsubscribed", "cleaned", "bounced", "complaint")
ACTIVE = ("linked", "pending", "error")
KIND_LABEL = {"unsubscribed": "unsubscribed", "cleaned": "cleaned (hard bounce)", "bounced": "hard bounce", "complaint": "spam complaint"}
STRENGTH = {"unsubscribed": 1, "cleaned": 2, "bounced": 2, "complaint": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def adapter_for(key: str) -> Adapter:
    found = ADAPTERS.get(key)
    if found is None:
        raise IntegrationError("Unknown email marketing tool.", 404)
    return found


def state_of(connection: IntegrationConnection) -> dict[str, Any]:
    return dict((connection.config or {}).get("em_sync") or {})


def save_state(connection: IntegrationConnection, state: dict[str, Any]) -> None:
    connection.config = {**(connection.config or {}), "em_sync": state}


async def settings_for(db: AsyncSession, connection: IntegrationConnection) -> dict[str, Any]:
    return settings_from(await core.workspace_config(db, connection.organization_id, connection.provider))


def list_key(settings: dict[str, Any]) -> str:
    return str(settings.get("list_id") or "")


def public_address() -> bool:
    """Webhooks can only reach AIVA at a public https address."""

    parts = urlsplit(get_settings().app_base_url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    return host not in ("localhost", "127.0.0.1", "::1") and not host.endswith((".localhost", ".local", ".test", ".internal", ".lan"))


async def plan_allows(db: AsyncSession, organization_id: uuid.UUID, cache: dict[uuid.UUID, bool]) -> bool:
    if organization_id not in cache:
        from app.models.editions import Subscription
        from app.services.billing import plan_state
        from app.services.metering import addon_features

        subscription = (await db.execute(select(Subscription).where(Subscription.organization_id == organization_id))).scalar_one_or_none()
        state = plan_state(subscription)
        features = set(state.features) | set(await addon_features(db, organization_id))
        cache[organization_id] = "mass_email" in features and not state.read_only
    return cache[organization_id]


# ===========================================================================
# Do-not-contact list, both ways
# ===========================================================================


async def suppress(db: AsyncSession, connection: IntegrationConnection, changes: list[Change], settings: dict[str, Any]) -> int:
    """Put people the tool reported on AIVA's do-not-contact list; returns how many were new."""

    adapter = adapter_for(connection.provider)
    org = connection.organization_id
    strongest: dict[str, Change] = {}
    for change in changes:
        email = normal_email(change.email)
        if not email or change.kind not in OPTED_OUT:
            continue
        current = strongest.get(email)
        if current is None or STRENGTH[change.kind] > STRENGTH[current.kind]:
            strongest[email] = Change(email=email, kind=change.kind, at=change.at, note=change.note)
    if not strongest:
        return 0
    emails = sorted(strongest)
    existing = set(
        (
            await db.execute(
                select(SuppressionEntry.value).where(SuppressionEntry.organization_id == org, SuppressionEntry.kind == "email", SuppressionEntry.value.in_(emails))
            )
        ).scalars()
    )
    new = [e for e in emails if e not in existing]
    for email in new:
        change = strongest[email]
        note = f"{adapter.name}: {KIND_LABEL[change.kind]}" + (f" ({change.note})" if change.note else "")
        await db.execute(
            pg_insert(SuppressionEntry)
            .values(id=uuid.uuid4(), organization_id=org, kind="email", value=email, reason=SUPPRESSION_REASON[change.kind], note=note[:300])
            .on_conflict_do_nothing(constraint="uq_suppression_entries_value")
        )
    for model in (Contact, Lead):
        await db.execute(
            update(model)
            .where(model.organization_id == org, func.lower(model.email).in_(emails), model.email_opt_out.is_(False))
            .values(email_opt_out=True)
            .execution_options(synchronize_session=False)
        )
    links = (
        await db.execute(
            select(IntegrationRecordLink).where(
                IntegrationRecordLink.organization_id == org,
                IntegrationRecordLink.provider == connection.provider,
                IntegrationRecordLink.external_type == LINK_TYPE,
                IntegrationRecordLink.external_parent == list_key(settings),
                IntegrationRecordLink.external_id.in_(emails),
            )
        )
    ).scalars().all()
    for link in links:
        kind = strongest[link.external_id].kind
        link.status = kind
        link.data = {**(link.data or {}), "opted_out_at": _iso(strongest[link.external_id].at or utcnow()), "opted_out_in": adapter.name}
    if new:
        core.log(
            db, org, connection.provider, "suppressed",
            f"{len(new)} {'person' if len(new) == 1 else 'people'} unsubscribed, bounced or complained in {adapter.name}; "
            f"added to AIVA's do-not-contact list.",
            connection=connection, detail={"emails": new[:20]},
        )
    return len(new)


async def push_suppressions(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], state: dict[str, Any], deadline: float) -> int:
    """Unsubscribe, in the tool, people AIVA synced there who are now on AIVA's do-not-contact list."""

    adapter = adapter_for(connection.provider)
    org = connection.organization_id
    cursor = state.get("suppression_cursor") or [None, None]
    query = select(SuppressionEntry).where(SuppressionEntry.organization_id == org)
    after, after_id = _when(cursor[0]), cursor[1]
    if after is not None:
        query = query.where(or_(SuppressionEntry.created_at > after, and_(SuppressionEntry.created_at == after, SuppressionEntry.id > uuid.UUID(after_id))))
    entries = (await db.execute(query.order_by(SuppressionEntry.created_at, SuppressionEntry.id).limit(SUPPRESSIONS_PER_RUN))).scalars().all()
    if not entries:
        return 0
    done = 0
    for start in range(0, len(entries), max(1, adapter.unsubscribe_batch)):
        if time.monotonic() > deadline:
            break
        chunk = entries[start:start + adapter.unsubscribe_batch]
        emails = [e.value for e in chunk if e.kind == "email"]
        domains = [e.value for e in chunk if e.kind == "domain"]
        conditions = [IntegrationRecordLink.external_id.in_(emails)] if emails else []
        conditions += [IntegrationRecordLink.external_id.like(f"%@{d}") for d in domains[:20]]
        links = []
        if conditions:
            links = (
                await db.execute(
                    select(IntegrationRecordLink).where(
                        IntegrationRecordLink.organization_id == org,
                        IntegrationRecordLink.provider == connection.provider,
                        IntegrationRecordLink.external_type == LINK_TYPE,
                        IntegrationRecordLink.external_parent == list_key(settings),
                        IntegrationRecordLink.status.in_(ACTIVE),
                        or_(*conditions),
                    ).limit(500)
                )
            ).scalars().all()
        if links:
            await adapter.unsubscribe(db, connection, settings, sorted({link.external_id for link in links}))
            for link in links:
                link.status = "unsubscribed"
                link.data = {**(link.data or {}), "opted_out_at": _iso(utcnow()), "opted_out_in": "AIVA"}
            done += len(links)
        last = chunk[-1]
        state["suppression_cursor"] = [_iso(last.created_at), str(last.id)]
    if done:
        core.log(db, org, connection.provider, "unsubscribed_in_tool", f"Unsubscribed {done} {'person' if done == 1 else 'people'} in {adapter.name} who are on AIVA's do-not-contact list.", connection=connection)
    return done


# ===========================================================================
# People
# ===========================================================================


def _eligible(record: Any, record_type: str, settings: dict[str, Any]) -> bool:
    if record_type == "contact":
        if getattr(record, "is_archived", False) or getattr(record, "is_active", True) is False:
            return False
        wanted = {t.lower() for t in settings.get("contact_tags") or []}
        if wanted and not wanted & {str(t).lower() for t in (record.tags or [])}:
            return False
        return True
    if settings.get("skip_converted", True) and (record.status == "converted" or record.converted_at is not None):
        return False
    statuses = {s.lower() for s in settings.get("lead_statuses") or []}
    return not statuses or str(record.status or "").lower() in statuses


async def _people(db: AsyncSession, organization_id: uuid.UUID, record_type: str, records: list[Any]) -> list[tuple[Any, Person]]:
    owners = {r.owner_user_id for r in records if r.owner_user_id}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(owners)))).scalars()} if owners else {}
    accounts: dict[uuid.UUID, Account] = {}
    if record_type == "contact":
        ids = {r.account_id for r in records if r.account_id}
        if ids:
            accounts = {a.id: a for a in (await db.execute(select(Account).where(Account.organization_id == organization_id, Account.id.in_(ids)))).scalars()}
    base = get_settings().app_base_url.rstrip("/")
    out: list[tuple[Any, Person]] = []
    for record in records:
        email = normal_email(record.email)
        if not email:
            continue
        owner = users.get(record.owner_user_id)
        values: dict[str, Any] = {
            "email": email,
            "first_name": record.first_name or "",
            "last_name": record.last_name or "",
            "full_name": f"{record.first_name or ''} {record.last_name or ''}".strip(),
            "phone": record.phone or (getattr(record, "mobile", None) or ""),
            "mobile": getattr(record, "mobile", None) or "",
            "job_title": record.job_title or "",
            "owner_name": owner.full_name if owner else "",
            "owner_email": owner.email if owner else "",
            "score": record.score,
        }
        if record_type == "contact":
            account = accounts.get(record.account_id)
            values.update(
                {
                    "company": account.name if account else "",
                    "industry": (account.industry if account else "") or "",
                    "city": (account.billing_city if account else "") or "",
                    "country": (account.billing_country if account else "") or "",
                    "record_type": "Contact",
                    "contact_stage": record.stage or "",
                    "lead_status": "",
                    "lead_source": "",
                    "aiva_url": f"{base}/contacts/{record.id}",
                }
            )
            tags = list(record.tags or [])
        else:
            values.update(
                {
                    "company": record.company_name or "", "industry": "", "city": "", "country": "", "record_type": "Lead", "contact_stage": "",
                    "lead_status": record.status or "", "lead_source": record.source or "", "aiva_url": f"{base}/leads/{record.id}",
                }
            )
            tags = []
        out.append((record, Person(record_type=record_type, record_id=record.id, email=email, values=values, aiva_tags=tags, opted_out=bool(record.email_opt_out))))
    return out


async def _links(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], *, emails: list[str] | None = None, record_ids: list[uuid.UUID] | None = None) -> list[IntegrationRecordLink]:
    query = select(IntegrationRecordLink).where(
        IntegrationRecordLink.organization_id == connection.organization_id,
        IntegrationRecordLink.provider == connection.provider,
        IntegrationRecordLink.external_type == LINK_TYPE,
        IntegrationRecordLink.external_parent == list_key(settings),
    )
    if emails is not None:
        query = query.where(IntegrationRecordLink.external_id.in_(emails))
    if record_ids is not None:
        query = query.where(IntegrationRecordLink.record_id.in_(record_ids))
    return list((await db.execute(query)).scalars())


async def _owner_still_has(db: AsyncSession, link: IntegrationRecordLink) -> bool:
    model = Contact if link.record_type == "contact" else Lead
    email = (await db.execute(select(model.email).where(model.id == link.record_id))).scalar_one_or_none()
    return normal_email(email) == link.external_id


async def sync_people(
    db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], pairs: list[tuple[Any, Person]], summary: dict[str, int], *, force: bool = False
) -> None:
    """Send these people to the tool (or unsubscribe them there), keeping their links up to date.

    ``force`` (someone clicked Subscribe on the record) skips the sync filters and
    sends even when nothing changed; the do-not-contact list still applies.
    """

    adapter = adapter_for(connection.provider)
    org = connection.organization_id
    parent = list_key(settings)
    from app.services.prospecting import suppressed_emails

    # One member per address: a contact wins over a lead with the same email.
    by_email: dict[str, tuple[Any, Person]] = {}
    for record, person in pairs:
        current = by_email.get(person.email)
        if current is None or (current[1].record_type == "lead" and person.record_type == "contact"):
            by_email[person.email] = (record, person)
    emails = sorted(by_email)
    if not emails:
        return
    blocked = await suppressed_emails(db, org, emails)
    links_by_email = {link.external_id: link for link in await _links(db, connection, settings, emails=emails)}
    links_by_record = {(link.record_type, link.record_id): link for link in await _links(db, connection, settings, record_ids=[p.record_id for _, p in by_email.values()])}

    # Records whose email changed: their old link moves to the new address (or goes if
    # someone else already holds it). The old address stays in the tool.
    for _record, person in by_email.values():
        own = links_by_record.get((person.record_type, person.record_id))
        if own is not None and own.external_id != person.email:
            if person.email in links_by_email:
                await db.delete(own)
            else:
                links_by_email.pop(own.external_id, None)
                own.external_id = person.email
                own.sync_hash = None
                links_by_email[person.email] = own
    await db.flush()

    send: list[Person] = []
    leave: list[str] = []
    for record, person in by_email.values():
        link = links_by_email.get(person.email)
        if link is not None and (link.record_type, link.record_id) != (person.record_type, person.record_id):
            takes_over = (link.record_type == "lead" and person.record_type == "contact") or not await _owner_still_has(db, link)
            if not takes_over:
                summary["unchanged"] += 1
                continue
            link.record_type, link.record_id, link.sync_hash = person.record_type, person.record_id, None
        if not force and not _eligible(record, person.record_type, settings):
            summary["skipped"] += 1
            continue
        if person.opted_out or person.email in blocked:
            if link is not None and link.status in ACTIVE:
                leave.append(person.email)
            else:
                summary["skipped"] += 1
            continue
        if link is not None and link.status in OPTED_OUT:
            summary["skipped"] += 1  # the tool says they opted out; AIVA never re-subscribes anyone
            continue
        fingerprint = digest({"p": adapter.payload(person, settings), "l": parent})
        if not force and link is not None and link.sync_hash == fingerprint and link.status in ("linked", "pending"):
            summary["unchanged"] += 1
            continue
        person.values["_fingerprint"] = fingerprint
        send.append(person)

    now = utcnow()
    tool_opt_outs: list[Change] = []
    if send:
        results = await adapter.upsert(db, connection, settings, send)
        for person in send:
            result = results.get(person.email) or UpsertResult(ok=False, status="error", error="No answer for this person.")
            link = links_by_email.get(person.email)
            if link is None:
                link = IntegrationRecordLink(
                    id=uuid.uuid4(), organization_id=org, provider=connection.provider, record_type=person.record_type, record_id=person.record_id,
                    external_type=LINK_TYPE, external_id=person.email, external_parent=parent, data={},
                )
                db.add(link)
                links_by_email[person.email] = link
            link.status = result.status if result.ok else "error"
            link.last_synced_at = now
            link.last_error = None if result.ok else (result.error or "Not accepted.")[:1000]
            link.sync_hash = person.values["_fingerprint"] if result.ok and result.status in ("linked", "pending") else None
            if result.ref:
                link.data = {**(link.data or {}), "ref": str(result.ref)[:200]}
            if result.ok and result.status in OPTED_OUT:
                tool_opt_outs.append(Change(email=person.email, kind=result.status))
            if result.ok:
                summary["sent"] += 1
            else:
                summary["errors"] += 1
        failed = [p.email for p in send if (results.get(p.email) and not results[p.email].ok)]
        if failed:
            first = results[failed[0]].error or "Not accepted."
            core.log(db, org, connection.provider, "sync_errors", f"{adapter.name} didn't accept {len(failed)} {'person' if len(failed) == 1 else 'people'}. First problem ({failed[0]}): {first}"[:1000],
                     level="warning", connection=connection, detail={"emails": failed[:20]})
    if leave:
        await adapter.unsubscribe(db, connection, settings, leave)
        for email in leave:
            link = links_by_email.get(email)
            if link is not None:
                link.status = "unsubscribed"
                link.data = {**(link.data or {}), "opted_out_at": _iso(now), "opted_out_in": "AIVA"}
        summary["unsubscribed_in_tool"] += len(leave)
    if tool_opt_outs:
        summary["suppressed_in_aiva"] += await suppress(db, connection, tool_opt_outs, settings)


async def push_records(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], state: dict[str, Any], deadline: float, summary: dict[str, int]) -> bool:
    """Send records changed since the cursor. Returns True when there's more to do."""

    adapter = adapter_for(connection.provider)
    org = connection.organization_id
    cursors = dict(state.get("cursor") or {})
    caught_up = dict(state.get("caught_up") or {})
    looked = 0
    backlog = False
    for record_type in settings["record_types"]:
        model = Contact if record_type == "contact" else Lead
        while True:
            if time.monotonic() > deadline or looked >= MAX_RECORDS_PER_RUN:
                backlog = True
                break
            after, after_id = (cursors.get(record_type) or [None, None])
            query = select(model).where(model.organization_id == org, model.email.is_not(None), model.email != "")
            after_time = _when(after)
            if after_time is not None and after_id:
                query = query.where(or_(model.updated_at > after_time, and_(model.updated_at == after_time, model.id > uuid.UUID(after_id))))
            records = (await db.execute(query.order_by(model.updated_at, model.id).limit(adapter.batch_size))).scalars().all()
            if not records:
                caught_up[record_type] = True
                break
            looked += len(records)
            await sync_people(db, connection, settings, await _people(db, org, record_type, list(records)), summary)
            last = records[-1]
            cursors[record_type] = [_iso(last.updated_at), str(last.id)]
            state["cursor"] = cursors
            await db.flush()
            if len(records) < adapter.batch_size:
                caught_up[record_type] = True
                break
            caught_up[record_type] = False
    state["caught_up"] = caught_up
    return backlog


# ===========================================================================
# Campaigns, engagement and CRM events
# ===========================================================================


async def record_engagement(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], events: list[Engagement]) -> int:
    """Opens and clicks of people AIVA synced become timeline activities (one per person, campaign and kind)."""

    events = [e for e in events if e.kind in ("open", "click") and normal_email(e.email)][:ENGAGEMENT_PER_RUN]
    if not events:
        return 0
    org = connection.organization_id
    adapter = adapter_for(connection.provider)
    links = {link.external_id: link for link in await _links(db, connection, settings, emails=sorted({normal_email(e.email) for e in events}))}  # type: ignore[misc]
    wanted: dict[str, Engagement] = {}
    for event in events:
        email = normal_email(event.email)
        if email in links:
            wanted.setdefault(f"em:{connection.provider}:{event.campaign_id}:{event.kind}:{email}"[:500], event)
    if not wanted:
        return 0
    seen = set(
        (await db.execute(select(Activity.external_id).where(Activity.organization_id == org, Activity.external_id.in_(list(wanted))))).scalars()
    )
    contacts: dict[uuid.UUID, uuid.UUID | None] = {}
    contact_ids = [links[normal_email(e.email)].record_id for e in wanted.values() if links[normal_email(e.email)].record_type == "contact"]  # type: ignore[index]
    if contact_ids:
        contacts = dict((await db.execute(select(Contact.id, Contact.account_id).where(Contact.organization_id == org, Contact.id.in_(contact_ids)))).all())
    made = 0
    for external_id, event in wanted.items():
        if external_id in seen:
            continue
        link = links[normal_email(event.email)]  # type: ignore[index]
        title = (event.campaign_title or "an email campaign")[:200]
        subject = f"Opened “{title}” ({adapter.name})" if event.kind == "open" else f"Clicked a link in “{title}” ({adapter.name})"
        db.add(
            Activity(
                id=uuid.uuid4(),
                organization_id=org,
                contact_id=link.record_id if link.record_type == "contact" else None,
                account_id=contacts.get(link.record_id) if link.record_type == "contact" else None,
                lead_id=link.record_id if link.record_type == "lead" else None,
                activity_type="email",
                subject=subject[:300],
                body=(event.url or None) if event.kind == "click" else None,
                direction="inbound",
                occurred_at=event.at or utcnow(),
                external_id=external_id,
                activity_metadata={"source": "email_marketing", "provider": connection.provider, "campaign_id": event.campaign_id, "event": event.kind, "url": event.url},
            )
        )
        made += 1
    return made


async def refresh_campaigns(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], state: dict[str, Any], deadline: float, summary: dict[str, int]) -> None:
    adapter = adapter_for(connection.provider)
    if not adapter.capabilities.get("campaigns"):
        return
    org = connection.organization_id
    now = utcnow()
    campaigns = await adapter.campaigns(db, connection, settings, now - timedelta(days=CAMPAIGN_DAYS))
    parent = list_key(settings)
    existing = {
        row.external_id: row
        for row in (
            await db.execute(
                select(IntegrationObject).where(
                    IntegrationObject.organization_id == org, IntegrationObject.provider == connection.provider, IntegrationObject.object_type == "campaign",
                    IntegrationObject.external_parent == parent, IntegrationObject.external_id.in_([c.id for c in campaigns] or [""]),
                )
            )
        ).scalars()
    }
    for campaign in campaigns:
        row = existing.get(campaign.id)
        if row is None:
            row = IntegrationObject(id=uuid.uuid4(), organization_id=org, provider=connection.provider, object_type="campaign", external_id=campaign.id[:200], external_parent=parent)
            db.add(row)
        row.title = campaign.title[:500]
        row.status = (campaign.status or "sent")[:60]
        row.occurred_at = campaign.sent_at
        row.url = campaign.url[:1000] if campaign.url and campaign.url.startswith("https://") else None
        row.data = {k: v for k, v in campaign.stats.items() if v is not None}
        row.external_updated_at = now
    summary["campaigns"] = len(campaigns)
    if not adapter.capabilities.get("engagement"):
        return
    windows = dict(state.get("engagement") or {})
    recent = [c for c in campaigns if c.sent_at and c.sent_at > now - timedelta(days=ENGAGEMENT_DAYS)][:3]
    for campaign in recent:
        if time.monotonic() > deadline:
            break
        since = _when(windows.get(campaign.id))
        events = await adapter.engagement(db, connection, settings, campaign, since, ENGAGEMENT_PER_RUN)
        summary["activities"] += await record_engagement(db, connection, settings, events)
        windows[campaign.id] = _iso(now - timedelta(minutes=5))
    keep = {c.id for c in recent}
    state["engagement"] = {k: v for k, v in windows.items() if k in keep}


async def crm_events(db: AsyncSession, organization_id: uuid.UUID, wanted: list[str], cursors: dict[str, str], limit: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """CRM events since each event's cursor, as Klaviyo events (email, properties, value, unique id)."""

    from app.models.opportunity import Opportunity
    from app.models.pipeline_stage import PipelineStage
    from app.models.platform import Quote

    now = utcnow()
    out: list[dict[str, Any]] = []
    cursors = dict(cursors)
    for event in wanted:
        since = _when(cursors.get(event))
        if since is None:
            cursors[event] = _iso(now)  # start from now; history isn't replayed
            continue
        remaining = limit - len(out)
        if remaining <= 0:
            break
        rows: list[tuple[datetime, str | None, dict[str, Any], Any, str | None, str]] = []
        if event in ("lead_created", "contact_created"):
            model = Lead if event == "lead_created" else Contact
            records = (
                await db.execute(select(model).where(model.organization_id == organization_id, model.created_at > since).order_by(model.created_at).limit(remaining))
            ).scalars().all()
            for r in records:
                props = {"first_name": r.first_name, "last_name": r.last_name, "job_title": r.job_title}
                if event == "lead_created":
                    props.update({"company": r.company_name, "source": r.source, "status": r.status})
                rows.append((r.created_at, r.email, props, None, None, str(r.id)))
        elif event in ("deal_won", "deal_lost"):
            category = "won" if event == "deal_won" else "lost"
            found = (
                await db.execute(
                    select(Opportunity, Contact.email)
                    .join(PipelineStage, PipelineStage.id == Opportunity.stage_id)
                    .join(Contact, Contact.id == Opportunity.primary_contact_id)
                    .where(Opportunity.organization_id == organization_id, PipelineStage.category == category, Opportunity.closed_at.is_not(None), Opportunity.closed_at > since)
                    .order_by(Opportunity.closed_at)
                    .limit(remaining)
                )
            ).all()
            for deal, email in found:
                rows.append((deal.closed_at, email, {"deal": deal.name, "loss_reason": deal.loss_reason if category == "lost" else None}, deal.amount if category == "won" else None, deal.currency, str(deal.id)))
        elif event == "quote_accepted":
            found = (
                await db.execute(
                    select(Quote, Contact.email)
                    .join(Contact, Contact.id == Quote.contact_id)
                    .where(Quote.organization_id == organization_id, Quote.accepted_at.is_not(None), Quote.accepted_at > since)
                    .order_by(Quote.accepted_at)
                    .limit(remaining)
                )
            ).all()
            for quote, email in found:
                rows.append((quote.accepted_at, email, {"quote": quote.name, "number": quote.number}, quote.total, quote.currency, str(quote.id)))
        for at, email, props, value, currency, record_id in rows:
            address = normal_email(email)
            cursors[event] = _iso(at) or cursors[event]
            if not address:
                continue
            out.append({
                "event": event, "email": address, "time": at, "unique_id": f"aiva-{event}-{record_id}",
                "properties": {k: v for k, v in props.items() if v not in (None, "")}, "value": value, "currency": currency,
            })
    return out, cursors


async def push_events(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], state: dict[str, Any], summary: dict[str, int]) -> None:
    adapter = adapter_for(connection.provider)
    if not adapter.capabilities.get("events") or not settings.get("events"):
        return
    from app.services.prospecting import suppressed_emails

    events, cursors = await crm_events(db, connection.organization_id, settings["events"], dict(state.get("events_cursor") or {}), EVENTS_PER_RUN)
    blocked = await suppressed_emails(db, connection.organization_id, [e["email"] for e in events])
    events = [e for e in events if e["email"] not in blocked]
    if events:
        summary["events"] += await adapter.send_events(db, connection, events)
    state["events_cursor"] = cursors


# ===========================================================================
# Webhook registration
# ===========================================================================


async def ensure_webhook(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any], state: dict[str, Any], manual: bool) -> None:
    adapter = adapter_for(connection.provider)
    if not adapter.capabilities.get("webhooks"):
        return
    url = core.webhook_url(connection.provider, connection.webhook_token)
    hook = dict(state.get("webhook") or {})
    if not url or not public_address():
        state["webhook_note"] = "AIVA's address isn't public https, so the tool can't call it. AIVA checks for unsubscribes every 15 minutes instead."
        return
    state.pop("webhook_note", None)
    if hook.get("id") and hook.get("url") == url and str(hook.get("list_id") or "") == list_key(settings):
        return
    checked = _when(state.get("webhook_checked_at"))
    if not manual and checked and checked > utcnow() - WEBHOOK_RETRY and state.get("webhook_error"):
        return
    state["webhook_checked_at"] = _iso(utcnow())
    try:
        if hook.get("id"):
            await adapter.remove_webhook(db, connection, hook)
        info = await adapter.register_webhook(db, connection, settings, url)
    except (RateLimited, AuthFailed):
        raise
    except IntegrationError as exc:
        if state.get("webhook_error") != str(exc):
            core.log(db, connection.organization_id, connection.provider, "webhook_failed", f"Couldn't register AIVA's webhook in {adapter.name}: {exc} AIVA checks for unsubscribes every 15 minutes instead.", level="warning", connection=connection)
        state["webhook_error"] = str(exc)[:500]
        state["webhook"] = None
        return
    state["webhook"] = {**(info or {}), "url": url} if info else None
    state.pop("webhook_error", None)
    if info:
        core.log(db, connection.organization_id, connection.provider, "webhook_registered", f"{adapter.name} now tells AIVA about unsubscribes right away.", level="success", connection=connection)


async def remove_webhook(db: AsyncSession, connection: IntegrationConnection) -> None:
    state = state_of(connection)
    hook = state.get("webhook") or {}
    if hook.get("id"):
        try:
            await adapter_for(connection.provider).remove_webhook(db, connection, hook)
        except IntegrationError as exc:
            core.log(db, connection.organization_id, connection.provider, "webhook_cleanup", f"Couldn't remove AIVA's webhook: {exc}", level="warning", connection=connection)
    state["webhook"] = None
    save_state(connection, state)


# ===========================================================================
# One run
# ===========================================================================


def _due(value: Any, every: timedelta, now: datetime) -> bool:
    last = _when(value)
    return last is None or last + every <= now


async def sync_connection(db: AsyncSession, connection: IntegrationConnection, *, budget: float = CONNECTION_BUDGET_SECONDS, manual: bool = False) -> dict[str, Any]:
    adapter = adapter_for(connection.provider)
    deadline = time.monotonic() + budget
    now = utcnow()
    settings = await settings_for(db, connection)
    state = state_of(connection)
    summary = {"sent": 0, "unchanged": 0, "skipped": 0, "errors": 0, "unsubscribed_in_tool": 0, "suppressed_in_aiva": 0, "activities": 0, "events": 0}
    backlog = False
    problem: str | None = None
    retry_after: timedelta | None = None
    try:
        if settings["sync_paused"]:
            problem = "Sync is paused."
        elif adapter.list_required and not settings["list_id"]:
            problem = f"Choose the {adapter.list_label.lower()} to sync to."
        else:
            if state.get("list_id", "") != list_key(settings):
                # A different list: start over there (links for the old list stay as history).
                old = state.get("webhook")
                state = {"list_id": list_key(settings), "suppression_cursor": None, "webhook": old}
            await ensure_webhook(db, connection, settings, state, manual)
            if manual or _due(state.get("changes_at"), POLL_EVERY, now):
                changes, cursor = await adapter.changes(db, connection, settings, _when(state.get("changes_cursor")), CHANGES_LIMIT)
                summary["suppressed_in_aiva"] += await suppress(db, connection, changes, settings)
                state["changes_cursor"] = _iso(cursor)
                state["changes_at"] = _iso(now)
            summary["unsubscribed_in_tool"] += await push_suppressions(db, connection, settings, state, deadline)
            backlog = await push_records(db, connection, settings, state, deadline, summary)
            if time.monotonic() < deadline and (manual or _due(state.get("campaigns_at"), CAMPAIGNS_EVERY, now)):
                await refresh_campaigns(db, connection, settings, state, deadline, summary)
                state["campaigns_at"] = _iso(now)
            if time.monotonic() < deadline:
                await push_events(db, connection, settings, state, summary)
    except RateLimited as exc:
        problem = str(exc)
        backlog = True
    except AuthFailed as exc:
        problem = str(exc)
        connection.status = "error"
        retry_after = timedelta(hours=1)  # a refused key won't start working by itself
    except IntegrationError as exc:
        problem = str(exc)
    if problem and problem != state.get("last_error") and not problem.startswith(("Sync is paused", "Choose the")):
        core.log(db, connection.organization_id, connection.provider, "sync_failed", f"Sync stopped: {problem}", level="error", connection=connection)
    if not problem and connection.status == "error":
        connection.status = "connected"
        connection.last_error = None
    state.update(
        {
            "last_run_at": _iso(now),
            "next_run_at": _iso(now + (retry_after or (BACKLOG_EVERY if backlog else SYNC_EVERY))),
            "last_error": problem,
            "last_result": summary,
            "backlog": backlog,
        }
    )
    save_state(connection, state)
    connection.last_sync_at = now
    if summary["sent"] or summary["unsubscribed_in_tool"]:
        logger.info("Email marketing sync %s %s: %s", connection.provider, connection.organization_id, summary)
    return {"summary": summary, "problem": problem, "backlog": backlog}


async def run_due(db: AsyncSession) -> int:
    """Scheduler entry point: sync the connections that are due, within a time budget."""

    db.sync_session.info["automation_disabled"] = True
    started = time.monotonic()
    now = utcnow()
    candidates = (
        await db.execute(
            select(IntegrationConnection.id, IntegrationConnection.organization_id, IntegrationConnection.provider, IntegrationConnection.config)
            .where(IntegrationConnection.provider.in_(KEYS), IntegrationConnection.user_id.is_(None), IntegrationConnection.status.in_(("connected", "error")))
            # Least recently looked at first (skipped ones included), so none waits forever.
            .order_by(core.scheduler_order())
            .limit(200)
        )
    ).all()
    plans: dict[uuid.UUID, bool] = {}
    examined: list[uuid.UUID] = []
    try:
        return await _run_candidates(db, candidates, started, now, plans, examined)
    finally:
        await db.rollback()
        await core.mark_seen_ids(db, examined)


async def _run_candidates(db: AsyncSession, candidates: Any, started: float, now: Any, plans: dict[uuid.UUID, bool], examined: list[uuid.UUID]) -> int:
    handled = 0
    ran = 0
    for connection_id, organization_id, provider, config in candidates:
        if ran >= MAX_CONNECTIONS_PER_RUN or time.monotonic() - started > RUN_BUDGET_SECONDS:
            break
        examined.append(connection_id)
        next_at = _when(((config or {}).get("em_sync") or {}).get("next_run_at"))
        if next_at is not None and next_at > now:
            continue
        if not await core.is_enabled(db, organization_id, provider) or not await plan_allows(db, organization_id, plans):
            continue
        connection = (
            await db.execute(select(IntegrationConnection).where(IntegrationConnection.id == connection_id).with_for_update(skip_locked=True))
        ).scalar_one_or_none()
        if connection is None:
            continue  # someone is syncing it right now ("Sync now")
        ran += 1
        remaining = RUN_BUDGET_SECONDS - (time.monotonic() - started)
        try:
            result = await sync_connection(db, connection, budget=max(2.0, min(CONNECTION_BUDGET_SECONDS, remaining)))
            handled += result["summary"]["sent"] + result["summary"]["unsubscribed_in_tool"] + result["summary"]["suppressed_in_aiva"]
            await db.commit()
        except Exception:  # noqa: BLE001 - one workspace's problem mustn't stop the others
            logger.exception("Email marketing sync failed for %s", provider)
            await db.rollback()
    return handled
