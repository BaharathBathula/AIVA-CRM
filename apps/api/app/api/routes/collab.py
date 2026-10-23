"""Pinned records, team chat and the people directory."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature
from app.core.permissions import SYSTEM_ROLES
from app.db.session import get_db
from app.models.collaboration import ChatChannel, ChatMember, ChatMessage, Pin
from app.models.membership import OrganizationMembership
from app.models.platform import Notification, Role, Team
from app.models.user import User
from app.services.lookups import names_for
from app.services.records import RECORD_TYPES, resolve

router = APIRouter(tags=["Collaboration"])

MAX_PINS = 50


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(value: Any) -> str | None:
    return str(value) if value else None


async def _active_members(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, User]:
    rows = (
        await db.execute(
            select(User)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == organization_id, OrganizationMembership.status == "active")
        )
    ).scalars().all()
    return {str(u.id): u for u in rows}


# ===========================================================================
# Pins
# ===========================================================================


class PinPayload(BaseModel):
    record_type: str = Field(min_length=1, max_length=40)
    record_id: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    href: str | None = Field(default=None, max_length=500)


def pin_out(pin: Pin) -> dict[str, Any]:
    return {
        "id": str(pin.id),
        "record_type": pin.record_type,
        "record_id": pin.record_id,
        "label": pin.label,
        "subtitle": pin.subtitle,
        "href": pin.href,
        "position": pin.position,
        "created_at": pin.created_at,
    }


async def _my_pins(db: AsyncSession, principal: Principal) -> list[Pin]:
    return list(
        (
            await db.execute(
                select(Pin).where(Pin.organization_id == principal.organization_id, Pin.user_id == principal.user_id).order_by(Pin.position, Pin.created_at)
            )
        ).scalars()
    )


@router.get("/pins")
async def list_pins(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    return {"items": [pin_out(p) for p in await _my_pins(db, principal)], "limit": MAX_PINS}


@router.post("/pins", status_code=status.HTTP_201_CREATED)
async def add_pin(payload: PinPayload, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    pins = await _my_pins(db, principal)
    existing = next((p for p in pins if p.record_type == payload.record_type and p.record_id == payload.record_id), None)
    if existing is not None:
        return pin_out(existing)
    if len(pins) >= MAX_PINS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You can pin up to {MAX_PINS} items. Unpin something first.")
    if payload.record_type in RECORD_TYPES:
        found = await resolve(db, principal.organization_id, payload.record_type, payload.record_id)
        label, href, subtitle = found["label"], found["href"], payload.subtitle or found["kind"]
        record_id = found["id"]
    elif payload.record_type == "page":
        href = (payload.href or "").strip()
        if not href.startswith("/") or href.startswith("//"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pinned pages must be pages in AIVA.")
        label = (payload.label or "").strip() or href
        subtitle = payload.subtitle or "Page"
        record_id = payload.record_id
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That can't be pinned.")
    pin = Pin(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        record_type=payload.record_type,
        record_id=record_id,
        label=label[:200],
        subtitle=(subtitle or "")[:200] or None,
        href=href[:500],
        position=(max((p.position for p in pins), default=-1) + 1),
    )
    db.add(pin)
    await db.commit()
    return pin_out(pin)


@router.delete("/pins/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_pin(pin_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    pin = await db.get(Pin, pin_id)
    if pin is None or pin.user_id != principal.user_id or pin.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pin not found.")
    await db.delete(pin)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class PinOrder(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_PINS)


@router.put("/pins/order")
async def reorder_pins(payload: PinOrder, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    pins = {p.id: p for p in await _my_pins(db, principal)}
    position = 0
    for pin_id in payload.ids:
        if pin_id in pins:
            pins.pop(pin_id).position = position
            position += 1
    for pin in sorted(pins.values(), key=lambda p: p.position):
        pin.position = position
        position += 1
    await db.commit()
    return {"items": [pin_out(p) for p in await _my_pins(db, principal)]}


# ===========================================================================
# People directory
# ===========================================================================


@router.get("/people")
async def directory(
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(User, OrganizationMembership)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.status == "active")
    )
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(User.full_name.ilike(like), User.email.ilike(like), User.job_title.ilike(like)))
    rows = (await db.execute(query.order_by(User.full_name))).all()
    teams = {t.id: t.name for t in (await db.execute(select(Team).where(Team.organization_id == principal.organization_id))).scalars()}
    custom_roles = {r.key: r.name for r in (await db.execute(select(Role).where(Role.organization_id == principal.organization_id))).scalars()}
    managers = await names_for(db, principal.organization_id, "user", [m.reports_to_user_id for _, m in rows])
    return {
        "items": [
            {
                "id": str(user.id),
                "full_name": user.full_name,
                "email": user.email,
                "job_title": user.job_title,
                "phone": user.phone,
                "timezone": user.timezone,
                "role": membership.role,
                "role_name": SYSTEM_ROLES[membership.role].name if membership.role in SYSTEM_ROLES else custom_roles.get(membership.role, membership.role),
                "team_id": _id(membership.team_id),
                "team_name": teams.get(membership.team_id),
                "manager_id": _id(membership.reports_to_user_id),
                "manager_name": managers.get(str(membership.reports_to_user_id)),
                "last_login_at": user.last_login_at,
                "is_me": user.id == principal.user_id,
            }
            for user, membership in rows
        ]
    }


# ===========================================================================
# Team chat
# ===========================================================================

chat = APIRouter(dependencies=[Depends(require_feature("team_chat"))])

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
MENTION = re.compile(r"@\[([^\]]{1,120})\]\(user:([0-9a-f-]{36})\)")


def _channel_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower().lstrip("#")).strip("-")[:80]
    if not NAME.match(name or ""):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Channel names use letters, numbers, dashes and underscores.")
    return name


async def _membership(db: AsyncSession, channel_id: uuid.UUID, user_id: uuid.UUID) -> ChatMember | None:
    return (await db.execute(select(ChatMember).where(ChatMember.channel_id == channel_id, ChatMember.user_id == user_id))).scalar_one_or_none()


async def _channel(db: AsyncSession, principal: Principal, channel_id: uuid.UUID, need_member: bool = True) -> tuple[ChatChannel, ChatMember | None]:
    channel = await db.get(ChatChannel, channel_id)
    if channel is None or channel.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    member = await _membership(db, channel.id, principal.user_id)
    if member is None and (channel.is_private or channel.kind == "direct"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    if need_member and member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the channel to post in it.")
    return channel, member


async def _ensure_general(db: AsyncSession, principal: Principal) -> None:
    """Every workspace gets a #general channel that everyone is in."""

    general = (
        await db.execute(select(ChatChannel).where(ChatChannel.organization_id == principal.organization_id, ChatChannel.kind == "channel", ChatChannel.name == "general"))
    ).scalar_one_or_none()
    if general is None:
        general = ChatChannel(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            kind="channel",
            name="general",
            topic="Announcements and conversation for the whole team.",
            is_private=False,
            created_by_user_id=principal.user_id,
        )
        db.add(general)
        await db.flush()
    if await _membership(db, general.id, principal.user_id) is None:
        db.add(ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=general.id, user_id=principal.user_id, role="member", last_read_at=utcnow()))
    await db.commit()


async def _unread(db: AsyncSession, principal: Principal, channel_ids: list[uuid.UUID], members: dict[uuid.UUID, ChatMember]) -> dict[uuid.UUID, int]:
    if not channel_ids:
        return {}
    conditions = [
        and_(
            ChatMessage.channel_id == cid,
            ChatMessage.created_at > (members[cid].last_read_at or datetime(1970, 1, 1, tzinfo=timezone.utc)),
        )
        for cid in channel_ids
    ]
    rows = (
        await db.execute(
            select(ChatMessage.channel_id, func.count())
            .where(or_(*conditions), ChatMessage.deleted_at.is_(None), or_(ChatMessage.user_id != principal.user_id, ChatMessage.user_id.is_(None)))
            .group_by(ChatMessage.channel_id)
        )
    ).all()
    return dict(rows)


async def channel_out(db: AsyncSession, principal: Principal, channel: ChatChannel, member: ChatMember | None, unread: int = 0, people: dict[str, User] | None = None) -> dict[str, Any]:
    member_ids = [str(u) for u in (await db.execute(select(ChatMember.user_id).where(ChatMember.channel_id == channel.id))).scalars()]
    people = people or {}
    title = f"#{channel.name}" if channel.kind == "channel" else None
    other = None
    if channel.kind == "direct":
        others = [m for m in member_ids if m != str(principal.user_id)] or member_ids
        other = people.get(others[0]) if others else None
        if other is None and others:
            other = await db.get(User, uuid.UUID(others[0]))
        title = (other.full_name if other else "Former member") + (" (you)" if others and others[0] == str(principal.user_id) else "")
    last = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.channel_id == channel.id, ChatMessage.deleted_at.is_(None)).order_by(ChatMessage.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    authors = await names_for(db, principal.organization_id, "user", [last.user_id] if last else [])
    return {
        "id": str(channel.id),
        "kind": channel.kind,
        "name": channel.name,
        "title": title,
        "topic": channel.topic,
        "is_private": channel.is_private,
        "is_archived": channel.is_archived,
        "is_member": member is not None,
        "my_role": member.role if member else None,
        "member_count": len(member_ids),
        "member_ids": member_ids,
        "other_user_id": str(other.id) if other else None,
        "unread": unread,
        "last_message": {
            "body": re.sub(MENTION, r"@\1", last.body)[:140],
            "author": authors.get(str(last.user_id)),
            "created_at": last.created_at,
        }
        if last
        else None,
        "last_message_at": channel.last_message_at,
        "created_at": channel.created_at,
    }


@chat.get("/chat/channels")
async def list_channels(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    await _ensure_general(db, principal)
    memberships = {
        m.channel_id: m
        for m in (
            await db.execute(select(ChatMember).where(ChatMember.organization_id == principal.organization_id, ChatMember.user_id == principal.user_id))
        ).scalars()
    }
    channels = (
        await db.execute(
            select(ChatChannel).where(
                ChatChannel.organization_id == principal.organization_id,
                or_(ChatChannel.id.in_(list(memberships) or [uuid.uuid4()]), and_(ChatChannel.kind == "channel", ChatChannel.is_private.is_(False))),
            )
        )
    ).scalars().all()
    unread = await _unread(db, principal, [c.id for c in channels if c.id in memberships], memberships)
    people = await _active_members(db, principal.organization_id)
    items = [await channel_out(db, principal, c, memberships.get(c.id), unread.get(c.id, 0), people) for c in channels]
    items.sort(key=lambda c: (c["kind"] != "channel", not c["is_member"], c["title"] or ""))
    return {"items": items, "unread_total": sum(unread.values())}


@chat.get("/chat/unread")
async def unread_total(principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    memberships = {
        m.channel_id: m
        for m in (
            await db.execute(select(ChatMember).where(ChatMember.organization_id == principal.organization_id, ChatMember.user_id == principal.user_id, ChatMember.is_muted.is_(False)))
        ).scalars()
    }
    unread = await _unread(db, principal, list(memberships), memberships)
    return {"unread": sum(unread.values()), "conversations": len([v for v in unread.values() if v])}


class ChannelPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    topic: str | None = Field(default=None, max_length=300)
    is_private: bool = False
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


@chat.post("/chat/channels", status_code=status.HTTP_201_CREATED)
async def create_channel(payload: ChannelPayload, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    if principal.read_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your workspace is read-only.")
    name = _channel_name(payload.name)
    clash = (
        await db.execute(select(ChatChannel.id).where(ChatChannel.organization_id == principal.organization_id, ChatChannel.kind == "channel", ChatChannel.name == name))
    ).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, f"#{name} already exists.")
    people = await _active_members(db, principal.organization_id)
    invited = {str(u) for u in payload.member_ids} - {str(principal.user_id)}
    if invited - set(people):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Everyone you add must be an active member of this workspace.")
    channel = ChatChannel(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        kind="channel",
        name=name,
        topic=(payload.topic or "").strip() or None,
        is_private=payload.is_private,
        created_by_user_id=principal.user_id,
    )
    db.add(channel)
    await db.flush()
    owner = ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=principal.user_id, role="owner", last_read_at=utcnow())
    db.add(owner)
    for user_id in invited:
        db.add(ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=uuid.UUID(user_id), role="member"))
    await db.commit()
    return await channel_out(db, principal, channel, owner, 0, people)


class DirectPayload(BaseModel):
    user_id: uuid.UUID


@chat.post("/chat/direct")
async def open_direct(payload: DirectPayload, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    people = await _active_members(db, principal.organization_id)
    if str(payload.user_id) not in people:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't an active member of this workspace.")
    ids = sorted({str(principal.user_id), str(payload.user_id)})
    key = ":".join(ids)
    channel = (
        await db.execute(select(ChatChannel).where(ChatChannel.organization_id == principal.organization_id, ChatChannel.dm_key == key))
    ).scalar_one_or_none()
    if channel is None:
        channel = ChatChannel(id=uuid.uuid4(), organization_id=principal.organization_id, kind="direct", name=None, is_private=True, dm_key=key, created_by_user_id=principal.user_id)
        db.add(channel)
        await db.flush()
        for user_id in ids:
            db.add(ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=uuid.UUID(user_id), role="member", last_read_at=utcnow() if user_id == str(principal.user_id) else None))
        await db.commit()
    member = await _membership(db, channel.id, principal.user_id)
    return await channel_out(db, principal, channel, member, 0, people)


@chat.get("/chat/channels/{channel_id}")
async def get_channel(channel_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    people = await _active_members(db, principal.organization_id)
    data = await channel_out(db, principal, channel, member, 0, people)
    data["members"] = [
        {"id": uid, "name": people[uid].full_name if uid in people else "Former member", "email": people[uid].email if uid in people else None}
        for uid in data["member_ids"]
    ]
    return data


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    topic: str | None = Field(default=None, max_length=300)
    is_archived: bool | None = None


def _can_manage(principal: Principal, member: ChatMember | None) -> bool:
    return (member is not None and member.role == "owner") or principal.can("admin.settings")


@chat.patch("/chat/channels/{channel_id}")
async def update_channel(channel_id: uuid.UUID, payload: ChannelUpdate, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    if channel.kind != "channel" or not _can_manage(principal, member):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the channel's owner or an administrator can change it.")
    if payload.name is not None:
        name = _channel_name(payload.name)
        if channel.name == "general" and name != "general":
            raise HTTPException(status.HTTP_409_CONFLICT, "#general can't be renamed.")
        clash = (
            await db.execute(select(ChatChannel.id).where(ChatChannel.organization_id == principal.organization_id, ChatChannel.kind == "channel", ChatChannel.name == name, ChatChannel.id != channel.id))
        ).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, f"#{name} already exists.")
        channel.name = name
    if "topic" in payload.model_fields_set:
        channel.topic = (payload.topic or "").strip() or None
    if payload.is_archived is not None:
        if channel.name == "general" and payload.is_archived:
            raise HTTPException(status.HTTP_409_CONFLICT, "#general can't be archived.")
        channel.is_archived = payload.is_archived
    await db.commit()
    return await channel_out(db, principal, channel, member)


@chat.post("/chat/channels/{channel_id}/join")
async def join_channel(channel_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    if channel.kind != "channel" or channel.is_private:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ask a member to add you to this channel.")
    if member is None:
        member = ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=principal.user_id, role="member", last_read_at=utcnow())
        db.add(member)
        await db.commit()
    return await channel_out(db, principal, channel, member)


@chat.post("/chat/channels/{channel_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_channel(channel_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id)
    if channel.kind == "direct":
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't leave a direct conversation.")
    if channel.name == "general":
        raise HTTPException(status.HTTP_409_CONFLICT, "Everyone stays in #general.")
    await db.delete(member)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MembersPayload(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@chat.post("/chat/channels/{channel_id}/members")
async def add_members(channel_id: uuid.UUID, payload: MembersPayload, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id)
    if channel.kind != "channel":
        raise HTTPException(status.HTTP_409_CONFLICT, "Start a channel to talk with more people.")
    people = await _active_members(db, principal.organization_id)
    wanted = {str(u) for u in payload.user_ids}
    if wanted - set(people):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Everyone you add must be an active member of this workspace.")
    present = {str(u) for u in (await db.execute(select(ChatMember.user_id).where(ChatMember.channel_id == channel.id))).scalars()}
    for user_id in wanted - present:
        db.add(ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=uuid.UUID(user_id), role="member"))
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=principal.organization_id, user_id=uuid.UUID(user_id), kind="chat",
                title=f"{principal.full_name or 'A teammate'} added you to #{channel.name}"[:200], body=None, link=f"/chat?c={channel.id}", is_read=False,
            )
        )
    await db.commit()
    return await get_channel(channel_id, principal, db)


@chat.delete("/chat/channels/{channel_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(channel_id: uuid.UUID, user_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    if channel.kind != "channel" or not _can_manage(principal, member):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the channel's owner or an administrator can remove people.")
    if channel.name == "general":
        raise HTTPException(status.HTTP_409_CONFLICT, "Everyone stays in #general.")
    target = await _membership(db, channel.id, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person isn't in this channel.")
    await db.delete(target)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class MessagePayload(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    record_type: str | None = Field(default=None, max_length=40)
    record_id: str | None = Field(default=None, max_length=64)


async def message_out(db: AsyncSession, principal: Principal, message: ChatMessage, authors: dict[str, str]) -> dict[str, Any]:
    deleted = message.deleted_at is not None
    return {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "user_id": _id(message.user_id),
        "author": authors.get(str(message.user_id), "Former member"),
        "body": "" if deleted else message.body,
        "mentions": message.mentions or [],
        "record": None if deleted else message.record,
        "is_mine": message.user_id == principal.user_id,
        "edited_at": message.edited_at,
        "deleted": deleted,
        "created_at": message.created_at,
    }


@chat.get("/chat/channels/{channel_id}/messages")
async def list_messages(
    channel_id: uuid.UUID,
    before: datetime | None = None,
    after: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    query = select(ChatMessage).where(ChatMessage.channel_id == channel.id)
    if after is not None:
        query = query.where(or_(ChatMessage.created_at > after, ChatMessage.updated_at > after)).order_by(ChatMessage.created_at).limit(limit)
        rows = list((await db.execute(query)).scalars())
    else:
        if before is not None:
            query = query.where(ChatMessage.created_at < before)
        rows = list(reversed((await db.execute(query.order_by(ChatMessage.created_at.desc()).limit(limit + 1))).scalars().all()))
    has_more = after is None and len(rows) > limit
    if has_more:
        rows = rows[1:]
    authors = await names_for(db, principal.organization_id, "user", [m.user_id for m in rows])
    return {
        "items": [await message_out(db, principal, m, authors) for m in rows],
        "has_more": has_more,
        "last_read_at": member.last_read_at if member else None,
        "server_time": utcnow(),
    }


@chat.post("/chat/channels/{channel_id}/messages", status_code=status.HTTP_201_CREATED)
async def post_message(channel_id: uuid.UUID, payload: MessagePayload, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    if principal.read_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your workspace is read-only.")
    channel, member = await _channel(db, principal, channel_id)
    if channel.is_archived:
        raise HTTPException(status.HTTP_409_CONFLICT, "This channel is archived.")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Write a message first.")
    record = None
    if payload.record_type and payload.record_id:
        found = await resolve(db, principal.organization_id, payload.record_type, payload.record_id)
        record = {"type": found["type"], "id": found["id"], "label": found["label"], "href": found["href"], "kind": found["kind"]}
    people = await _active_members(db, principal.organization_id)
    mentioned = [uid for _, uid in MENTION.findall(body) if uid in people]
    mentioned = list(dict.fromkeys(mentioned))
    now = utcnow()
    message = ChatMessage(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        channel_id=channel.id,
        user_id=principal.user_id,
        body=body,
        mentions=mentioned,
        record=record,
    )
    db.add(message)
    channel.last_message_at = now
    member.last_read_at = now
    members = {str(u) for u in (await db.execute(select(ChatMember.user_id).where(ChatMember.channel_id == channel.id))).scalars()}
    place = f"#{channel.name}" if channel.kind == "channel" else "a direct message"
    for uid in mentioned:
        if uid == str(principal.user_id):
            continue
        if uid not in members and channel.kind == "channel" and not channel.is_private:
            db.add(ChatMember(id=uuid.uuid4(), organization_id=principal.organization_id, channel_id=channel.id, user_id=uuid.UUID(uid), role="member"))
        elif uid not in members:
            continue
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=principal.organization_id, user_id=uuid.UUID(uid), kind="mention",
                title=f"{principal.full_name or 'A teammate'} mentioned you in {place}"[:200],
                body=re.sub(MENTION, r"@\1", body)[:300], link=f"/chat?c={channel.id}", is_read=False,
            )
        )
    await db.commit()
    authors = await names_for(db, principal.organization_id, "user", [principal.user_id])
    return await message_out(db, principal, message, authors)


class MessageEdit(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


async def _message(db: AsyncSession, principal: Principal, message_id: uuid.UUID) -> ChatMessage:
    message = await db.get(ChatMessage, message_id)
    if message is None or message.organization_id != principal.organization_id or message.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found.")
    await _channel(db, principal, message.channel_id, need_member=False)
    return message


@chat.patch("/chat/messages/{message_id}")
async def edit_message(message_id: uuid.UUID, payload: MessageEdit, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    message = await _message(db, principal, message_id)
    if message.user_id != principal.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own messages.")
    message.body = payload.body.strip()
    message.edited_at = utcnow()
    await db.commit()
    authors = await names_for(db, principal.organization_id, "user", [message.user_id])
    return await message_out(db, principal, message, authors)


@chat.delete("/chat/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(message_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    message = await _message(db, principal, message_id)
    if message.user_id != principal.user_id and not principal.can("admin.settings"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own messages.")
    message.deleted_at = utcnow()
    message.body = ""
    message.record = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@chat.post("/chat/channels/{channel_id}/read")
async def mark_read(channel_id: uuid.UUID, principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_db)):
    channel, member = await _channel(db, principal, channel_id, need_member=False)
    if member is not None:
        member.last_read_at = utcnow()
        await db.commit()
    return {"ok": True}


router.include_router(chat)


# ===========================================================================
# SalesInbox: email threads sorted by who they're with
# ===========================================================================


def _thread_emails(participants: list[Any]) -> list[str]:
    found = []
    for item in participants or []:
        value = item.get("email") if isinstance(item, dict) else item
        if isinstance(value, str) and "@" in value:
            found.append(value.strip().lower())
    return found


@router.get("/salesinbox")
async def salesinbox(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    from app.models.email_message import EmailMessage
    from app.models.email_thread import EmailThread
    from app.models.lead import Lead

    if not principal.can("records.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role doesn't allow this action. Ask an administrator for access.")
    org = principal.organization_id
    query = select(EmailThread).where(EmailThread.organization_id == org)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(EmailThread.subject.ilike(like), EmailThread.snippet.ilike(like)))
    threads = (await db.execute(query.order_by(EmailThread.last_message_at.desc().nulls_last()).limit(limit))).scalars().all()
    ids = [t.id for t in threads]
    stats = {
        thread_id: (total, unread, last_direction)
        for thread_id, total, unread, last_direction in (
            await db.execute(
                select(
                    EmailMessage.thread_id,
                    func.count(),
                    func.count().filter(and_(EmailMessage.is_read.is_(False), EmailMessage.direction == "inbound")),
                    func.max(EmailMessage.direction),
                )
                .where(EmailMessage.thread_id.in_(ids or [uuid.uuid4()]))
                .group_by(EmailMessage.thread_id)
            )
        ).all()
    }
    accounts = await names_for(db, org, "account", [t.account_id for t in threads])
    contacts = await names_for(db, org, "contact", [t.contact_id for t in threads])
    opps = await names_for(db, org, "opportunity", [t.opportunity_id for t in threads])
    emails = {e for t in threads for e in _thread_emails(t.participants)}
    leads = {
        (email or "").lower(): (lead_id, f"{first or ''} {last or ''}".strip())
        for lead_id, email, first, last in (
            await db.execute(
                select(Lead.id, Lead.email, Lead.first_name, Lead.last_name).where(
                    Lead.organization_id == org, func.lower(Lead.email).in_(list(emails) or ["-"]), Lead.status != "converted"
                )
            )
        ).all()
    }
    columns: dict[str, list[dict]] = {"deals": [], "contacts": [], "leads": [], "others": []}
    for thread in threads:
        total, unread, _ = stats.get(thread.id, (0, 0, None))
        item = {
            "id": str(thread.id),
            "subject": thread.subject,
            "snippet": thread.snippet,
            "last_message_at": thread.last_message_at,
            "participants": _thread_emails(thread.participants),
            "message_count": total,
            "unread": unread,
            "record": None,
        }
        if thread.opportunity_id:
            item["record"] = {"type": "opportunity", "label": opps.get(str(thread.opportunity_id)), "href": f"/opportunities/{thread.opportunity_id}"}
            columns["deals"].append(item)
        elif thread.contact_id or thread.account_id:
            if thread.contact_id:
                item["record"] = {"type": "contact", "label": contacts.get(str(thread.contact_id)), "href": f"/contacts/{thread.contact_id}"}
            else:
                item["record"] = {"type": "account", "label": accounts.get(str(thread.account_id)), "href": f"/accounts/{thread.account_id}"}
            columns["contacts"].append(item)
        else:
            lead = next((leads[e] for e in item["participants"] if e in leads), None)
            if lead:
                item["record"] = {"type": "lead", "label": lead[1], "href": f"/leads/{lead[0]}"}
                columns["leads"].append(item)
            else:
                columns["others"].append(item)
    return {"columns": columns, "counts": {k: len(v) for k, v in columns.items()}, "unread": {k: sum(i["unread"] for i in v) for k, v in columns.items()}}
