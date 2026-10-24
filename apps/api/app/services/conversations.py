"""Conversation intelligence for calls and meetings.

A conversation starts from a transcript file (Zoom and Teams .vtt, .srt, or
plain "Speaker: text" .txt), pasted text, or an audio recording sent to a
transcription provider (Deepgram or AssemblyAI with your API key). AIVA then:

* redacts card and social security numbers (when switched on),
* works out talk time per speaker,
* counts keyword trackers ("pricing", competitor names…) with the lines
  they were said in,
* writes a summary and action items (Claude when configured, otherwise
  extractive rules), and
* answers custom field prompts ("What budget did they mention?") as
  suggested updates to the linked deal, account or contact.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationTracker, FieldPrompt
from app.models.opportunity import Opportunity
from app.services import ai, ai_context, metering, workspace_settings

logger = logging.getLogger("aiva.conversations")

TIME = r"(\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
CUE_RE = re.compile(rf"^\s*({TIME})\s*-->\s*({TIME})")
VOICE_RE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.S)
SPEAKER_RE = re.compile(r"^\s*(?:\[(?P<ts>[\d:.]+)\]\s*)?(?P<speaker>[A-Z][\w .'\-]{0,60}?)\s*(?:\((?P<ts2>[\d:.]+)\))?\s*:\s*(?P<text>.+)$")
CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
ACTION_RE = re.compile(r"\b(i'?ll|we'?ll|i will|we will|let me|next step|follow up|send (you|over)|action item|by (monday|tuesday|wednesday|thursday|friday|next week|tomorrow))\b", re.I)
MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|thousand|million)?\b", re.I)
# "40,000 dollars", "40k", "1.2 million USD": amounts said without a dollar sign.
SPOKEN_MONEY_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|thousand|million)?\s*(?:dollars|usd|bucks)\b|\b(\d+(?:\.\d+)?)\s*(k|m)\b",
    re.I,
)

FIELD_TARGETS: dict[str, dict[str, dict[str, str]]] = {
    "opportunity": {
        "amount": {"label": "Amount", "type": "money"},
        "next_step": {"label": "Next step", "type": "text"},
        "expected_close_date": {"label": "Expected close date", "type": "date"},
        "competitor": {"label": "Competitor", "type": "text"},
        "description": {"label": "Description", "type": "text"},
    },
    "account": {
        "industry": {"label": "Industry", "type": "text"},
        "employee_count": {"label": "Employees", "type": "number"},
        "description": {"label": "Description", "type": "text"},
    },
    "contact": {
        "job_title": {"label": "Job title", "type": "text"},
        "phone": {"label": "Phone", "type": "text"},
        "department": {"label": "Department", "type": "text"},
    },
}
MODELS = {"opportunity": Opportunity, "account": Account, "contact": Contact}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Parsing transcripts
# ---------------------------------------------------------------------------


def _seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = [float(p) for p in value.split(":")]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return round(total, 2)


def parse_transcript(text: str, filename: str | None = None) -> list[dict[str, Any]]:
    """Utterances as [{"speaker", "start", "end", "text"}] from VTT, SRT or plain text."""

    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = text.split("\n")
    utterances: list[dict[str, Any]] = []
    is_cued = any(CUE_RE.match(line) for line in lines[:50])
    if is_cued:
        current: dict[str, Any] | None = None
        for line in lines:
            cue = CUE_RE.match(line)
            if cue:
                current = {"speaker": None, "start": _seconds(cue.group(1)), "end": _seconds(cue.group(3)), "text": ""}
                utterances.append(current)
                continue
            if current is None or not line.strip() or line.strip().isdigit() or line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
                continue
            voice = VOICE_RE.search(line)
            if voice:
                current["speaker"] = voice.group(1).strip()
                content = re.sub(r"<[^>]+>", "", voice.group(2)).strip()
            else:
                match = SPEAKER_RE.match(line)
                if match and current["speaker"] is None and not current["text"]:
                    current["speaker"] = match.group("speaker").strip()
                    content = match.group("text").strip()
                else:
                    content = re.sub(r"<[^>]+>", "", line).strip()
            current["text"] = f"{current['text']} {content}".strip()
        utterances = [u for u in utterances if u["text"]]
        # Merge consecutive cues from the same speaker.
        merged: list[dict[str, Any]] = []
        for item in utterances:
            item["speaker"] = item["speaker"] or "Speaker"
            if merged and merged[-1]["speaker"] == item["speaker"] and item["start"] - merged[-1]["end"] < 2:
                merged[-1]["text"] = f"{merged[-1]['text']} {item['text']}"
                merged[-1]["end"] = item["end"]
            else:
                merged.append(item)
        return merged
    for line in lines:
        if not line.strip():
            continue
        match = SPEAKER_RE.match(line)
        if match:
            ts = match.group("ts") or match.group("ts2")
            utterances.append({"speaker": match.group("speaker").strip(), "start": _seconds(ts) if ts else None, "end": None, "text": match.group("text").strip()})
        elif utterances:
            utterances[-1]["text"] = f"{utterances[-1]['text']} {line.strip()}"
        else:
            utterances.append({"speaker": "Speaker", "start": None, "end": None, "text": line.strip()})
    for index, item in enumerate(utterances):
        if item["start"] is not None and item["end"] is None:
            nxt = next((u["start"] for u in utterances[index + 1:] if u["start"] is not None), None)
            item["end"] = nxt
    return utterances


def redact(utterances: list[dict[str, Any]]) -> int:
    count = 0
    for item in utterances:
        text, n1 = CARD_RE.subn("[redacted number]", item["text"])
        text, n2 = SSN_RE.subn("[redacted number]", text)
        item["text"] = text
        count += n1 + n2
    return count


def talk_time(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    timed = all(u.get("start") is not None and u.get("end") is not None for u in utterances) and utterances
    totals: dict[str, float] = {}
    for item in utterances:
        amount = max(0.0, (item["end"] or 0) - (item["start"] or 0)) if timed else float(len(item["text"].split()))
        totals[item["speaker"]] = totals.get(item["speaker"], 0) + amount
    whole = sum(totals.values()) or 1
    return {
        "unit": "seconds" if timed else "words",
        "speakers": sorted(({"speaker": k, "amount": round(v, 1), "percent": round(v / whole * 100, 1)} for k, v in totals.items()), key=lambda r: -r["amount"]),
        "longest_monologue": round(max(((u["end"] or 0) - (u["start"] or 0) for u in utterances), default=0), 1) if timed else None,
        "questions": sum(u["text"].count("?") for u in utterances),
    }


def duration_of(utterances: list[dict[str, Any]]) -> int:
    ends = [u["end"] for u in utterances if u.get("end") is not None]
    return int(max(ends)) if ends else 0


def full_text(utterances: list[dict[str, Any]]) -> str:
    return "\n".join(f"{u['speaker']}: {u['text']}" for u in utterances)


def tracker_hits(utterances: list[dict[str, Any]], trackers: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tracker in trackers:
        hits = []
        for index, item in enumerate(utterances):
            lowered = item["text"].lower()
            found: list[str] = []
            for keyword in tracker.keywords or []:
                keyword = keyword.strip().lower()
                match = re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", lowered) if keyword else None
                if match:
                    # Keep the speaker's own spelling ("HubSpot"), not the lowercased keyword.
                    found.append(item["text"][match.start():match.end()])
            if found:
                hits.append({"index": index, "speaker": item["speaker"], "keyword": found[0].lower(), "keywords": found, "start": item.get("start"), "text": item["text"][:300]})
        if hits:
            out[str(tracker.id)] = {"name": tracker.name, "category": tracker.category, "color": tracker.color, "count": len(hits), "hits": hits[:50]}
    return out


def rules_summary(utterances: list[dict[str, Any]], hits: dict[str, Any]) -> tuple[str, list[str]]:
    speakers = [s["speaker"] for s in talk_time(utterances)["speakers"]]
    topics = [h["name"] for h in sorted(hits.values(), key=lambda h: -h["count"])][:4]
    actions = []
    for item in utterances:
        for sentence in re.split(r"(?<=[.!?])\s+", item["text"]):
            if ACTION_RE.search(sentence) and len(sentence.split()) >= 4:
                actions.append(f"{item['speaker']}: {sentence.strip()}")
    questions = [u for u in utterances if "?" in u["text"]]
    parts = [f"Conversation between {', '.join(speakers[:4])}{' and others' if len(speakers) > 4 else ''}."]
    if topics:
        parts.append(f"Topics that came up: {', '.join(topics)}.")
    if questions:
        parts.append(f"{len(questions)} question{'s were' if len(questions) != 1 else ' was'} asked.")
    if actions:
        parts.append(f"{len(actions)} commitment{'s or next steps were' if len(actions) != 1 else ' or next step was'} mentioned.")
    return " ".join(parts), actions[:10]


async def ai_summary(db: AsyncSession, organization_id: uuid.UUID, transcript: str) -> tuple[str, list[str]] | None:
    if not ai.configured():
        return None
    text = await ai.complete(
        "Summarize this sales or customer conversation for the CRM. Reply with JSON only: "
        '{"summary": "3-5 sentences: who, what was discussed, concerns, outcome", "action_items": ["owner: action", ...]}.\n\n'
        f"Transcript:\n{transcript[:14000]}",
        system=await ai_context.system_prompt(db, organization_id, "conversations"),
        max_tokens=700,
    )
    if not text:
        return None
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        return str(data.get("summary") or "").strip(), [str(a) for a in data.get("action_items") or []][:12]
    except (ValueError, json.JSONDecodeError):
        return text.strip(), []


def _extract_value(field_type: str, text: str) -> Any:
    if field_type == "money":
        match = MONEY_RE.search(text)
        if match:
            amount, unit = match.group(1), match.group(2)
        else:
            spoken = SPOKEN_MONEY_RE.search(text)
            if not spoken:
                return None
            amount, unit = (spoken.group(1), spoken.group(2)) if spoken.group(1) else (spoken.group(3), spoken.group(4))
        number = Decimal(amount.replace(",", ""))
        unit = (unit or "").lower()
        if unit in ("k", "thousand"):
            number *= 1000
        if unit in ("m", "million"):
            number *= 1_000_000
        return str(number.quantize(Decimal("1")))
    if field_type == "number":
        match = re.search(r"\b(\d{1,3}(?:,\d{3})+|\d+)\b", text)
        return match.group(1).replace(",", "") if match else None
    if field_type == "date":
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        return match.group(1) if match else None
    return text.strip()[:500]


async def answer_prompts(db: AsyncSession, conversation: Conversation, utterances: list[dict[str, Any]], prompts: list[FieldPrompt]) -> list[dict[str, Any]]:
    suggestions = []
    transcript = full_text(utterances)
    for prompt in prompts:
        record_id = {"opportunity": conversation.opportunity_id, "account": conversation.account_id, "contact": conversation.contact_id}.get(prompt.object_type)
        if record_id is None:
            continue
        spec = field_spec(prompt.object_type, prompt.field_key)
        value, evidence, source = None, None, "rules"
        if ai.configured():
            answer = await ai.complete(
                f"From this conversation, answer: {prompt.prompt}\nReply with JSON only: "
                '{"value": "the answer, or null if it wasn\'t discussed", "evidence": "the line it came from"}.'
                f" The answer is for the CRM field “{spec['label']}” ({spec['type']}).\n\nTranscript:\n{transcript[:12000]}",
                system=await ai_context.system_prompt(db, conversation.organization_id, "conversations"),
                max_tokens=250,
            )
            if answer:
                try:
                    data = json.loads(answer[answer.index("{"): answer.rindex("}") + 1])
                    raw = data.get("value")
                    if raw not in (None, "", "null"):
                        value = _extract_value(spec["type"], str(raw)) if spec["type"] in ("money", "number", "date") else str(raw)[:500]
                        evidence, source = str(data.get("evidence") or "")[:300], "claude"
                        metering.record_ai_words(db, conversation.organization_id, conversation.owner_user_id, "field_prompts", str(raw), "claude")
                except (ValueError, json.JSONDecodeError):
                    pass
        if value is None and prompt.field_key == "competitor":
            # Competitors: name the ones the competition trackers heard, not the whole sentence.
            names = competitors_heard(conversation)
            if names:
                value, evidence = ", ".join(names), "Mentioned in the call (competitor trackers)."
        if value is None:
            keywords = [k.lower() for k in prompt.keywords or []] or [w for w in re.findall(r"[a-z]{5,}", prompt.prompt.lower())][:4]
            for item in utterances:
                lowered = item["text"].lower()
                matched = next((k for k in keywords if k in lowered), None)
                if matched:
                    text = item["text"]
                    if spec["type"] == "text":
                        # Keep only the sentence that answers the question.
                        sentences = re.split(r"(?<=[.!?])\s+", text)
                        text = next((sentence for sentence in sentences if matched in sentence.lower()), text)
                    candidate = _extract_value(spec["type"], text)
                    if candidate:
                        value, evidence = candidate, f"{item['speaker']}: {item['text'][:280]}"
                        break
        if value is None:
            continue
        suggestions.append(
            {
                "id": uuid.uuid4().hex[:12], "prompt_id": str(prompt.id), "name": prompt.name, "object_type": prompt.object_type, "record_id": str(record_id),
                "field_key": prompt.field_key, "field_label": spec["label"], "value": value, "evidence": evidence, "source": source,
                "status": "pending", "apply_mode": prompt.apply_mode,
            }
        )
    return suggestions


def competitors_heard(conversation: Conversation) -> list[str]:
    names: list[str] = []
    for key, hit in (conversation.tracker_hits or {}).items():
        if key.startswith("_") or not isinstance(hit, dict) or (hit.get("category") or "").lower() not in {"competition", "competitor", "competitors"}:
            continue
        for item in hit.get("hits") or []:
            for keyword in item.get("keywords") or [item.get("keyword")]:
                label = str(keyword or "").strip()
                label = label.title() if label.islower() else label
                if label and label.lower() not in {n.lower() for n in names}:
                    names.append(label)
    return names


def field_spec(object_type: str, field_key: str) -> dict[str, str]:
    if field_key.startswith("cf."):
        return {"label": field_key[3:].replace("_", " ").title(), "type": "text"}
    return FIELD_TARGETS.get(object_type, {}).get(field_key, {"label": field_key, "type": "text"})


async def apply_suggestion(db: AsyncSession, organization_id: uuid.UUID, suggestion: dict[str, Any]) -> None:
    model = MODELS.get(suggestion["object_type"])
    record = await db.get(model, uuid.UUID(suggestion["record_id"])) if model else None
    if record is None or record.organization_id != organization_id:
        raise ValueError("The linked record no longer exists.")
    key, value = suggestion["field_key"], suggestion["value"]
    if key.startswith("cf."):
        record.custom_fields = {**(record.custom_fields or {}), key[3:]: value}
        return
    spec = field_spec(suggestion["object_type"], key)
    if spec["type"] == "money":
        try:
            value = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{value} isn't an amount.") from exc
    elif spec["type"] == "number":
        value = int(str(value))
    elif spec["type"] == "date":
        value = date.fromisoformat(str(value))
    if not hasattr(record, key):
        raise ValueError("That field doesn't exist on the record.")
    setattr(record, key, value)


# ---------------------------------------------------------------------------
# Transcription providers
# ---------------------------------------------------------------------------


class TranscriptionError(Exception):
    pass


async def deepgram(audio: bytes, content_type: str, api_key: str, language: str) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                "https://api.deepgram.com/v1/listen",
                params={"smart_format": "true", "diarize": "true", "punctuate": "true", "utterances": "true", "language": language or "en"},
                headers={"Authorization": f"Token {api_key}", "Content-Type": content_type or "audio/mpeg"},
                content=audio,
            )
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Deepgram couldn't be reached ({exc.__class__.__name__}).") from exc
    if response.status_code in (401, 403):
        raise TranscriptionError("Deepgram rejected the API key.")
    if response.status_code >= 400:
        raise TranscriptionError(f"Deepgram answered {response.status_code}: {response.text[:200]}")
    data = response.json()
    items = (data.get("results") or {}).get("utterances") or []
    return [{"speaker": f"Speaker {int(u.get('speaker', 0)) + 1}", "start": round(float(u.get("start", 0)), 2), "end": round(float(u.get("end", 0)), 2), "text": u.get("transcript", "")} for u in items]


async def assemblyai_start(audio: bytes, api_key: str, language: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=300, headers={"authorization": api_key}) as client:
            upload = await client.post("https://api.assemblyai.com/v2/upload", content=audio)
            if upload.status_code in (401, 403):
                raise TranscriptionError("AssemblyAI rejected the API key.")
            upload.raise_for_status()
            job = await client.post(
                "https://api.assemblyai.com/v2/transcript",
                json={"audio_url": upload.json()["upload_url"], "speaker_labels": True, "language_code": language or "en"},
            )
            job.raise_for_status()
            return job.json()["id"]
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"AssemblyAI couldn't be reached ({exc.__class__.__name__}).") from exc


async def assemblyai_poll(job_id: str, api_key: str) -> list[dict[str, Any]] | None:
    async with httpx.AsyncClient(timeout=60, headers={"authorization": api_key}) as client:
        response = await client.get(f"https://api.assemblyai.com/v2/transcript/{job_id}")
    data = response.json()
    if data.get("status") == "error":
        raise TranscriptionError(data.get("error") or "AssemblyAI couldn't transcribe the recording.")
    if data.get("status") != "completed":
        return None
    return [
        {"speaker": f"Speaker {u.get('speaker', 'A')}", "start": round(u.get("start", 0) / 1000, 2), "end": round(u.get("end", 0) / 1000, 2), "text": u.get("text", "")}
        for u in data.get("utterances") or []
    ]


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


async def process(db: AsyncSession, conversation: Conversation, utterances: list[dict[str, Any]], *, user_id: uuid.UUID | None) -> Conversation:
    org = conversation.organization_id
    recording = await workspace_settings.get(db, org, "recording")
    if recording.redact_numbers:
        redact(utterances)
    trackers = (await db.execute(select(ConversationTracker).where(ConversationTracker.organization_id == org, ConversationTracker.is_active.is_(True)))).scalars().all()
    prompts = (
        await db.execute(select(FieldPrompt).where(FieldPrompt.organization_id == org, FieldPrompt.is_active.is_(True)).order_by(FieldPrompt.position))
    ).scalars().all()
    conversation.transcript = utterances
    conversation.transcript_text = full_text(utterances)
    conversation.duration_seconds = conversation.duration_seconds or duration_of(utterances)
    conversation.talk_time = talk_time(utterances)
    if not conversation.participants:
        conversation.participants = [{"name": s["speaker"]} for s in conversation.talk_time["speakers"]]
    hits = tracker_hits(utterances, trackers)
    conversation.tracker_hits = hits
    if recording.auto_summarize:
        available = await metering.ai_words_available(db, org, user_id)
        result = await ai_summary(db, org, conversation.transcript_text) if available["remaining"] > 0 else None
        if result:
            conversation.summary, conversation.action_items = result
            conversation.summary_source = "claude"
        else:
            conversation.summary, conversation.action_items = rules_summary(utterances, hits)
            conversation.summary_source = "rules"
        metering.record_ai_words(db, org, user_id, "conversations", f"{conversation.summary} {' '.join(conversation.action_items)}", conversation.summary_source)
    suggestions = await answer_prompts(db, conversation, utterances, list(prompts))
    for suggestion in suggestions:
        if suggestion["apply_mode"] == "auto":
            try:
                await apply_suggestion(db, org, suggestion)
                suggestion["status"] = "applied"
            except (ValueError, InvalidOperation):
                suggestion["status"] = "pending"
    conversation.field_suggestions = suggestions
    if not recording.keep_audio:
        conversation.recording = None
        conversation.recording_size = 0
    conversation.status = "ready"
    conversation.error = None
    return conversation


def classify(participants: list[dict[str, Any]], internal_domains: set[str]) -> str:
    emails = [(p.get("email") or "").lower() for p in participants if p.get("email")]
    if not emails:
        return "external"
    return "internal" if all(e.split("@")[-1] in internal_domains for e in emails) else "external"
