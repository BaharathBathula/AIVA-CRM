"""Meetings & conversations: transcripts, trackers, scorecards and field prompts."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationTracker, FieldPrompt, Scorecard, ScorecardReview
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.platform import Notification
from app.models.user import User
from app.services import conversations as engine
from app.services import workspace_settings
from app.services.automation import record_audit
from app.services.lookups import names_for

router = APIRouter(tags=["Conversations"])
FEATURE = [Depends(require_feature("conversation_intelligence"))]
MAX_TRANSCRIPT = 3 * 1024 * 1024
MAX_AUDIO = 25 * 1024 * 1024
AUDIO_TYPES = ("audio/", "video/mp4", "video/webm")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Access
# ===========================================================================


async def _access(db: AsyncSession, principal: Principal):
    return await workspace_settings.get(db, principal.organization_id, "conversation_access")


def _is_participant(row: Conversation, principal: Principal) -> bool:
    email = (principal.email or "").lower()
    return row.owner_user_id == principal.user_id or any((p.get("email") or "").lower() == email or p.get("user_id") == str(principal.user_id) for p in row.participants or [])


async def _can_view(db: AsyncSession, principal: Principal, row: Conversation, access: Any) -> bool:
    if access.view == "everyone" or _is_participant(row, principal):
        return True
    if principal.can("conversations.coach") and access.reviewers_see_all:
        return True
    if access.managers_see_reports and principal.can("records.view_all"):
        return True
    if access.view == "team" and principal.team_id and row.owner_user_id:
        owner_team = (
            await db.execute(
                select(OrganizationMembership.team_id).where(OrganizationMembership.organization_id == principal.organization_id, OrganizationMembership.user_id == row.owner_user_id)
            )
        ).scalar_one_or_none()
        return owner_team == principal.team_id
    return False


async def _conversation(db: AsyncSession, principal: Principal, conversation_id: uuid.UUID) -> Conversation:
    row = await db.get(Conversation, conversation_id)
    if row is None or row.organization_id != principal.organization_id or not await _can_view(db, principal, row, await _access(db, principal)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return row


async def _internal_domains(db: AsyncSession, org: uuid.UUID) -> set[str]:
    team = await workspace_settings.get(db, org, "team_meetings")
    emails = (
        await db.execute(
            select(User.email).join(OrganizationMembership, OrganizationMembership.user_id == User.id).where(OrganizationMembership.organization_id == org)
        )
    ).scalars()
    return {e.split("@")[-1].lower() for e in emails if e and "@" in e} | set(team.internal_domains)


# ===========================================================================
# Output
# ===========================================================================


async def _links(db: AsyncSession, org: uuid.UUID, rows: list[Conversation]) -> dict[str, dict[str, str]]:
    return {
        "account": await names_for(db, org, "account", [r.account_id for r in rows]),
        "contact": await names_for(db, org, "contact", [r.contact_id for r in rows]),
        "opportunity": await names_for(db, org, "opportunity", [r.opportunity_id for r in rows]),
        "lead": await names_for(db, org, "lead", [r.lead_id for r in rows]),
        "user": await names_for(db, org, "user", [r.owner_user_id for r in rows]),
    }


def conversation_summary(row: Conversation, links: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "source": row.source,
        "kind": row.kind,
        "status": row.status,
        "error": row.error,
        "occurred_at": row.occurred_at,
        "duration_seconds": row.duration_seconds,
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "owner_name": links["user"].get(str(row.owner_user_id)) if row.owner_user_id else None,
        "account_id": str(row.account_id) if row.account_id else None,
        "account_name": links["account"].get(str(row.account_id)) if row.account_id else None,
        "contact_id": str(row.contact_id) if row.contact_id else None,
        "contact_name": links["contact"].get(str(row.contact_id)) if row.contact_id else None,
        "opportunity_id": str(row.opportunity_id) if row.opportunity_id else None,
        "opportunity_name": links["opportunity"].get(str(row.opportunity_id)) if row.opportunity_id else None,
        "lead_id": str(row.lead_id) if row.lead_id else None,
        "lead_name": links["lead"].get(str(row.lead_id)) if row.lead_id else None,
        "meeting_id": str(row.meeting_id) if row.meeting_id else None,
        "participants": list(row.participants or []),
        "summary": row.summary,
        # Keys starting with "_" are bookkeeping (e.g. a pending transcription job), not trackers.
        "tracker_counts": [
            {"id": k, "name": v["name"], "count": v["count"], "color": v.get("color")}
            for k, v in (row.tracker_hits or {}).items()
            if not k.startswith("_") and isinstance(v, dict) and "name" in v
        ],
        "pending_suggestions": sum(1 for s in row.field_suggestions or [] if s.get("status") == "pending"),
        "talk_time": row.talk_time or {},
        "has_recording": bool(row.recording_size),
    }


async def conversation_detail(db: AsyncSession, principal: Principal, row: Conversation) -> dict[str, Any]:
    links = await _links(db, principal.organization_id, [row])
    access = await _access(db, principal)
    reviews = (
        await db.execute(
            select(ScorecardReview, Scorecard.name).join(Scorecard, Scorecard.id == ScorecardReview.scorecard_id).where(ScorecardReview.conversation_id == row.id).order_by(ScorecardReview.created_at)
        )
    ).all()
    reviewers = await names_for(db, principal.organization_id, "user", [r.reviewer_user_id for r, _ in reviews])
    can_download = access.download == "everyone" or (access.download == "managers" and principal.can("records.view_all")) or row.owner_user_id == principal.user_id
    can_delete = principal.can("admin.settings") or (access.delete == "owner" and row.owner_user_id == principal.user_id)
    return {
        **conversation_summary(row, links),
        "transcript": list(row.transcript or []),
        "action_items": list(row.action_items or []),
        "summary_source": row.summary_source,
        "tracker_hits": {k: v for k, v in (row.tracker_hits or {}).items() if not k.startswith("_")},
        "field_suggestions": list(row.field_suggestions or []),
        "reviews": [
            {"id": str(r.id), "scorecard_id": str(r.scorecard_id), "scorecard_name": name, "reviewer": reviewers.get(str(r.reviewer_user_id)), "score": float(r.score),
             "answers": r.answers or {}, "comment": r.comment, "created_at": r.created_at}
            for r, name in reviews
        ],
        "can_review": principal.can("conversations.coach"),
        "can_download": can_download and bool(row.recording_size),
        "can_delete": can_delete,
        "can_edit": row.owner_user_id == principal.user_id or principal.can("conversations.coach") or principal.can("admin.settings"),
        "retain_until": row.retain_until,
    }


# ===========================================================================
# Listing and creating
# ===========================================================================


@router.get("/conversations", dependencies=FEATURE)
async def list_conversations(
    search: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None, pattern="^(external|internal)$"),
    tracker_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
    record_type: str | None = Query(default=None, pattern="^(account|contact|opportunity|lead)$"),
    record_id: uuid.UUID | None = None,
    days: int = Query(default=90, ge=1, le=3650),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    org = principal.organization_id
    query = select(Conversation).where(Conversation.organization_id == org, Conversation.occurred_at >= utcnow() - timedelta(days=days))
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Conversation.title.ilike(like), Conversation.transcript_text.ilike(like), Conversation.summary.ilike(like)))
    if kind:
        query = query.where(Conversation.kind == kind)
    if owner_user_id:
        query = query.where(Conversation.owner_user_id == owner_user_id)
    if record_type and record_id:
        column = {"account": Conversation.account_id, "contact": Conversation.contact_id, "opportunity": Conversation.opportunity_id, "lead": Conversation.lead_id}[record_type]
        query = query.where(column == record_id)
    rows = (await db.execute(query.order_by(Conversation.occurred_at.desc()).limit(500))).scalars().all()
    access = await _access(db, principal)
    rows = [r for r in rows if await _can_view(db, principal, r, access)]
    if tracker_id:
        rows = [r for r in rows if str(tracker_id) in (r.tracker_hits or {})]
    links = await _links(db, org, rows)
    trackers = (await db.execute(select(ConversationTracker).where(ConversationTracker.organization_id == org).order_by(ConversationTracker.name))).scalars().all()
    totals = {str(t.id): sum((r.tracker_hits or {}).get(str(t.id), {}).get("count", 0) for r in rows) for t in trackers}
    return {
        "items": [conversation_summary(r, links) for r in rows],
        "trackers": [{"id": str(t.id), "name": t.name, "color": t.color, "mentions": totals.get(str(t.id), 0), "conversations": sum(1 for r in rows if str(t.id) in (r.tracker_hits or {}))} for t in trackers],
        "stats": {
            "count": len(rows),
            "hours": round(sum(r.duration_seconds or 0 for r in rows) / 3600, 1),
            "pending_suggestions": sum(1 for r in rows for s in r.field_suggestions or [] if s.get("status") == "pending"),
        },
    }


class ConversationPayload(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    occurred_at: datetime | None = None
    transcript_text: str = Field(min_length=1, max_length=2_000_000)
    meeting_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    force: bool = False


async def _check_links(db: AsyncSession, org: uuid.UUID, links: dict[str, uuid.UUID | None]) -> None:
    for key, model in (("account_id", Account), ("contact_id", Contact), ("lead_id", Lead), ("opportunity_id", Opportunity), ("meeting_id", Meeting)):
        value = links.get(key)
        if value is None:
            continue
        record = await db.get(model, value)
        if record is None or record.organization_id != org:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"The linked {key.removesuffix('_id').replace('_', ' ')} wasn't found.")


async def _new_conversation(
    db: AsyncSession,
    principal: Principal,
    *,
    title: str,
    occurred_at: datetime | None,
    links: dict[str, uuid.UUID | None],
    source: str,
    force: bool,
) -> Conversation:
    org = principal.organization_id
    await _check_links(db, org, links)
    meeting = await db.get(Meeting, links["meeting_id"]) if links.get("meeting_id") else None
    participants: list[dict[str, Any]] = []
    if meeting is not None:
        participants = [{"name": a.get("name"), "email": a.get("email"), "user_id": a.get("id") if a.get("type") == "user" else None} for a in meeting.attendees or []]
        for key in ("account_id", "contact_id", "lead_id", "opportunity_id"):
            links[key] = links.get(key) or getattr(meeting, key, None)
        occurred_at = occurred_at or meeting.start_at
        title = title or meeting.title
    # A deal or contact on its own implies its account.
    if not links.get("account_id"):
        for key, model in (("opportunity_id", Opportunity), ("contact_id", Contact)):
            if links.get(key):
                linked = await db.get(model, links[key])
                if linked is not None and getattr(linked, "account_id", None):
                    links["account_id"] = linked.account_id
                    break
    team = await workspace_settings.get(db, org, "team_meetings")
    kind = engine.classify(participants, await _internal_domains(db, org)) if participants else "external"
    if not force:
        if kind == "internal" and not team.record_internal:
            raise HTTPException(status.HTTP_409_CONFLICT, "Internal team meetings aren't kept (Settings → Team meetings). Save it anyway?")
        skipped = next((k for k in team.skip_title_keywords if k.strip() and k.strip().lower() in title.lower()), None)
        if skipped:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Meetings with “{skipped}” in the title aren't kept (Settings → Team meetings). Save it anyway?")
    recording = await workspace_settings.get(db, org, "recording")
    if recording.record_mode == "none" and not force:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversation capture is turned off (Settings → Recording configuration). Save it anyway?")
    occurred = occurred_at or utcnow()
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    row = Conversation(
        id=uuid.uuid4(), organization_id=org, title=title.strip()[:300], source=source, kind=kind, owner_user_id=principal.user_id, occurred_at=occurred,
        participants=participants, status="processing", retain_until=occurred + timedelta(days=recording.retention_days), language=recording.language,
        **{k: v for k, v in links.items() if v is not None},
    )
    db.add(row)
    return row


async def _finish(db: AsyncSession, principal: Principal, row: Conversation, utterances: list[dict[str, Any]]) -> dict[str, Any]:
    if not utterances:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "AIVA couldn't find any speech in that transcript. Use a .vtt, .srt or “Name: text” file.")
    await engine.process(db, row, utterances, user_id=principal.user_id)
    team = await workspace_settings.get(db, principal.organization_id, "team_meetings")
    if team.share_notes_with_attendees and row.summary:
        for participant in row.participants or []:
            user_id = participant.get("user_id")
            if user_id and user_id != str(principal.user_id):
                db.add(Notification(id=uuid.uuid4(), organization_id=row.organization_id, user_id=uuid.UUID(user_id), kind="conversation",
                                    title=f"Notes ready: {row.title}"[:200], body=row.summary[:500], link=f"/conversations/{row.id}", is_read=False))
    record_audit(db, organization_id=principal.organization_id, action="create", entity_type="conversation", entity_id=row.id, entity_label=row.title)
    await db.commit()
    return await conversation_detail(db, principal, row)


@router.post("/conversations", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_conversation(payload: ConversationPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    links = {k: getattr(payload, k) for k in ("meeting_id", "account_id", "contact_id", "lead_id", "opportunity_id")}
    row = await _new_conversation(db, principal, title=payload.title, occurred_at=payload.occurred_at, links=links, source="manual", force=payload.force)
    return await _finish(db, principal, row, engine.parse_transcript(payload.transcript_text))


@router.post("/conversations/upload", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def upload_conversation(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    occurred_at: datetime | None = Form(default=None),
    meeting_id: uuid.UUID | None = Form(default=None),
    account_id: uuid.UUID | None = Form(default=None),
    contact_id: uuid.UUID | None = Form(default=None),
    lead_id: uuid.UUID | None = Form(default=None),
    opportunity_id: uuid.UUID | None = Form(default=None),
    force: bool = Form(default=False),
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read(MAX_AUDIO + 1)
    name = file.filename or "conversation"
    content_type = (file.content_type or "").lower()
    is_audio = content_type.startswith(AUDIO_TYPES) or name.lower().endswith((".mp3", ".m4a", ".wav", ".mp4", ".webm", ".ogg"))
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty.")
    links = {"meeting_id": meeting_id, "account_id": account_id, "contact_id": contact_id, "lead_id": lead_id, "opportunity_id": opportunity_id}
    title = title.strip() or name.rsplit(".", 1)[0].replace("_", " ")[:300]
    if not is_audio:
        if len(data) > MAX_TRANSCRIPT:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Transcripts can be up to 3 MB.")
        text = data.decode("utf-8", errors="replace")
        row = await _new_conversation(db, principal, title=title, occurred_at=occurred_at, links=links, source="transcript_upload", force=force)
        return await _finish(db, principal, row, engine.parse_transcript(text, name))
    if len(data) > MAX_AUDIO:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Recordings can be up to 25 MB. Upload the meeting's transcript file instead.")
    recording = await workspace_settings.get(db, principal.organization_id, "recording")
    provider = recording.transcription_provider
    if provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a transcription provider in Recording configuration, or upload the meeting's transcript (.vtt) instead.")
    key = await workspace_settings.secret(db, principal.organization_id, "recording", f"{provider}_api_key")
    if not key:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Add your {provider.title()} API key in Recording configuration.")
    row = await _new_conversation(db, principal, title=title, occurred_at=occurred_at, links=links, source="audio_upload", force=force)
    row.recording = data
    row.recording_size = len(data)
    row.recording_type = content_type or "audio/mpeg"
    try:
        if provider == "deepgram":
            utterances = await engine.deepgram(data, row.recording_type, key, recording.language)
            return await _finish(db, principal, row, utterances)
        job = await engine.assemblyai_start(data, key, recording.language)
    except engine.TranscriptionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    row.tracker_hits = {"_job": {"provider": "assemblyai", "id": job}}
    await db.commit()
    return await conversation_detail(db, principal, row)


async def poll_transcriptions(db: AsyncSession) -> int:
    """Scheduler job: finish AssemblyAI transcriptions, and drop data past its retention date."""

    done = 0
    rows = (await db.execute(select(Conversation).where(Conversation.status == "processing", Conversation.source == "audio_upload").limit(20))).scalars().all()
    for row in rows:
        job = (row.tracker_hits or {}).get("_job") or {}
        if job.get("provider") != "assemblyai":
            continue
        key = await workspace_settings.secret(db, row.organization_id, "recording", "assemblyai_api_key")
        if not key:
            row.status, row.error = "failed", "The AssemblyAI key was removed."
            continue
        try:
            utterances = await engine.assemblyai_poll(job["id"], key)
        except Exception as exc:  # noqa: BLE001 - provider errors and outages alike
            row.status, row.error = "failed", str(exc)[:500]
            continue
        if utterances is None:
            continue
        row.tracker_hits = {}
        await engine.process(db, row, utterances, user_id=row.owner_user_id)
        done += 1
    expired = (await db.execute(select(Conversation).where(Conversation.retain_until.is_not(None), Conversation.retain_until < utcnow()).limit(200))).scalars().all()
    for row in expired:
        await db.delete(row)
    await db.commit()
    return done + len(expired)


@router.get("/conversations/{conversation_id}", dependencies=FEATURE)
async def get_conversation(conversation_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return await conversation_detail(db, principal, await _conversation(db, principal, conversation_id))


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    kind: str | None = Field(default=None, pattern="^(external|internal)$")


@router.patch("/conversations/{conversation_id}", dependencies=FEATURE)
async def update_conversation(conversation_id: uuid.UUID, payload: ConversationUpdate, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _conversation(db, principal, conversation_id)
    if row.owner_user_id != principal.user_id and not (principal.can("conversations.coach") or principal.can("admin.settings")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the conversation's owner can change it.")
    changes = payload.model_dump(exclude_unset=True)
    await _check_links(db, principal.organization_id, {k: v for k, v in changes.items() if k.endswith("_id")})
    for key, value in changes.items():
        setattr(row, key, value.strip() if isinstance(value, str) and key == "title" else value)
    await db.commit()
    return await conversation_detail(db, principal, row)


@router.post("/conversations/{conversation_id}/reprocess", dependencies=FEATURE)
async def reprocess_conversation(conversation_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _conversation(db, principal, conversation_id)
    if not row.transcript:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's no transcript to process yet.")
    handled = {
        (s.get("prompt_id"), s.get("field_key")): s
        for s in row.field_suggestions or []
        if s.get("status") in ("applied", "dismissed")
    }
    await engine.process(db, row, [dict(u) for u in row.transcript], user_id=principal.user_id)
    # Keep what people already applied or dismissed instead of asking them again.
    if handled:
        kept = []
        for suggestion in row.field_suggestions or []:
            previous = handled.pop((suggestion.get("prompt_id"), suggestion.get("field_key")), None)
            kept.append(previous if previous is not None else suggestion)
        row.field_suggestions = kept + list(handled.values())
    await db.commit()
    return await conversation_detail(db, principal, row)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_conversation(conversation_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _conversation(db, principal, conversation_id)
    access = await _access(db, principal)
    if not (principal.can("admin.settings") or (access.delete == "owner" and row.owner_user_id == principal.user_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your workspace only lets admins delete conversations.")
    record_audit(db, organization_id=principal.organization_id, action="delete", entity_type="conversation", entity_id=row.id, entity_label=row.title)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/{conversation_id}/recording", dependencies=FEATURE)
async def download_recording(conversation_id: uuid.UUID, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    await _conversation(db, principal, conversation_id)
    row = (await db.execute(select(Conversation).options(undefer(Conversation.recording)).where(Conversation.id == conversation_id))).scalar_one()
    detail = await conversation_detail(db, principal, row)
    if not detail["can_download"] or not row.recording:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't download this recording.")
    return Response(row.recording, media_type=row.recording_type or "audio/mpeg", headers={"Content-Disposition": f'attachment; filename="{row.title[:80]}"', "X-Content-Type-Options": "nosniff"})


class SuggestionDecision(BaseModel):
    action: str = Field(pattern="^(apply|dismiss)$")
    value: str | None = Field(default=None, max_length=2000)


@router.post("/conversations/{conversation_id}/suggestions/{suggestion_id}", dependencies=FEATURE)
async def decide_suggestion(conversation_id: uuid.UUID, suggestion_id: str, payload: SuggestionDecision, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    row = await _conversation(db, principal, conversation_id)
    suggestions = [dict(s) for s in row.field_suggestions or []]
    target = next((s for s in suggestions if s.get("id") == suggestion_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found.")
    if target.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This suggestion was already handled.")
    if payload.action == "apply":
        if payload.value is not None and payload.value.strip():
            target["value"] = payload.value.strip()
        try:
            await engine.apply_suggestion(db, principal.organization_id, target)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        target["status"] = "applied"
    else:
        target["status"] = "dismissed"
    target["decided_by"] = principal.full_name
    row.field_suggestions = suggestions
    await db.commit()
    return await conversation_detail(db, principal, row)


# ===========================================================================
# Trackers
# ===========================================================================


class TrackerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    keywords: list[str] = Field(min_length=1, max_length=100)
    color: str = Field(default="blue", max_length=20)
    is_active: bool = True


def tracker_out(row: ConversationTracker) -> dict[str, Any]:
    return {"id": str(row.id), "name": row.name, "category": row.category, "keywords": list(row.keywords or []), "color": row.color, "is_active": row.is_active}


def _keywords(values: list[str]) -> list[str]:
    out = []
    for value in values:
        value = value.strip()
        if value and value.lower() not in {v.lower() for v in out}:
            out.append(value[:80])
    if not out:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add at least one keyword or phrase.")
    return out


@router.get("/conversation-trackers", dependencies=FEATURE)
async def list_trackers(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ConversationTracker).where(ConversationTracker.organization_id == principal.organization_id).order_by(ConversationTracker.name))).scalars().all()
    since = utcnow() - timedelta(days=30)
    recent = (await db.execute(select(Conversation.tracker_hits).where(Conversation.organization_id == principal.organization_id, Conversation.occurred_at >= since))).scalars().all()
    return {
        "items": [
            {**tracker_out(r), "mentions_30_days": sum(((h or {}).get(str(r.id)) or {}).get("count", 0) for h in recent), "conversations_30_days": sum(1 for h in recent if str(r.id) in (h or {}))}
            for r in rows
        ],
        "conversations_30_days": len(recent),
        "can_edit": principal.can("admin.settings"),
    }


@router.post("/conversation-trackers", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_tracker(payload: TrackerPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = ConversationTracker(id=uuid.uuid4(), organization_id=principal.organization_id, **{**payload.model_dump(), "name": payload.name.strip(), "keywords": _keywords(payload.keywords)})
    db.add(row)
    await db.commit()
    return tracker_out(row)


@router.put("/conversation-trackers/{tracker_id}", dependencies=FEATURE)
async def update_tracker(tracker_id: uuid.UUID, payload: TrackerPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(ConversationTracker, tracker_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tracker not found.")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.name, row.keywords = payload.name.strip(), _keywords(payload.keywords)
    await db.commit()
    return tracker_out(row)


@router.delete("/conversation-trackers/{tracker_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_tracker(tracker_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(ConversationTracker, tracker_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tracker not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Scorecards
# ===========================================================================


class Question(BaseModel):
    id: str | None = Field(default=None, max_length=20)
    text: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="rating", pattern="^(rating|yes_no)$")
    weight: int = Field(default=1, ge=1, le=10)


class ScorecardPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    questions: list[Question] = Field(min_length=1, max_length=40)
    is_active: bool = True


def scorecard_out(row: Scorecard, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": str(row.id), "name": row.name, "description": row.description, "questions": list(row.questions or []), "is_active": row.is_active, **(stats or {})}


def _questions(items: list[Question]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for item in items:
        qid = item.id or uuid.uuid4().hex[:8]
        while qid in seen:
            qid = uuid.uuid4().hex[:8]
        seen.add(qid)
        out.append({"id": qid, "text": item.text.strip(), "kind": item.kind, "weight": item.weight})
    return out


@router.get("/scorecards", dependencies=FEATURE)
async def list_scorecards(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Scorecard).where(Scorecard.organization_id == principal.organization_id).order_by(Scorecard.name))).scalars().all()
    stats = {
        str(sid): {"reviews": int(count), "average": round(float(avg or 0), 1)}
        for sid, count, avg in (
            await db.execute(
                select(ScorecardReview.scorecard_id, func.count(), func.avg(ScorecardReview.score))
                .where(ScorecardReview.organization_id == principal.organization_id)
                .group_by(ScorecardReview.scorecard_id)
            )
        ).all()
    }
    by_rep = (
        await db.execute(
            select(ScorecardReview.scorecard_id, ScorecardReview.rep_user_id, func.count(), func.avg(ScorecardReview.score))
            .where(ScorecardReview.organization_id == principal.organization_id)
            .group_by(ScorecardReview.scorecard_id, ScorecardReview.rep_user_id)
        )
    ).all()
    names = await names_for(db, principal.organization_id, "user", [r for _, r, _, _ in by_rep])
    reps: dict[str, list[dict[str, Any]]] = {}
    for sid, rep, count, avg in by_rep:
        reps.setdefault(str(sid), []).append({"user_id": str(rep) if rep else None, "name": names.get(str(rep), "Unassigned"), "reviews": int(count), "average": round(float(avg or 0), 1)})
    return {
        "items": [scorecard_out(r, {**stats.get(str(r.id), {"reviews": 0, "average": 0}), "by_rep": sorted(reps.get(str(r.id), []), key=lambda x: -x["average"])}) for r in rows],
        "can_edit": principal.can("admin.settings") or principal.can("conversations.coach"),
    }


@router.post("/scorecards", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_scorecard(payload: ScorecardPayload, principal: Principal = Depends(require_permission("conversations.coach")), db: AsyncSession = Depends(get_db)):
    row = Scorecard(id=uuid.uuid4(), organization_id=principal.organization_id, name=payload.name.strip(), description=payload.description,
                    questions=_questions(payload.questions), is_active=payload.is_active, created_by_user_id=principal.user_id)
    db.add(row)
    await db.commit()
    return scorecard_out(row)


@router.put("/scorecards/{scorecard_id}", dependencies=FEATURE)
async def update_scorecard(scorecard_id: uuid.UUID, payload: ScorecardPayload, principal: Principal = Depends(require_permission("conversations.coach")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Scorecard, scorecard_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scorecard not found.")
    row.name, row.description, row.questions, row.is_active = payload.name.strip(), payload.description, _questions(payload.questions), payload.is_active
    await db.commit()
    return scorecard_out(row)


@router.delete("/scorecards/{scorecard_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_scorecard(scorecard_id: uuid.UUID, principal: Principal = Depends(require_permission("conversations.coach")), db: AsyncSession = Depends(get_db)):
    row = await db.get(Scorecard, scorecard_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scorecard not found.")
    used = (await db.execute(select(func.count()).select_from(ScorecardReview).where(ScorecardReview.scorecard_id == row.id))).scalar_one()
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversations were scored with this scorecard. Turn it off instead to keep the history.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReviewPayload(BaseModel):
    scorecard_id: uuid.UUID
    answers: dict[str, Any]
    comment: str | None = Field(default=None, max_length=5000)
    rep_user_id: uuid.UUID | None = None


def score_answers(questions: list[dict[str, Any]], answers: dict[str, Any]) -> Decimal:
    """Weighted 0–100 score: ratings are 1–5, yes/no is 5 or 1."""

    total = weight = 0.0
    for question in questions:
        value = answers.get(question["id"])
        if value in (None, ""):
            continue
        if question["kind"] == "yes_no":
            points = 5.0 if value in (True, "yes", "true", 1) else 1.0
        else:
            points = float(value)
            if not 1 <= points <= 5:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ratings go from 1 to 5.")
        total += (points - 1) / 4 * 100 * question.get("weight", 1)
        weight += question.get("weight", 1)
    if not weight:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Answer at least one question.")
    return Decimal(str(round(total / weight, 2)))


@router.post("/conversations/{conversation_id}/reviews", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def review_conversation(conversation_id: uuid.UUID, payload: ReviewPayload, principal: Principal = Depends(require_permission("conversations.coach")), db: AsyncSession = Depends(get_db)):
    row = await _conversation(db, principal, conversation_id)
    scorecard = await db.get(Scorecard, payload.scorecard_id)
    if scorecard is None or scorecard.organization_id != principal.organization_id or not scorecard.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scorecard not found.")
    review = ScorecardReview(
        id=uuid.uuid4(), organization_id=principal.organization_id, scorecard_id=scorecard.id, conversation_id=row.id, reviewer_user_id=principal.user_id,
        rep_user_id=payload.rep_user_id or row.owner_user_id, answers={k: v for k, v in payload.answers.items() if any(q["id"] == k for q in scorecard.questions)},
        score=score_answers(scorecard.questions or [], payload.answers), comment=payload.comment,
    )
    db.add(review)
    if review.rep_user_id and review.rep_user_id != principal.user_id:
        db.add(Notification(id=uuid.uuid4(), organization_id=principal.organization_id, user_id=review.rep_user_id, kind="conversation",
                            title=f"{principal.full_name} scored “{row.title}”"[:200], body=f"{scorecard.name}: {float(review.score):.0f}/100", link=f"/conversations/{row.id}", is_read=False))
    await db.commit()
    return await conversation_detail(db, principal, row)


# ===========================================================================
# Custom field prompts
# ===========================================================================


class PromptPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    object_type: str = Field(pattern="^(opportunity|account|contact)$")
    field_key: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=5, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    apply_mode: str = Field(default="suggest", pattern="^(suggest|auto)$")
    is_active: bool = True


def prompt_out(row: FieldPrompt) -> dict[str, Any]:
    spec = engine.field_spec(row.object_type, row.field_key)
    return {"id": str(row.id), "name": row.name, "object_type": row.object_type, "field_key": row.field_key, "field_label": spec["label"], "prompt": row.prompt,
            "keywords": list(row.keywords or []), "apply_mode": row.apply_mode, "is_active": row.is_active, "position": row.position}


async def _validate_prompt(db: AsyncSession, principal: Principal, payload: PromptPayload) -> None:
    if payload.field_key.startswith("cf."):
        from app.models.editions import CustomField

        exists = (
            await db.execute(
                select(CustomField.id).where(CustomField.organization_id == principal.organization_id, CustomField.object_type == payload.object_type, CustomField.key == payload.field_key[3:])
            )
        ).first()
        if not exists:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That custom field doesn't exist.")
    elif payload.field_key not in engine.FIELD_TARGETS[payload.object_type]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a field AIVA can fill in.")
    payload.keywords = [k.strip() for k in payload.keywords if k.strip()]


@router.get("/field-prompts", dependencies=FEATURE)
async def list_prompts(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(FieldPrompt).where(FieldPrompt.organization_id == principal.organization_id).order_by(FieldPrompt.position, FieldPrompt.name))).scalars().all()
    from app.models.editions import CustomField

    custom = (
        await db.execute(select(CustomField).where(CustomField.organization_id == principal.organization_id, CustomField.object_type.in_(("opportunity", "account", "contact")), CustomField.is_active.is_(True)))
    ).scalars().all()
    targets = {k: [{"key": f, "label": v["label"], "type": v["type"]} for f, v in fields.items()] for k, fields in engine.FIELD_TARGETS.items()}
    for field in custom:
        if not field.is_encrypted:
            targets[field.object_type].append({"key": f"cf.{field.key}", "label": field.label, "type": "text"})
    from app.services import ai

    return {"items": [prompt_out(r) for r in rows], "targets": targets, "ai": ai.configured(), "can_edit": principal.can("admin.settings")}


@router.post("/field-prompts", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_prompt(payload: PromptPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    await _validate_prompt(db, principal, payload)
    count = (await db.execute(select(func.count()).select_from(FieldPrompt).where(FieldPrompt.organization_id == principal.organization_id))).scalar_one()
    row = FieldPrompt(id=uuid.uuid4(), organization_id=principal.organization_id, position=count, **{**payload.model_dump(), "name": payload.name.strip()})
    db.add(row)
    await db.commit()
    return prompt_out(row)


@router.put("/field-prompts/{prompt_id}", dependencies=FEATURE)
async def update_prompt(prompt_id: uuid.UUID, payload: PromptPayload, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(FieldPrompt, prompt_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt not found.")
    await _validate_prompt(db, principal, payload)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.name = payload.name.strip()
    await db.commit()
    return prompt_out(row)


@router.delete("/field-prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def delete_prompt(prompt_id: uuid.UUID, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    row = await db.get(FieldPrompt, prompt_id)
    if row is None or row.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt not found.")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/team-meetings", dependencies=FEATURE)
async def team_meetings(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Upcoming and recent meetings with only teammates, and whether they'd be kept."""

    org = principal.organization_id
    team = await workspace_settings.get(db, org, "team_meetings")
    domains = await _internal_domains(db, org)
    meetings = (
        await db.execute(
            select(Meeting).where(Meeting.organization_id == org, Meeting.start_at >= utcnow() - timedelta(days=14), Meeting.start_at <= utcnow() + timedelta(days=14)).order_by(Meeting.start_at)
        )
    ).scalars().all()
    items = []
    for meeting in meetings:
        participants = [{"email": a.get("email")} for a in meeting.attendees or []]
        kind = engine.classify(participants, domains) if participants else "internal"
        if kind != "internal":
            continue
        skipped = next((k for k in team.skip_title_keywords if k.strip() and k.strip().lower() in meeting.title.lower()), None)
        items.append({"id": str(meeting.id), "title": meeting.title, "start_at": meeting.start_at, "attendees": len(meeting.attendees or []),
                      "kept": team.record_internal and not skipped, "reason": f"Title contains “{skipped}”" if skipped else (None if team.record_internal else "Internal meetings aren't kept")})
    return {"items": items, "internal_domains": sorted(domains), "settings": team.model_dump(mode="json")}
