"""Social publishing and website visit tracking."""

from __future__ import annotations

import json
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.core.crypto import decrypt, encrypt
from app.db.session import get_db
from app.models.contact import Contact
from app.models.engagement import SocialChannel, SocialPost, TrackingSite, WebPageView, WebSession, WebVisitor
from app.models.lead import Lead
from app.services import social
from app.services.automation import record_audit
from app.services.lookups import names_for

logger = logging.getLogger("aiva.engagement")

router = APIRouter(tags=["Social and website visits"])
public_router = APIRouter(prefix="/public", tags=["Public"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(value: Any) -> str | None:
    return str(value) if value else None


NETWORKS = {"linkedin": "LinkedIn", "x": "X", "facebook": "Facebook", "instagram": "Instagram"}

# ===========================================================================
# Social channels
# ===========================================================================


class ChannelPayload(BaseModel):
    network: str = Field(pattern="^(linkedin|x|facebook|instagram)$")
    external_id: str = Field(default="", max_length=200)
    access_token: str = Field(min_length=10, max_length=5000)
    name: str | None = Field(default=None, max_length=160)
    token_expires_at: datetime | None = None


def channel_out(channel: SocialChannel) -> dict[str, Any]:
    return {
        "id": str(channel.id),
        "network": channel.network,
        "network_label": NETWORKS.get(channel.network, channel.network),
        "name": channel.name,
        "handle": channel.handle,
        "external_id": channel.external_id,
        "status": channel.status,
        "last_error": channel.last_error,
        "last_checked_at": channel.last_checked_at,
        "token_expires_at": channel.token_expires_at,
        "created_at": channel.created_at,
    }


@router.get("/social/channels", dependencies=[Depends(require_feature("social"))])
async def list_channels(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    channels = (
        await db.execute(select(SocialChannel).where(SocialChannel.organization_id == principal.organization_id).order_by(SocialChannel.network, SocialChannel.name))
    ).scalars().all()
    return {"items": [channel_out(c) for c in channels], "can_manage": principal.can("marketing.social"), "limits": social.LIMITS}


@router.post("/social/channels", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("social"))])
async def connect_channel(
    payload: ChannelPayload,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    token = payload.access_token.strip()
    external_id = payload.external_id.strip()
    if payload.network in {"facebook", "instagram", "linkedin"} and not external_id:
        what = {"facebook": "Page ID", "instagram": "Instagram business account ID", "linkedin": "organization or member ID"}[payload.network]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Enter the {what}.")
    try:
        found = await social.verify(payload.network, external_id, token)
    except social.SocialError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{NETWORKS[payload.network]} didn't accept that token: {exc}") from exc
    external_id = external_id or str(found.get("id") or "")
    if not external_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Couldn't work out the account ID. Enter it and try again.")
    existing = (
        await db.execute(
            select(SocialChannel).where(
                SocialChannel.organization_id == principal.organization_id,
                SocialChannel.network == payload.network,
                SocialChannel.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    channel = existing or SocialChannel(id=uuid.uuid4(), organization_id=principal.organization_id, network=payload.network, external_id=external_id)
    channel.name = (payload.name or "").strip() or found.get("name") or found.get("handle") or NETWORKS[payload.network]
    channel.handle = found.get("handle")
    channel.access_token = encrypt(token)
    channel.token_expires_at = payload.token_expires_at
    channel.status = "connected"
    channel.last_error = None
    channel.last_checked_at = utcnow()
    channel.connected_by_user_id = principal.user_id
    if existing is None:
        db.add(channel)
    record_audit(db, organization_id=principal.organization_id, action="connect", entity_type="social_channel", entity_id=channel.id, entity_label=f"{NETWORKS[channel.network]}: {channel.name}")
    await db.commit()
    return channel_out(channel)


async def _channel(db: AsyncSession, principal: Principal, channel_id: uuid.UUID) -> SocialChannel:
    channel = await db.get(SocialChannel, channel_id)
    if channel is None or channel.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Social account not found.")
    return channel


@router.post("/social/channels/{channel_id}/check", dependencies=[Depends(require_feature("social"))])
async def check_channel(
    channel_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    channel = await _channel(db, principal, channel_id)
    try:
        found = await social.verify(channel.network, channel.external_id, decrypt(channel.access_token) or "")
        channel.status = "connected"
        channel.last_error = None
        channel.handle = found.get("handle") or channel.handle
    except social.SocialError as exc:
        channel.status = "error"
        channel.last_error = str(exc)
    channel.last_checked_at = utcnow()
    await db.commit()
    return channel_out(channel)


@router.delete("/social/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("social"))])
async def disconnect_channel(
    channel_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    channel = await _channel(db, principal, channel_id)
    record_audit(db, organization_id=principal.organization_id, action="disconnect", entity_type="social_channel", entity_id=channel.id, entity_label=f"{NETWORKS[channel.network]}: {channel.name}")
    await db.delete(channel)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Social posts
# ===========================================================================


def _url(value: str | None) -> str | None:
    value = (value or "").strip()
    if value and not re.match(r"^https?://[^\s]+$", value, re.I):
        raise ValueError("Links must start with https://")
    return value or None


class PostPayload(BaseModel):
    text: str = Field(min_length=1, max_length=63206)
    link_url: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=1000)
    channel_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    scheduled_at: datetime | None = None
    publish_now: bool = False
    campaign: str | None = Field(default=None, max_length=120)

    @field_validator("link_url", "image_url")
    @classmethod
    def _links(cls, value: str | None) -> str | None:
        return _url(value)


class PostUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=63206)
    link_url: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=1000)
    channel_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=20)
    scheduled_at: datetime | None = None
    campaign: str | None = Field(default=None, max_length=120)

    @field_validator("link_url", "image_url")
    @classmethod
    def _links(cls, value: str | None) -> str | None:
        return _url(value)


def _length_for(network: str, text: str, link: str | None) -> int:
    if network == "x":
        # X counts every link as 23 characters.
        body = re.sub(r"https?://\S+", "x" * 23, text)
        return len(body) + (24 if link and link not in text else 0)
    return len(text)


async def _post_channels(db: AsyncSession, organization_id: uuid.UUID, ids: list[Any]) -> list[SocialChannel]:
    channels = (
        await db.execute(select(SocialChannel).where(SocialChannel.organization_id == organization_id, SocialChannel.id.in_([uuid.UUID(str(i)) for i in ids])))
    ).scalars().all()
    if len(channels) != len(set(str(i) for i in ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One of the social accounts isn't connected to this workspace.")
    return list(channels)


def _check_post(channels: list[SocialChannel], text: str, link: str | None, image: str | None) -> None:
    for channel in channels:
        limit = social.LIMITS.get(channel.network, 3000)
        if _length_for(channel.network, text, link) > limit:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Too long for {NETWORKS[channel.network]} ({limit} characters max).")
        if channel.network == "instagram" and not image:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Instagram posts need an image link.")


async def post_out(db: AsyncSession, post: SocialPost) -> dict[str, Any]:
    channels = {
        str(c.id): c
        for c in (
            await db.execute(select(SocialChannel).where(SocialChannel.id.in_([uuid.UUID(i) for i in post.channel_ids or []] or [uuid.uuid4()])))
        ).scalars()
    }
    authors = await names_for(db, post.organization_id, "user", [post.created_by_user_id])
    return {
        "id": str(post.id),
        "text": post.text,
        "link_url": post.link_url,
        "image_url": post.image_url,
        "status": post.status,
        "scheduled_at": post.scheduled_at,
        "published_at": post.published_at,
        "campaign": post.campaign,
        "channel_ids": post.channel_ids or [],
        "channels": [
            {
                "id": cid,
                "name": channels[cid].name if cid in channels else "Disconnected account",
                "network": channels[cid].network if cid in channels else None,
                "result": (post.results or {}).get(cid),
            }
            for cid in post.channel_ids or []
        ],
        "created_by": authors.get(str(post.created_by_user_id)),
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


async def publish_post(db: AsyncSession, post: SocialPost) -> None:
    """Send a post to each of its channels and record the outcome per channel."""

    post.status = "publishing"
    results = dict(post.results or {})
    channels = {
        str(c.id): c
        for c in (await db.execute(select(SocialChannel).where(SocialChannel.id.in_([uuid.UUID(i) for i in post.channel_ids or []] or [uuid.uuid4()])))).scalars()
    }
    ok = 0
    for cid in post.channel_ids or []:
        if (results.get(cid) or {}).get("status") == "published":
            ok += 1
            continue
        channel = channels.get(cid)
        if channel is None:
            results[cid] = {"status": "failed", "error": "The account was disconnected.", "at": utcnow().isoformat()}
            continue
        try:
            done = await social.publish(channel.network, channel.external_id, decrypt(channel.access_token) or "", post.text, post.link_url, post.image_url)
            results[cid] = {"status": "published", "external_id": done.external_id, "url": done.url, "at": utcnow().isoformat()}
            ok += 1
        except social.SocialError as exc:
            message = str(exc)
            results[cid] = {"status": "failed", "error": message, "at": utcnow().isoformat()}
            if any(word in message.lower() for word in ("token", "expired", "unauthorized", "permission", "401", "403")):
                channel.status = "error"
                channel.last_error = message
    post.results = results
    total = len(post.channel_ids or [])
    post.status = "published" if ok == total else "partial" if ok else "failed"
    if ok:
        post.published_at = post.published_at or utcnow()


@router.get("/social/posts", dependencies=[Depends(require_feature("social"))])
async def list_posts(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(draft|scheduled|published|partial|failed)$"),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(SocialPost).where(SocialPost.organization_id == principal.organization_id)
    if status_filter:
        query = query.where(SocialPost.status == status_filter)
    posts = (await db.execute(query.order_by(func.coalesce(SocialPost.published_at, SocialPost.scheduled_at, SocialPost.created_at).desc()).limit(300))).scalars().all()
    every = (await db.execute(select(SocialPost.status, func.count()).where(SocialPost.organization_id == principal.organization_id).group_by(SocialPost.status))).all()
    return {"items": [await post_out(db, p) for p in posts], "counts": {s: c for s, c in every}, "can_manage": principal.can("marketing.social")}


@router.post("/social/posts", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("social"))])
async def create_post(
    payload: PostPayload,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    channels = await _post_channels(db, principal.organization_id, payload.channel_ids)
    _check_post(channels, payload.text, payload.link_url, payload.image_url)
    if payload.scheduled_at and not payload.publish_now and payload.scheduled_at <= utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a time in the future, or publish now.")
    post = SocialPost(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        text=payload.text.strip(),
        link_url=payload.link_url,
        image_url=payload.image_url,
        channel_ids=list(dict.fromkeys(str(c) for c in payload.channel_ids)),
        status="scheduled" if payload.scheduled_at and not payload.publish_now else "draft",
        scheduled_at=None if payload.publish_now else payload.scheduled_at,
        results={},
        campaign=(payload.campaign or "").strip() or None,
        created_by_user_id=principal.user_id,
    )
    db.add(post)
    if payload.publish_now:
        disconnected = [c.name for c in channels if c.status != "connected"]
        if disconnected:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Reconnect {', '.join(disconnected)} before publishing.")
        await publish_post(db, post)
    await db.commit()
    return await post_out(db, post)


async def _post(db: AsyncSession, principal: Principal, post_id: uuid.UUID) -> SocialPost:
    post = await db.get(SocialPost, post_id)
    if post is None or post.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found.")
    return post


@router.patch("/social/posts/{post_id}", dependencies=[Depends(require_feature("social"))])
async def update_post(
    post_id: uuid.UUID,
    payload: PostUpdate,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    post = await _post(db, principal, post_id)
    if post.status not in {"draft", "scheduled", "failed"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published posts can't be edited here. Edit them on the network.")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("channel_ids") is not None:
        changes["channel_ids"] = list(dict.fromkeys(str(c) for c in changes["channel_ids"]))
    for key, value in changes.items():
        if value is None and key in {"text", "channel_ids"}:
            continue
        setattr(post, key, value.strip() if key == "text" else value)
    channels = await _post_channels(db, principal.organization_id, post.channel_ids)
    _check_post(channels, post.text, post.link_url, post.image_url)
    if "scheduled_at" in changes:
        if post.scheduled_at and post.scheduled_at <= utcnow():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a time in the future.")
        post.status = "scheduled" if post.scheduled_at else "draft"
    if post.status == "failed":
        post.status = "scheduled" if post.scheduled_at and post.scheduled_at > utcnow() else "draft"
        post.results = {}
    await db.commit()
    return await post_out(db, post)


@router.post("/social/posts/{post_id}/publish", dependencies=[Depends(require_feature("social"))])
async def publish_now(
    post_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    post = await _post(db, principal, post_id)
    if post.status == "published":
        raise HTTPException(status.HTTP_409_CONFLICT, "This post is already published.")
    if post.status == "publishing":
        raise HTTPException(status.HTTP_409_CONFLICT, "This post is being published right now.")
    channels = await _post_channels(db, principal.organization_id, post.channel_ids)
    disconnected = [c.name for c in channels if c.status != "connected"]
    if disconnected:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Reconnect {', '.join(disconnected)} before publishing.")
    post.scheduled_at = None
    await publish_post(db, post)
    await db.commit()
    return await post_out(db, post)


@router.post("/social/posts/{post_id}/unschedule", dependencies=[Depends(require_feature("social"))])
async def unschedule(
    post_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    post = await _post(db, principal, post_id)
    if post.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only scheduled posts can be moved back to drafts.")
    post.status = "draft"
    post.scheduled_at = None
    await db.commit()
    return await post_out(db, post)


@router.delete("/social/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("social"))])
async def delete_post(
    post_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.social")),
    db: AsyncSession = Depends(get_db),
):
    post = await _post(db, principal, post_id)
    if post.status == "publishing":
        raise HTTPException(status.HTTP_409_CONFLICT, "This post is being published right now.")
    await db.delete(post)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def publish_due_posts(db: AsyncSession) -> int:
    """Publish scheduled posts whose time has come (called by the scheduler)."""

    posts = (
        await db.execute(
            select(SocialPost).where(SocialPost.status == "scheduled", SocialPost.scheduled_at <= utcnow()).order_by(SocialPost.scheduled_at).limit(20)
        )
    ).scalars().all()
    for post in posts:
        await publish_post(db, post)
        await db.commit()
    return len(posts)


# ===========================================================================
# Website visits: sites
# ===========================================================================


def _domain(value: str) -> str:
    value = value.strip().lower()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.split("/")[0].split(":")[0].removeprefix("www.")
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$|^localhost$", value):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter your website's domain, like example.com.")
    return value


class SitePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    domain: str = Field(min_length=3, max_length=255)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    domain: str | None = Field(default=None, min_length=3, max_length=255)
    is_active: bool | None = None


async def site_out(db: AsyncSession, site: TrackingSite) -> dict[str, Any]:
    since = utcnow() - timedelta(days=30)
    visitors = (
        await db.execute(select(func.count()).select_from(WebVisitor).where(WebVisitor.site_id == site.id, WebVisitor.last_seen_at >= since))
    ).scalar_one()
    return {
        "id": str(site.id),
        "name": site.name,
        "domain": site.domain,
        "site_key": site.site_key,
        "is_active": site.is_active,
        "last_hit_at": site.last_hit_at,
        "visitors_30d": visitors,
        "created_at": site.created_at,
    }


@router.get("/visits/sites", dependencies=[Depends(require_feature("website_visits"))])
async def list_sites(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    sites = (await db.execute(select(TrackingSite).where(TrackingSite.organization_id == principal.organization_id).order_by(TrackingSite.name))).scalars().all()
    return {"items": [await site_out(db, s) for s in sites], "can_manage": principal.can("marketing.manage")}


@router.post("/visits/sites", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("website_visits"))])
async def create_site(
    payload: SitePayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    site = TrackingSite(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=payload.name.strip(),
        domain=_domain(payload.domain),
        site_key="ws_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24],
        is_active=True,
        created_by_user_id=principal.user_id,
    )
    db.add(site)
    await db.commit()
    return await site_out(db, site)


async def _site(db: AsyncSession, principal: Principal, site_id: uuid.UUID) -> TrackingSite:
    site = await db.get(TrackingSite, site_id)
    if site is None or site.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Website not found.")
    return site


@router.patch("/visits/sites/{site_id}", dependencies=[Depends(require_feature("website_visits"))])
async def update_site(
    site_id: uuid.UUID,
    payload: SiteUpdate,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    site = await _site(db, principal, site_id)
    if payload.name:
        site.name = payload.name.strip()
    if payload.domain:
        site.domain = _domain(payload.domain)
    if payload.is_active is not None:
        site.is_active = payload.is_active
    await db.commit()
    return await site_out(db, site)


@router.delete("/visits/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("website_visits"))])
async def delete_site(
    site_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    site = await _site(db, principal, site_id)
    await db.delete(site)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Website visits: visitors
# ===========================================================================


async def _match_person(db: AsyncSession, visitor: WebVisitor) -> None:
    if not visitor.email or (visitor.contact_id or visitor.lead_id):
        return
    email = visitor.email.lower()
    contact = (
        await db.execute(select(Contact.id).where(Contact.organization_id == visitor.organization_id, func.lower(Contact.email) == email).limit(1))
    ).scalar_one_or_none()
    if contact:
        visitor.contact_id = contact
        return
    lead = (
        await db.execute(select(Lead.id).where(Lead.organization_id == visitor.organization_id, func.lower(Lead.email) == email).limit(1))
    ).scalar_one_or_none()
    if lead:
        visitor.lead_id = lead


async def visitor_out(db: AsyncSession, visitor: WebVisitor, site_names: dict[uuid.UUID, str] | None = None) -> dict[str, Any]:
    contacts = await names_for(db, visitor.organization_id, "contact", [visitor.contact_id])
    lead_name = None
    if visitor.lead_id:
        lead = await db.get(Lead, visitor.lead_id)
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else None
    label = contacts.get(str(visitor.contact_id)) or lead_name or visitor.name or visitor.email
    return {
        "id": str(visitor.id),
        "label": label or f"Visitor {visitor.visitor_key[:6]}",
        "is_identified": bool(visitor.email or visitor.contact_id or visitor.lead_id),
        "email": visitor.email,
        "name": visitor.name,
        "site_id": str(visitor.site_id),
        "site_name": (site_names or {}).get(visitor.site_id),
        "contact_id": _id(visitor.contact_id),
        "contact_name": contacts.get(str(visitor.contact_id)),
        "lead_id": _id(visitor.lead_id),
        "lead_name": lead_name,
        "first_seen_at": visitor.first_seen_at,
        "last_seen_at": visitor.last_seen_at,
        "visit_count": visitor.visit_count,
        "page_view_count": visitor.page_view_count,
        "total_seconds": visitor.total_seconds,
        "first_referrer": visitor.first_referrer,
        "first_source": visitor.first_source or {},
        "last_url": visitor.last_url,
        "device": visitor.device,
        "browser": visitor.browser,
        "language": visitor.language,
        "timezone": visitor.timezone,
    }


def _source_label(referrer: str | None, utm_source: str | None) -> str:
    if utm_source:
        return utm_source
    if not referrer:
        return "Direct"
    host = (urlsplit(referrer).hostname or "").removeprefix("www.")
    for needle, label in (("google.", "Google"), ("bing.", "Bing"), ("linkedin.", "LinkedIn"), ("facebook.", "Facebook"), ("t.co", "X"), ("twitter.", "X"), ("duckduckgo.", "DuckDuckGo")):
        if needle in host:
            return label
    return host or "Direct"


@router.get("/visits/visitors", dependencies=[Depends(require_feature("website_visits"))])
async def list_visitors(
    site_id: uuid.UUID | None = None,
    identified: bool | None = None,
    search: str | None = Query(default=None, max_length=200),
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    since = utcnow() - timedelta(days=days)
    query = select(WebVisitor).where(WebVisitor.organization_id == org, WebVisitor.last_seen_at >= since)
    if site_id:
        query = query.where(WebVisitor.site_id == site_id)
    if identified is True:
        query = query.where(or_(WebVisitor.email.is_not(None), WebVisitor.contact_id.is_not(None), WebVisitor.lead_id.is_not(None)))
    elif identified is False:
        query = query.where(WebVisitor.email.is_(None), WebVisitor.contact_id.is_(None), WebVisitor.lead_id.is_(None))
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(WebVisitor.email.ilike(like), WebVisitor.name.ilike(like), WebVisitor.last_url.ilike(like)))
    visitors = (await db.execute(query.order_by(WebVisitor.last_seen_at.desc()).limit(300))).scalars().all()
    sites = {s.id: s.name for s in (await db.execute(select(TrackingSite).where(TrackingSite.organization_id == org))).scalars()}

    view_filter = [WebPageView.organization_id == org, WebPageView.viewed_at >= since]
    session_filter = [WebSession.organization_id == org, WebSession.started_at >= since]
    if site_id:
        session_filter.append(WebSession.site_id == site_id)
        view_filter.append(WebPageView.session_id.in_(select(WebSession.id).where(WebSession.site_id == site_id)))
    pages = (
        await db.execute(
            select(WebPageView.path, func.count(), func.count(func.distinct(WebPageView.visitor_id)))
            .where(*view_filter)
            .group_by(WebPageView.path)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    sessions = (await db.execute(select(WebSession.referrer, WebSession.utm_source, WebSession.page_count, WebSession.duration_seconds).where(*session_filter))).all()
    sources: dict[str, int] = {}
    for referrer, utm, *_ in sessions:
        label = _source_label(referrer, utm)
        sources[label] = sources.get(label, 0) + 1
    total_views = sum(s[2] for s in sessions)
    unique = (
        await db.execute(
            select(func.count(func.distinct(WebSession.visitor_id))).where(*session_filter)
        )
    ).scalar_one()
    known = (
        await db.execute(
            select(func.count()).select_from(WebVisitor).where(
                WebVisitor.organization_id == org,
                WebVisitor.last_seen_at >= since,
                or_(WebVisitor.email.is_not(None), WebVisitor.contact_id.is_not(None), WebVisitor.lead_id.is_not(None)),
                *([WebVisitor.site_id == site_id] if site_id else []),
            )
        )
    ).scalar_one()
    return {
        "items": [await visitor_out(db, v, sites) for v in visitors],
        "summary": {
            "visitors": unique,
            "identified": known,
            "sessions": len(sessions),
            "page_views": total_views,
            "pages_per_visit": round(total_views / len(sessions), 1) if sessions else 0,
            "avg_seconds": round(sum(s[3] for s in sessions) / len(sessions)) if sessions else 0,
            "top_pages": [{"path": path, "views": views, "visitors": people} for path, views, people in pages],
            "top_sources": sorted(({"source": k, "sessions": v} for k, v in sources.items()), key=lambda e: -e["sessions"])[:8],
        },
        "has_sites": bool(sites),
    }


async def _visitor(db: AsyncSession, principal: Principal, visitor_id: uuid.UUID) -> WebVisitor:
    visitor = await db.get(WebVisitor, visitor_id)
    if visitor is None or visitor.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visitor not found.")
    return visitor


async def _sessions_for(db: AsyncSession, visitor_ids: list[uuid.UUID], limit: int = 30) -> list[dict]:
    sessions = (
        await db.execute(select(WebSession).where(WebSession.visitor_id.in_(visitor_ids or [uuid.uuid4()])).order_by(WebSession.started_at.desc()).limit(limit))
    ).scalars().all()
    views = (
        await db.execute(select(WebPageView).where(WebPageView.session_id.in_([s.id for s in sessions] or [uuid.uuid4()])).order_by(WebPageView.viewed_at))
    ).scalars().all()
    by_session: dict[uuid.UUID, list[dict]] = {}
    for view in views:
        by_session.setdefault(view.session_id, []).append(
            {"url": view.url, "path": view.path, "title": view.title, "viewed_at": view.viewed_at, "seconds": view.seconds}
        )
    return [
        {
            "id": str(s.id),
            "started_at": s.started_at,
            "duration_seconds": s.duration_seconds,
            "page_count": s.page_count,
            "source": _source_label(s.referrer, s.utm_source),
            "referrer": s.referrer,
            "utm_source": s.utm_source,
            "utm_medium": s.utm_medium,
            "utm_campaign": s.utm_campaign,
            "landing_url": s.landing_url,
            "pages": by_session.get(s.id, []),
        }
        for s in sessions
    ]


@router.get("/visits/visitors/{visitor_id}", dependencies=[Depends(require_feature("website_visits"))])
async def get_visitor(
    visitor_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    visitor = await _visitor(db, principal, visitor_id)
    before = (visitor.contact_id, visitor.lead_id)
    await _match_person(db, visitor)
    if (visitor.contact_id, visitor.lead_id) != before:
        await db.commit()
    site = await db.get(TrackingSite, visitor.site_id)
    data = await visitor_out(db, visitor, {site.id: site.name} if site else None)
    data["sessions"] = await _sessions_for(db, [visitor.id])
    return data


class LinkVisitor(BaseModel):
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None


@router.post("/visits/visitors/{visitor_id}/link", dependencies=[Depends(require_feature("website_visits"))])
async def link_visitor(
    visitor_id: uuid.UUID,
    payload: LinkVisitor,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    visitor = await _visitor(db, principal, visitor_id)
    if bool(payload.contact_id) == bool(payload.lead_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick either a contact or a lead.")
    model = Contact if payload.contact_id else Lead
    record = await db.get(model, payload.contact_id or payload.lead_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    visitor.contact_id = payload.contact_id
    visitor.lead_id = payload.lead_id
    visitor.email = visitor.email or (record.email.lower() if record.email else None)
    visitor.identified_at = visitor.identified_at or utcnow()
    await db.commit()
    return await visitor_out(db, visitor)


@router.post("/visits/visitors/{visitor_id}/create-lead", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("website_visits"))])
async def lead_from_visitor(
    visitor_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    visitor = await _visitor(db, principal, visitor_id)
    await _match_person(db, visitor)
    if visitor.contact_id or visitor.lead_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This visitor is already linked to a contact or lead.")
    if not visitor.email:
        raise HTTPException(status.HTTP_409_CONFLICT, "This visitor hasn't shared an email address yet.")
    first, _, last = (visitor.name or "").partition(" ")
    if not first:
        first = visitor.email.split("@")[0].replace(".", " ").title()
    lead = Lead(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        first_name=first[:100],
        last_name=(last or "")[:100] or "(website visitor)",
        email=visitor.email,
        source="website",
        status="new",
        owner_user_id=principal.user_id,
    )
    db.add(lead)
    await db.flush()
    visitor.lead_id = lead.id
    await db.commit()
    return {"lead_id": str(lead.id), "visitor": await visitor_out(db, visitor)}


@router.get("/visits/record/{record_type}/{record_id}", dependencies=[Depends(require_feature("website_visits"))])
async def visits_for_record(
    record_type: str,
    record_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    if record_type not in {"lead", "contact"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Website visits are shown for leads and contacts.")
    column = WebVisitor.lead_id if record_type == "lead" else WebVisitor.contact_id
    visitors = (
        await db.execute(select(WebVisitor).where(WebVisitor.organization_id == principal.organization_id, column == record_id))
    ).scalars().all()
    return {
        "visitor_count": len(visitors),
        "visit_count": sum(v.visit_count for v in visitors),
        "page_view_count": sum(v.page_view_count for v in visitors),
        "total_seconds": sum(v.total_seconds for v in visitors),
        "first_seen_at": min((v.first_seen_at for v in visitors), default=None),
        "last_seen_at": max((v.last_seen_at for v in visitors), default=None),
        "sessions": await _sessions_for(db, [v.id for v in visitors], limit=10),
    }


# ===========================================================================
# Public tracking endpoint (called by the snippet on the customer's website)
# ===========================================================================

BOTS = re.compile(r"bot|spider|crawl|slurp|facebookexternalhit|preview|monitor|curl|wget|python-requests", re.I)
KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _device(agent: str) -> tuple[str, str]:
    lowered = agent.lower()
    device = "tablet" if ("ipad" in lowered or "tablet" in lowered) else "mobile" if "mobi" in lowered else "desktop"
    for needle, name in (("edg/", "Edge"), ("opr/", "Opera"), ("firefox/", "Firefox"), ("chrome/", "Chrome"), ("safari/", "Safari")):
        if needle in lowered:
            return device, name
    return device, "Other"


def _host_allowed(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def _clip(value: Any, size: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:size] or None


def _ga_client_id(value: Any) -> str | None:
    text = str(value or "").strip()[:60]
    return text if re.fullmatch(r"\d{1,20}\.\d{1,20}", text) else None


@public_router.post("/track", status_code=status.HTTP_204_NO_CONTENT)
async def track(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 8192:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if not isinstance(event, dict):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    agent = request.headers.get("user-agent", "")
    if BOTS.search(agent):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    site_key, visitor_key, session_key = event.get("k"), event.get("v"), event.get("s")
    if not all(isinstance(x, str) and KEY.match(x) for x in (site_key, visitor_key, session_key)):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    site = (await db.execute(select(TrackingSite).where(TrackingSite.site_key == site_key))).scalar_one_or_none()
    url = _clip(event.get("u"), 1000) or ""
    if site is None or not site.is_active or not url.lower().startswith(("http://", "https://")) or not _host_allowed(url, site.domain):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    from app.api.dependencies.auth import _ip_allowed, client_ip
    from app.services import workspace_settings

    rules = await workspace_settings.get(db, site.organization_id, "website")
    path_now = urlsplit(url).path or "/"
    if any(path_now == p or path_now.startswith(p.rstrip("*").rstrip("/") + "/") or (p.endswith("*") and path_now.startswith(p[:-1])) for p in rules.exclude_paths):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if rules.exclude_ips and _ip_allowed(client_ip(request), rules.exclude_ips):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Settings → Website visitors: honour the browser's Do Not Track / Global Privacy Control signals.
    if rules.respect_do_not_track and (request.headers.get("dnt") == "1" or request.headers.get("sec-gpc") == "1"):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    db.sync_session.info["automation_disabled"] = True
    now = utcnow()
    kind = event.get("e") if event.get("e") in {"view", "ping", "identify"} else "view"
    if kind == "identify" and not rules.identify_on_form_submit:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    visitor = (
        await db.execute(select(WebVisitor).where(WebVisitor.site_id == site.id, WebVisitor.visitor_key == visitor_key))
    ).scalar_one_or_none()
    query = parse_qs(urlsplit(url).query)
    referrer = _clip(event.get("r"), 1000)
    if referrer and _host_allowed(referrer, site.domain):
        referrer = None
    utm = {k: _clip(query.get(f"utm_{k}", [None])[0], 160) for k in ("source", "medium", "campaign")}
    is_new = visitor is None
    if visitor is None:
        if kind == "ping":
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        device, browser = _device(agent)
        visitor = WebVisitor(
            id=uuid.uuid4(),
            organization_id=site.organization_id,
            site_id=site.id,
            visitor_key=visitor_key,
            first_seen_at=now,
            last_seen_at=now,
            visit_count=0,
            page_view_count=0,
            total_seconds=0,
            first_referrer=referrer,
            first_source={k: v for k, v in utm.items() if v},
            device=device,
            browser=browser,
            language=_clip(event.get("lang"), 20),
            timezone=_clip(event.get("tz"), 60),
        )
        db.add(visitor)
        await db.flush()
    visitor.last_seen_at = now
    visitor.last_url = url
    site.last_hit_at = now
    # The visitor's Google Analytics client ID (from the _ga cookie), so CRM events sent
    # to GA4 later join this visitor's sessions.
    ga_client_id = _ga_client_id(event.get("ga"))
    if ga_client_id and (visitor.first_source or {}).get("ga_client_id") != ga_client_id:
        visitor.first_source = {**(visitor.first_source or {}), "ga_client_id": ga_client_id}

    if kind == "identify":
        email = (_clip(event.get("email"), 320) or "").lower()
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            if visitor.email != email:
                visitor.contact_id = None
                visitor.lead_id = None
            visitor.email = email
            visitor.name = _clip(event.get("name"), 200) or visitor.name
            visitor.identified_at = visitor.identified_at or now
            await _match_person(db, visitor)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    session = None
    if not is_new:
        session = (
            await db.execute(select(WebSession).where(WebSession.visitor_id == visitor.id, WebSession.session_key == session_key))
        ).scalar_one_or_none()
    if session is None:
        if kind == "ping":
            await db.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        session = WebSession(
            id=uuid.uuid4(),
            organization_id=site.organization_id,
            visitor_id=visitor.id,
            site_id=site.id,
            session_key=session_key,
            started_at=now,
            last_activity_at=now,
            page_count=0,
            duration_seconds=0,
            referrer=referrer,
            utm_source=utm["source"],
            utm_medium=utm["medium"],
            utm_campaign=utm["campaign"],
            landing_url=url,
        )
        db.add(session)
        await db.flush()
        visitor.visit_count += 1
    session.last_activity_at = now
    session.exit_url = url
    view_key = event.get("p") if isinstance(event.get("p"), str) and KEY.match(event.get("p")) else None
    if kind == "view":
        if view_key and (
            await db.execute(select(WebPageView.id).where(WebPageView.session_id == session.id, WebPageView.view_key == view_key))
        ).first():
            await db.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        db.add(
            WebPageView(
                id=uuid.uuid4(),
                organization_id=site.organization_id,
                session_id=session.id,
                visitor_id=visitor.id,
                view_key=view_key or secrets.token_hex(8),
                url=url,
                path=(urlsplit(url).path or "/")[:500],
                title=_clip(event.get("t"), 300),
                viewed_at=now,
                seconds=0,
            )
        )
        session.page_count += 1
        visitor.page_view_count += 1
    elif kind == "ping" and view_key:
        view = (
            await db.execute(select(WebPageView).where(WebPageView.session_id == session.id, WebPageView.view_key == view_key))
        ).scalar_one_or_none()
        try:
            seconds = max(0, min(int(event.get("d") or 0), 3600))
        except (TypeError, ValueError):
            seconds = 0
        if view is not None and seconds > view.seconds:
            delta = seconds - view.seconds
            view.seconds = seconds
            session.duration_seconds += delta
            visitor.total_seconds += delta
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
